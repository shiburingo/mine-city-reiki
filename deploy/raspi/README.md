# mine-city-reiki 本番反映

- UI: `/var/www/mine-city-reiki/`
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

## Meilisearch

Meilisearch is optional. When `MEILI_ENABLED=1`, the API uses Meilisearch first and falls back to the existing MySQL search if Meilisearch is unavailable or a query is not supported by the Meilisearch path.

Required API env values:

```bash
MEILI_ENABLED=1
MEILI_URL=http://127.0.0.1:7700
MEILI_MASTER_KEY=...
MEILI_INDEX=mine_city_reiki_articles
MEILI_MINUTES_INDEX=mine_city_meeting_minutes
```

After enabling Meilisearch, run a full reindex from the Settings screen or `POST /api/reindex/run` so the law index is rebuilt from MySQL. Run a meeting-minutes compile from Settings after deployment so the separate meeting-minutes index is rebuilt from the active compiled generation.

## Git 管理の前提

- `/opt/mine-city-reiki` は Git clone 正本で運用する
- `git remote -v` で `origin https://github.com/shiburingo/mine-city-reiki.git` が出ることを確認
- `not-git` の場合は `docs/ops.md` の「前提: `/opt/mine-city-reiki` は Git clone 正本にする」の手順で正本化する
