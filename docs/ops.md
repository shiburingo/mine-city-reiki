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
6. ヘルスチェックを再試行し、API応答を確認

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

地方自治法・地方公務員法の構造化パーサを更新した場合は、設定画面から両ソースを同期します。通常同期だけで変更文書のMariaDB転置索引とMeilisearch文書が増分更新され、別表・元XMLアンカーも反映されます。

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

通常同期は、変更文書の旧Meilisearch条文を一括削除して新条文だけを投入し、`law_documents`、`law_articles`、`law_search_terms` のテーブル統計を更新します。検索設定変更や索引破損の修復時だけ「全件再索引」を実行します。

Meilisearchの削除タスクは要求件数と実削除件数を照合し、不一致を警告します。全件再索引では削除失敗を無視せず処理を停止するため、完了後はMeilisearchの `numberOfDocuments` が `law_documents + law_articles` の有効件数と一致することを確認してください。世代違いの旧条文が残った場合も全件再索引で解消します。

### 会議録同期とコンパイル

- 「会議録のみ差分同期」は元WebページのPDF URL・内容ハッシュを比較し、追加・変更日程だけを再抽出します。
- 同期成功後は全会議録を新しい世代へコンパイルし、軽量検索テーブル、日程閲覧JSON、Meilisearch専用索引を作り直します。
- 新世代は全処理成功後だけ有効化します。有効世代と直前の成功世代を残し、失敗時は旧世代で検索・閲覧を継続します。
- 再タグ付けはPDFを再取得せず、年度別話者辞書、会議日名簿、状態機械、最新ルールを全発言へ再適用します。

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

会議録テーブルと互換カラム・索引はAPI起動時にも冪等に確認されます。本番でDDLを確認する場合は先にバックアップを取得し、状態変更を伴う手動SQLではなくアプリの初期化処理またはスキーマファイルを使用します。

### 検索統計の確認

通常同期と全件再索引は検索用テーブルへ `ANALYZE TABLE` を実行します。手動確認する場合は読み取り専用で次を使います。

```sql
SHOW INDEX FROM law_search_terms;
EXPLAIN SELECT document_id, article_id
FROM law_search_terms
WHERE target_type = 'article' AND term = '観光';
```

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
- 同期・再索引・同義語変更時は `cache_generation` を更新して検索・質問キャッシュを即時失効する。古い結果が見える場合はAPIワーカーとMeilisearchの状態を確認し、管理画面のキャッシュクリアを診断用に使用する。
