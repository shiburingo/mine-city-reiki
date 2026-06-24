# mine-city-reiki 本番反映

- UI: `/var/www/mine-city-reiki/`
- API: `mine-city-reiki-api.service`
- API env: `/etc/mine-city-reiki-api.env`
- nginx snippet: `/etc/nginx/snippets/mine-city-reiki.conf`
- timer: `mine-city-reiki-sync.timer`

## 更新
本番反映は GitHub 経由に統一します。Mac側で commit / push した後、Raspberry Pi 側で次を実行します。

```bash
cd /opt/mine-city-reiki
./deploy/raspi/update.sh
```

## Git 管理の前提

- `/opt/mine-city-reiki` は Git clone 正本で運用する
- `git remote -v` で `origin https://github.com/shiburingo/mine-city-reiki.git` が出ることを確認
- `not-git` の場合は `docs/ops.md` の「前提: `/opt/mine-city-reiki` は Git clone 正本にする」の手順で正本化する
