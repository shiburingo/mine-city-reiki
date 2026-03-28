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

1. `git pull` で最新コードを取得
2. `npm ci && npm run build` でフロントエンドをビルド
3. `dist/` を `/var/www/mine-city-reiki/` にコピー
4. `pip install -r server/requirements.txt` でバックエンド依存を更新
5. `systemctl restart mine-city-reiki-api` で API を再起動

---

## 初回セットアップ

### DB スキーマ適用

```bash
mysql -u root mine_city_reiki < server/schema.mariadb.sql
```

スキーマは `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` で冪等に書かれているため、再実行しても安全です。

### 初回データ取り込み

API 起動後、UI の「同期設定」タブ → 「すべて同期」ボタンを押すか、CLI から:

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

---

## 手動同期

UI の「同期設定」タブ → 「手動同期」パネルから実行します:

- **美祢市例規のみ** — 美祢市公式サイトのみを対象にクロール
- **地方自治法のみ** — e-Gov API のみを対象にクロール
- **すべて同期** — 両ソースを順番にクロール

---

## キャッシュ管理

UI の「同期設定」タブ → 「キャッシュ管理」パネルから操作できます:

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
```

---

## ログ確認

```bash
# API ログ
journalctl -u mine-city-reiki-api -n 100 -f

# 同期ログ
journalctl -u mine-city-reiki-sync -n 50
```

---

## 注意事項

- 例規の解釈は必ず原文を確認すること。このシステムの回答は候補提示のみであり、法的判断を断定しない。
- 個人情報を含む質問をシステムに入力しないこと。
- キャッシュが残っている場合、同期直後でも古い検索結果が返ることがある（最大 30 分）。手動クリアで即時反映できる。
