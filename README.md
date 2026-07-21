# mine-city-reiki

美祢市（山口県）の例規集・地方自治法・地方公務員法と美祢市議会会議録をデータベース化し、全文検索・構造化閲覧・条文照会・発言集作成を提供する自治体向け情報検索システムです。

---

## 機能一覧

### 検索
- **多フィールド AND / OR 検索** — 検索窓を最大 4 つ設け、フィールド間で AND / OR を切り替えられます。各窓内のスペース区切りは AND 検索です。
- **転置インデックス検索** — Janome 形態素解析で生成した `law_search_terms` を使い高速検索します。
- **同義語展開** — `law_synonyms` テーブルの辞書で検索語を自動拡張します（例: `例規 → 条例 / 規則 / 要綱`）。
- **累積シソーラス** — Wikipedia・Wiktionary・Wikidata・WordNet・例規・会議録から出典付きで関連語を蓄積し、50万語を第1目標として日次更新します。検索時は100万語超を想定したSQLite索引と語単位LRUキャッシュを使用します。
- **詳細絞り込みフィルター** — 法令種別・公布日（開始〜終了）で検索結果を絞り込めます。
- **マッチ根拠バッジ** — 各検索結果にどの項目でヒットしたか（タイトル / 条番号 / 条名 / 条文 / 本文）をバッジ表示します。
- **オートコンプリート** — 過去の検索語をサジェストします。
- **ページネーション** — 20 件単位でページ送りできます。
- **Meilisearch 連携** — 例規・法令と会議録を別インデックスで検索し、未設定・障害・対象外クエリでは MariaDB の転置索引 / FULLTEXT へ自動フォールバックします。

### 閲覧
- **体系別閲覧** — ソースを `美祢市例規集 / 地方自治法 / 地方公務員法` で切り替え、体系順・番号順で左ペインから閲覧できます。デフォルト表示は `美祢市例規集` です。
- **構造化全文表示** — 目次、編・章・節、条文、附則、別表を左ナビゲーションと本文の2ペインで閲覧できます。美祢市例規のHTML表とe-Gov XMLの別表を表として表示します。
- **本文内リンク** — 条番号、別表、元XMLアンカー、同一文書内参照、地方自治法・地方公務員法への文書間参照を解決します。解決できない参照は誤誘導を避けるためリンク化しません。
- **CSV ダウンロード** — 表示中のソース（美祢市例規集 / 地方自治法 / 地方公務員法）の例規一覧を CSV でエクスポートできます（UTF-8 BOM 付きで Excel 対応）。

### 質問応答
- **条文照会（Q&A）** — 自然文の質問から関連条文候補を提示します（法的判断の断定はしません）。
- **質問テンプレートボタン** — よくある質問を 1 クリックで入力できます。
- **質問履歴** — 直近 5 件の質問を再実行できます。
- **2 層キャッシュ** — メモリ（120 秒）+ DB（検索 30 分 / Q&A 6 時間）で高速化。同期時に自動失効。

### 文書詳細
- **ブックマーク** — 条文をブラウザに保存し、ブックマークタブから再参照できます。
- **関連条文レコメンド** — 現在表示中の例規に関連する条文候補を自動検索して表示します。
- **変更履歴** — 同期のたびに保存されるスナップショット一覧を表示します。
- **差分ビュー** — 履歴エントリの「全文を見る」から全文を表示し、現在版との行差分をハイライト表示します。
- **印刷** — 条文全文を印刷用に整形して出力します。
- **パーマリンク** — URL ハッシュにタブ・文書 ID を反映し、ブラウザバック／ブックマークで状態を復元します。

### 管理・設定
- **手動同期** — ワンクリックで美祢市例規 / 地方自治法 / 地方公務員法 / 全ソースを同期できます。
- **月次更新設定** — 日・時・分・対象ソースを DB に保存し、systemd timer で定期実行します。
- **同期履歴** — 追加 / 更新 / 変更なし / 条文合計件数の詳細サマリを表示します。
- **キャッシュ管理** — 検索キャッシュ・質問キャッシュを個別またはまとめてクリアできます。
- **同義語管理** — 正規語と同義語のペアを追加・削除できます。
- **利用統計（アナリティクス）** — キャッシュヒット率・検索ランキング・質問ランキングをダッシュボードに表示します。
- **会議録管理** — PDF差分同期、全件コンパイル、再タグ付け、会議録由来辞書更新を管理画面から実行できます。
- **辞書更新** — WordNet・Wikipedia・Wiktionary・Wikidata・既存DBから関連語を累積し、検索用SQLite索引へ原子的にコンパイルします。

### 他アプリ連携 API
- 同ホスト上の業務アプリから `/mine-city-reiki-api/api/reference/*` で条文検索・参照が可能です。

---

## 技術スタック

| 層 | 技術 |
|---|---|
| フロントエンド | React 18 + TypeScript + Vite + Tailwind CSS 4 |
| バックエンド | Python 3 / Flask 3 + Gunicorn（ポート 8795） |
| データベース | MariaDB（例規・辞書・キャッシュ・会議録・コンパイル世代を管理） |
| 検索エンジン | Meilisearch + MariaDB 転置索引 / FULLTEXT フォールバック |
| 形態素解析 | Janome 0.5.0 |
| 認証 | 共通認証 `mine-trout-cash-api`（ポート 8787, `/api/auth/*`）に委譲 |
| 共有 UI | mine-troutfarm-ui（ローカル npm パッケージ） |
| デプロイ | Raspberry Pi + Nginx リバースプロキシ |

---

## ディレクトリ構成

```
mine-city-reiki/
├── src/app/
│   ├── App.tsx          # メイン UI（タブ・検索・閲覧・Q&A・設定）
│   ├── api.ts           # REST API クライアント
│   ├── authApi.ts       # 認証 API クライアント
│   ├── types.ts         # 型定義
│   └── ArticleContent.tsx  # 条文レンダラー（HTML テーブル対応）
├── server/
│   ├── app.py           # Flask アプリ（API 全エンドポイント）
│   ├── dictionary_engine.py # 関連語の取得・品質評価・コンパイル
│   ├── meeting_minutes/ # PDF抽出・話者タグ付け・表整形
│   ├── schema.mariadb.sql  # DB スキーマ（冪等 CREATE / ALTER）
│   ├── run_due_sync.py  # 月次同期 CLI（systemd timer から呼び出し）
│   └── wsgi.py          # Gunicorn エントリポイント
├── mine-troutfarm-ui/   # 共有 UI ライブラリ（PortalHeader 等）
├── deploy/
│   ├── raspi/           # Raspberry Pi 用デプロイスクリプト・設定
│   └── ...              # systemd unit / nginx snippet
└── docs/
    ├── spec.md          # 技術仕様
    └── ops.md           # 運用手順
```

---

## 開発環境セットアップ

### フロントエンド

```bash
npm install
npm run dev          # Vite 開発サーバー（ポート 5173）
```

### バックエンド

```bash
cd server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py       # 開発サーバー（ポート 8795）
```

または npm から:

```bash
npm run dev:api
```

### ビルド確認

```bash
npm run build
python3 -m py_compile server/app.py
PYTHONPATH=server server/venv/bin/python -m unittest discover -s server/tests -p 'test_*.py'
```

---

## API エンドポイント一覧

### ヘルス・情報

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/health` | ヘルスチェック |
| GET | `/api/openapi` | OpenAPI スキーマ（JSON） |

### 検索

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/search` | 条文検索（`q1`〜`q4`, `op2`〜`op4`, `source`, `limit`, `offset`, `lawType`, `fromDate`, `toDate`） |
| GET | `/api/law-types` | 法令種別一覧 |

### 文書・条文

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/documents` | 文書一覧（`source`, `format=csv` 対応） |
| GET | `/api/documents/:id` | 文書詳細（条文付き） |
| GET | `/api/documents/:id/history` | 変更履歴一覧 |
| GET | `/api/documents/:id/history/:historyId` | 変更履歴詳細（全文付き） |

### 質問応答

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/api/ask` | 自然文から関連条文候補を提示 |

### 同期・設定

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/sync/status` | 同期状態・統計 |
| GET | `/api/sync/runs` | 同期実行履歴 |
| PUT | `/api/sync/settings` | 月次更新設定の保存 |
| POST | `/api/sync/run` | 手動同期実行（`sourceScope`: `all` / `mine-city` / `egov` / `local-public-service`） |
| POST | `/api/reindex/run` | 例規・法令の全件再索引 |

### 会議録

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/minutes/status` | 会議録件数、同期・コンパイル状態 |
| POST | `/api/minutes/sync` | 追加・変更PDFだけを差分同期 |
| POST | `/api/minutes/compile` | 全件コンパイルと専用Meilisearch索引の世代切替 |
| POST | `/api/minutes/retag` | 最新辞書・ルールによる全発言の再タグ付け |
| GET | `/api/minutes/search` | 本文・発言者・会議・期間・ロール検索（カーソル対応） |
| GET | `/api/minutes/meetings` | 会議一覧 |
| GET | `/api/minutes/meetings/:id` | 会議詳細・日程一覧 |
| GET | `/api/minutes/days/:id` | 日程単位の会議録、発言、表 |

### キャッシュ・同義語・アナリティクス

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/api/cache/clear` | キャッシュクリア（`scope`: `search` / `ask` / `all`） |
| GET | `/api/synonyms` | 同義語一覧 |
| POST | `/api/synonyms` | 同義語追加 |
| DELETE | `/api/synonyms/:id` | 同義語削除 |
| GET | `/api/analytics` | 利用統計 |
| POST | `/api/dictionary/update` | WordNet・既存DBから辞書再作成 |
| POST | `/api/dictionary/internet/update` | 公開辞書ソースの増分取得 |
| POST | `/api/dictionary/minutes/update` | 未抽出会議録から辞書候補を追加 |
| POST | `/api/dictionary/compile` | 検索用SQLite辞書を再コンパイル |

### 他アプリ参照用（クロスアプリ）

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/reference/search` | 条文候補検索 |
| GET | `/api/reference/document/:id` | 文書・条文詳細取得 |

---

## データソース

- **美祢市例規集**: https://www2.city.mine.lg.jp/section/reiki/reiki_taikei/r_taikei_05.html
- **地方自治法（e-Gov API）**: https://laws.e-gov.go.jp/law/322AC0000000067
- **地方公務員法（e-Gov API）**: https://laws.e-gov.go.jp/law/325AC0000000261
- **日本語Wikipedia / Wiktionary**: MediaWiki APIのリダイレクト（CC BY-SA 4.0）
- **Wikidata**: 日本語ラベル・別名（CC0 1.0）
- **日本語WordNet**: WN-JA公開データ

---

## データベーステーブル

| テーブル | 用途 |
|---|---|
| `law_documents` | 文書単位（タイトル・法令種別・全文等） |
| `law_articles` | 条文単位（条番号・条名・本文） |
| `law_search_terms` | 転置インデックス（term → document/article） |
| `law_synonyms` | 同義語辞書 |
| `dictionary_sources` | 辞書ソース、ライセンス、巡回カーソル、実行状態 |
| `dictionary_pair_evidence` | 関連語ペアごとの出典、URL、信頼度、確認履歴 |
| `law_document_history` | 文書の変更履歴スナップショット（全文含む） |
| `sync_settings` | 月次更新設定・最終同期情報 |
| `sync_runs` | 同期実行ログ |
| `search_query_cache` | 検索結果キャッシュ |
| `ask_query_cache` | 質問応答キャッシュ |
| `meeting_sessions` / `meeting_days` | 会議と開催日、PDF差分状態 |
| `meeting_speakers` / `meeting_utterances` | 年・会議日を考慮した話者辞書と発言本文・分類 |
| `meeting_tables` | PDF表の行列JSON、HTML、検索テキスト |
| `meeting_compile_versions` | 会議録コンパイル世代と有効世代 |
| `meeting_compiled_utterances` / `meeting_compiled_days` | 検索・閲覧用の非正規化済み軽量データ |

スキーマは `server/schema.mariadb.sql` に冪等な `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` で記述されており、再実行しても安全です。

`law_search_terms` は検索速度と容量のバランスを取るため、次の主要索引を持ちます。

- `uq_law_search_terms_target_term (target_type, target_id, term)`
- `idx_law_search_terms_term_target (term, target_type)`
- `idx_law_search_terms_target_term_doc_article (target_type, term, document_id, article_id)`
- `idx_law_search_terms_document (document_id)`
- `idx_law_search_terms_article (article_id)`

`(target_type, term, document_id)` 単独索引は、上記の複合索引で左辺一致として利用できるため作成しません。
