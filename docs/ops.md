# 運用手順

## 配備構成

| 種別 | パス / 名前 |
|---|---|
| 静的 UI | `/var/www/mine-city-reiki/` |
| API サービス | `mine-city-reiki-api.service`（Gunicorn、ポート 8795） |
| API 環境変数 | `/etc/mine-city-reiki-api.env` |
| 同期サービス | `mine-city-reiki-sync.service` |
| 同期タイマー | `mine-city-reiki-sync.timer`（毎時チェック） |
| nginx スニペット | `/etc/nginx/snippets/mine-city-reiki.conf` |
| データベース | MariaDB `mine_city_reiki` |

---

## デプロイ（更新）

```bash
cd /opt/mine-city-reiki
./deploy/raspi/update.sh
```

このスクリプトは以下を行います:

1. `git pull --ff-only` で最新コードを取得
2. `npm ci && npm run build` でフロントエンドをビルド
3. `dist/` を `/var/www/mine-city-reiki/` にコピー
4. `pip install -r server/requirements.txt` でバックエンド依存を更新
5. `systemctl restart mine-city-reiki-api` で API を再起動

本番反映は GitHub 経由に統一します。Mac側で commit / push した後、Raspberry Pi 側の Git checkout で `deploy/raspi/update.sh` を実行します。

### 前提: `/opt/mine-city-reiki` は Git clone 正本にする

`update.sh` は `git pull --ff-only` を前提にしているため、`/opt/mine-city-reiki` は
必ず Git 管理下（origin 設定済み）で運用します。

確認:

```bash
cd /opt/mine-city-reiki
git rev-parse --is-inside-work-tree
git remote -v
```

`not-git` や remote 未設定の場合は、以下で正本化します（ラズパイ上で実行）。

```bash
set -euo pipefail
TS="$(date +%Y%m%d-%H%M%S)"
OLD="/opt/mine-city-reiki"
BAK="/opt/mine-city-reiki.pre-git-${TS}"

sudo mv "${OLD}" "${BAK}"
sudo git clone --branch main https://github.com/shiburingo/mine-city-reiki.git "${OLD}"

# 既存の Python 仮想環境を引き継ぐ（存在する場合）
if [ -d "${BAK}/server/venv" ]; then
  sudo rsync -a "${BAK}/server/venv/" "${OLD}/server/venv/"
fi

sudo chown -R "$(id -un)":"$(id -gn)" "${OLD}"
cd "${OLD}"
./deploy/raspi/update.sh
```

反映確認:

```bash
curl -fsS http://127.0.0.1:8795/api/health
curl -ksS https://youson-btwhjbrrqvjc.dynamic-m.com/mine-city-reiki-api/api/health
```

---

## 初回セットアップ

### DB スキーマ適用

```bash
mysql -u root mine_city_reiki < server/schema.mariadb.sql
```

スキーマは `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` で冪等に書かれているため、再実行しても安全です。

`law_search_terms` は容量が大きくなりやすいため、`idx_law_search_terms_target_term_doc_article` でカバーできる短い重複索引は作成しません。既存本番DBの重複索引削除は行データを変更しない索引DDLとして実施済みです。

### 初回データ取り込み

API 起動後、UI の「設定」タブ → 「すべて同期」ボタンを押すか、CLI から:

```bash
cd /opt/mine-city-reiki
source server/venv/bin/activate
python3 server/run_due_sync.py --force
```

---

## 定期同期

`mine-city-reiki-sync.timer` が 1 時間ごとに `run_due_sync.py` を呼び出します。
同スクリプトは DB の `sync_settings` を参照し、設定した「月次実行日時」を過ぎていればクロールを実行します。
実行結果は `sync_runs` テーブルに記録されます。

### 関連語辞書の日次成長

`mine-city-reiki-dictionary.timer` は毎日04:00〜05:00に次を直列実行します。

1. 新規会議録発言から関連語候補を増分作成
2. 日本語Wikipediaリダイレクトを前回カーソルから巡回
3. 日本語Wiktionaryリダイレクトを前回カーソルから巡回
4. 現在の辞書から未調査語を選びWikidata日本語別名で補強
5. 検索用辞書を再コンパイル

取得結果は累積され、通信失敗や取得件数の変動で既存辞書は削除されません。管理画面には50万語・100万語目標の進捗、ソース別巡回件数、新規発見数、ライセンス、最終成功・エラーを表示します。

コンパイル結果の正本は `server/data/compiled_synonyms.sqlite3` です。作成中は一時ファイルへ書き込み、完了時に原子的に差し替えるため、API検索を止めずに更新できます。10万語以下では `server/data/compiled_synonyms.json` も互換用に生成します。

```bash
ls -lh server/data/compiled_synonyms.sqlite3
sqlite3 server/data/compiled_synonyms.sqlite3 \
  "SELECT key, value FROM metadata WHERE key IN ('termCount','edgeCount','compiledAt');"
```

既定の日次予算はWikipedia 5,000件、Wiktionary 2,000件、Wikidata 25語です。API負荷や実行時間に応じて `/etc/mine-city-reiki-api.env` で調整します。

---

## 手動同期

UI の「設定」タブ → 「手動同期」パネルから実行します:

- **美祢市例規のみ** — 美祢市公式サイトのみを対象にクロール
- **地方自治法のみ** — e-Gov API のみを対象にクロール
- **地方公務員法のみ** — e-Gov API のみを対象にクロール
- **すべて同期** — 3ソースを順番にクロール

---

## キャッシュ管理

UI の「設定」タブ → 「キャッシュ管理」パネルから操作できます:

| ボタン | 効果 |
|---|---|
| 検索キャッシュをクリア | `search_query_cache` を全削除し `cache_generation` を +1 |
| 質問キャッシュをクリア | `ask_query_cache` を全削除し `cache_generation` を +1 |
| すべてクリア | 両テーブルを全削除し `cache_generation` を +1 |

同義語を追加・削除した場合は自動でキャッシュが無効化されます（`cache_generation` がインクリメントされるため）。

---

## スキーマ変更時の対応

`server/schema.mariadb.sql` に `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` を追記してから再実行します:

```bash
mysql -u root mine_city_reiki < server/schema.mariadb.sql
```

既存データへの影響なしに新しいカラムが追加されます。

---

## API 環境変数

`/etc/mine-city-reiki-api.env` に以下を設定します:

```
DB_HOST=localhost
DB_PORT=3306
DB_NAME=mine_city_reiki
DB_USER=...
DB_PASSWORD=...
SECRET_KEY=...
AUTH_VERIFY_URL=http://localhost:8787/api/auth/verify
CORS_ORIGINS=https://your-domain.example.com
REIKI_DAILY_DICTIONARY_MEDIAWIKI=1
REIKI_DAILY_DICTIONARY_WIKIPEDIA_LIMIT=5000
REIKI_DAILY_DICTIONARY_WIKTIONARY_LIMIT=2000
REIKI_DAILY_DICTIONARY_WIKIDATA=1
REIKI_DAILY_DICTIONARY_WIKIDATA_TERMS=25
REIKI_DICTIONARY_USER_AGENT=mine-city-reiki-thesaurus-bot/0.1 (https://github.com/shiburingo/mine-city-reiki)
REIKI_SYNONYM_JSON_COMPAT_MAX_TERMS=100000
```

---

## ログ確認

```bash
# API ログ
journalctl -u mine-city-reiki-api -n 100 -f

# 同期ログ
journalctl -u mine-city-reiki-sync -n 50

# 関連語辞書の日次ログ・次回実行
journalctl -u mine-city-reiki-dictionary -n 100
systemctl list-timers mine-city-reiki-dictionary.timer --all
```

---

## 注意事項

- 例規の解釈は必ず原文を確認すること。このシステムの回答は候補提示のみであり、法的判断を断定しない。
- 個人情報を含む質問をシステムに入力しないこと。
- キャッシュが残っている場合、同期直後でも古い検索結果が返ることがある（最大 30 分）。手動クリアで即時反映できる。
