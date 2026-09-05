# mine-city-reiki 本番反映

- UI: `/var/www/mine-city-reiki/`
- 管理 UI URL: `/mine-city-reiki/`
- 公開会議録 UI URL: `/mine-city-minutes/`
- API: `mine-city-reiki-api.service`
- API env: `/etc/mine-city-reiki-api.env`
- nginx snippet: `/etc/nginx/snippets/mine-city-reiki.conf`
- timer: `mine-city-reiki-sync.timer`
- optional search engine: `meilisearch.service` on `127.0.0.1:7700`

## 更新
本番反映は GitHub 経由に統一します。Mac側で commit / push した後、Raspberry Pi 側で次を実行します。

```bash
cd /opt/mine-city-reiki
./deploy/raspi/update.sh
```

更新スクリプトは配備ユーザーで実行し、`npm` を `sudo` で直接実行しません。過去の手動更新で `node_modules` または `dist` に root 所有ファイルが残っている場合は、依存関係の導入とビルドの前に所有権を配備ユーザーへ修復します。

API再起動前にSQLite会議録索引を確認し、未作成・破損・旧形式なら有効世代から生成します。作成に失敗した場合は新しいAPIを起動しません。詳細と手動再作成方法は `docs/ops.md` の「2026-09-05 検索索引の移行」を参照してください。

`deploy/nginx/snippets/mine-city-reiki.conf.example` を変更したリリースでは、`update.sh` の後に本番スニペットへ反映し、`sudo nginx -t` が成功してから nginx をreloadします。公開会議録ページは同じ `dist/` に含まれますが、URLは管理UIと分離した `/mine-city-minutes/` です。

## Meilisearch

Meilisearch is optional for law search. When `MEILI_ENABLED=1`, law search uses Meilisearch first and falls back to MySQL. Minutes search uses its separate read-only SQLite FTS5 snapshot instead.

Required API env values:

```bash
MEILI_ENABLED=1
MEILI_URL=http://127.0.0.1:7700
MEILI_MASTER_KEY=...
MEILI_INDEX=mine_city_reiki_articles
MEILI_MINUTES_INDEX=mine_city_meeting_minutes
```

After enabling Meilisearch for the first time, run a full reindex from the Settings screen or `POST /api/reindex/run` so the law index is rebuilt from MySQL. After that, normal law syncs update only changed documents and delete obsolete article records in batches. The legacy `MEILI_MINUTES_INDEX` is retained only for rollback; this release does not delete that index.

The meeting-minutes compiler activates a generation only after lightweight rows, day payloads, and its SQLite snapshot pass validation. Keep the active and immediately previous successful generations; failed builds must not replace the active generation. Source DB records and the synonym dictionary are unchanged by rebuilding this accelerator.

## Git 管理の前提

- `/opt/mine-city-reiki` は Git clone 正本で運用する
- `git remote -v` で `origin https://github.com/shiburingo/mine-city-reiki.git` が出ることを確認
- `not-git` の場合は `docs/ops.md` の「前提: `/opt/mine-city-reiki` は Git clone 正本にする」の手順で正本化する
