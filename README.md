# mine-city-reiki

地方自治法と美祢市例規をデータベース化し、検索・条文参照・簡易質問応答を行うシステムです。

## 機能
- 美祢市例規の公開ページからの取り込み
- e-Gov API からの地方自治法取り込み
- 条文単位検索
- 簡易質問応答（候補条文提示型）
- 月次更新設定
- 他アプリ向け参照 API

## 開発
```bash
npm install
npm run dev
```

API:
```bash
cd server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

## API
- `GET /mine-city-reiki-api/api/health`
- `GET /mine-city-reiki-api/api/search?q=...`
- `GET /mine-city-reiki-api/api/documents/:id`
- `POST /mine-city-reiki-api/api/ask`
- `GET /mine-city-reiki-api/api/reference/search?q=...`
- `GET /mine-city-reiki-api/api/reference/document/:id`

## ソース
- 美祢市例規: https://www2.city.mine.lg.jp/section/reiki/reiki_taikei/r_taikei_05.html
- 地方自治法: https://laws.e-gov.go.jp/law/322AC0000000067
