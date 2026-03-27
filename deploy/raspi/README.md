# mine-city-reiki 本番反映

- UI: `/var/www/mine-city-reiki/`
- API: `mine-city-reiki-api.service`
- API env: `/etc/mine-city-reiki-api.env`
- nginx snippet: `/etc/nginx/snippets/mine-city-reiki.conf`
- timer: `mine-city-reiki-sync.timer`

## 更新
```bash
cd /opt/mine-city-reiki
./deploy/raspi/update.sh
```
