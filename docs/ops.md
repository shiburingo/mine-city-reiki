# 運用

## 手動同期
- UI の同期設定タブから実行

## 定期同期
- `mine-city-reiki-sync.timer` が 1 時間ごとに `run_due_sync.py` を実行
- 実行可否は DB の `sync_settings` で判定

## 注意
- 例規解釈は必ず原文確認前提とする
- Q&Aは候補表示のみで断定回答しない

## 配備構成
- 静的UI: `/var/www/mine-city-reiki/`
- API env: `/etc/mine-city-reiki-api.env`
- API service: `mine-city-reiki-api.service`
- 同期 service: `mine-city-reiki-sync.service`
- 同期 timer: `mine-city-reiki-sync.timer`
- DB: `mine_city_reiki`
- nginx snippet: `mine-city-reiki.conf`

## 初回投入
- API 配備後に同期設定画面または CLI から美祢市例規 / 地方自治法の初回同期を実行します。
- 月次更新は `sync_settings` の時刻定義に従い、timer 起動時に due 判定します。
