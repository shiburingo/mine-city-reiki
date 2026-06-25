# 技術仕様

## 目的

- 美祢市例規集・地方自治法・地方公務員法を横断検索・条文単位で参照できるようにする。
- 美祢市議会の会議録を発言単位・表単位で検索、閲覧できるようにする。
- 同ホスト上の業務アプリから REST API 経由で条文検索・参照ができるようにする。
- 自然文質問に対して関連条文候補を提示する（法的判断の断定はしない）。

---

## データベーステーブル構成

| テーブル | 役割 |
|---|---|
| `law_documents` | 文書単位。`source`（mine-city / egov / local-public-service）、タイトル、法令種別、全文、コンテンツハッシュ等を保持。 |
| `law_articles` | 条文単位。`document_id` FK、条番号・条名・親パス・本文・検索用テキスト。`sort_key` で順序管理。 |
| `law_search_terms` | 転置インデックス。`(target_type, target_id, term, weight)` の形で文書・条文それぞれに term を紐付ける。`weight` は重み（タイトル > 条番号 > 本文）。 |
| `law_synonyms` | 同義語辞書。`(canonical_term, synonym_term, priority, is_active)`。 |
| `law_document_history` | 変更履歴スナップショット。同期時に内容が変わった場合のみ `full_text` を含めて記録。 |
| `sync_settings` | 月次更新設定（日・時・分・タイムゾーン・対象ソース）、最終同期日時、エラー、`cache_generation`。 |
| `sync_runs` | 同期実行ログ。`summary_json` に追加件数・更新件数・変更なし件数・条文合計を保存。 |
| `search_query_cache` | 検索結果キャッシュ。キーは正規化クエリ + ソース + limit の SHA-256。`cache_generation` で旧キャッシュを無効化。 |
| `ask_query_cache` | 質問応答キャッシュ。同上。 |
| `meeting_sessions` | 会議単位。年度、会議種別、本会議/委員会名、会議名、ソースURL。 |
| `meeting_days` | 日程単位。開催日、PDF URL、PDFハッシュ、抽出状態。 |
| `meeting_speakers` | 発言者辞書。氏名、役職、所属、議員/執行部区分、任期。 |
| `meeting_utterances` | 発言単位。発言者、質問者/答弁者タグ、発言種別、本文、ページ範囲。 |
| `meeting_tables` | 会議録中の表。行列JSON、表示HTML、検索用テキスト、ページ、抽出信頼度。 |
| `meeting_extract_runs` | 会議録抽出実行履歴。抽出・タグ付け・表整形エンジンのバージョン、警告、エラーを保存。 |

---

## 検索アーキテクチャ

### インデックス生成（同期時）

1. 各文書・条文の本文を Janome で形態素解析し、名詞・動詞・形容詞を抽出。
2. タイトルや条番号など重要フィールドには高 weight を付与。
3. `law_search_terms` に `(target_type, target_id, term, weight)` を UPSERT（重複キーは無視）。

### 検索クエリ処理

1. 入力語（最大 4 フィールド、AND / OR 指定）を Janome で正規化・分割。
2. `law_synonyms` テーブルで同義語を展開（例: `例規 → [条例, 規則, 要綱]`）。
3. `law_search_terms` を使い各 term に該当する (document_id, article_id) のセットを取得。
4. AND フィールド: 全 term がマッチする文書・条文のみ残す（集合積）。
5. OR フィールド: いずれかの term がマッチすれば採用（集合和）。
6. スコアリング: マッチした term の weight 合計 + マッチした term 数を反映。同時にマッチ根拠（タイトル / 条番号 / 条名 / 条文 / 本文）を `match_reasons` として記録。
7. 詳細絞り込み（`lawType`, `fromDate`, `toDate`）: スコアリング後に `law_documents` をクエリしてフィルタリング。
8. 上位結果を `law_documents` / `law_articles` から JOIN して返す。
9. 結果を `search_query_cache` に保存（`cache_generation` が一致する場合はキャッシュヒット）。

> **フォールバック**: `law_search_terms` にインデックスがない場合は LIKE 検索に自動フォールバックする。

### `law_search_terms` の索引方針

`law_search_terms` は最も大きいテーブルになるため、検索で使う索引だけを維持します。

- `uq_law_search_terms_target_term (target_type, target_id, term)` で同一対象の同一term重複を防ぐ。
- `idx_law_search_terms_term_target (term, target_type)` は term 起点の候補抽出で使う。
- `idx_law_search_terms_target_term_doc_article (target_type, term, document_id, article_id)` は article/document 候補抽出と `document_id` / `article_id` のグルーピングに使う。
- `idx_law_search_terms_document (document_id)` と `idx_law_search_terms_article (article_id)` は文書削除・条文削除時の外部キー参照を支える。

`idx_law_search_terms_target_term_doc (target_type, term, document_id)` は上記の複合索引でカバーできるため、容量削減のため作成しません。

### 2 層キャッシュ

| 層 | TTL | 失効条件 |
|---|---|---|
| メモリ（`LOCAL_SEARCH_CACHE` / `LOCAL_ASK_CACHE`） | 120 秒 | サーバー再起動 / キャッシュクリア API |
| DB（`search_query_cache` / `ask_query_cache`） | 検索 30 分 / Q&A 6 時間 | `cache_generation` が変わった場合（同期・同義語変更・手動クリア） |

---

## 質問応答（Q&A）

1. 質問文を Janome で形態素解析してキーワードを抽出。
2. 「要件は？」「手続きは？」等のパターンマッチングで `questionType` を判定し、`answerLead`（導入文）を生成。
3. 抽出キーワードで `search_documents_structured` を呼び出し、上位候補条文を取得。
4. 文書ごとに条文をグループ化した `candidateGroups` を返す。
5. 結果を `ask_query_cache` に保存。

---

## 会議録検索

### 検索対象

- 美祢市Webサイトの会議録ページを起点に、本会議、常任委員会、特別委員会のPDF会議録を取り込む。
- PDFは日程単位で管理し、本文は発言単位に分割する。
- 会議録内の表は表単位で抽出し、Web表示用テーブルと検索用テキストの両方を保存する。

### エンジン分離

会議録特有の前処理は、同期処理や検索APIへ直書きしない。次の2つは専用プログラムとして分離し、辞書・ルール・エンジンバージョンを保存する。

| エンジン | 役割 | 更新単位 |
|---|---|---|
| 発言者タグ付けエンジン | PDF抽出行から発言単位へ分割し、質問者（主に議員）・答弁者（主に執行部）・議長・事務局等をタグ付けする。 | 議員/執行部辞書、役職辞書、判定ルール、エンジン本体 |
| 表整形エンジン | PDF中の表を行列構造へ復元し、HTML表示と検索可能テキストへ変換する。 | 表検出ルール、セル結合処理、HTML整形、検索テキスト生成 |

詳細は `docs/meeting-minutes-search-design.md` を参照。

### Meilisearch

会議録は例規・法令とは別インデックスにする。

- 例規・法令: `mine_city_reiki_articles`
- 会議録: `mine_city_meeting_minutes`

会議録インデックスは `session` / `day` / `utterance` / `table` の recordType を持ち、年度、開催日、会議種別、発言者、質問者/答弁者タグ、発言種別でフィルタできるようにする。

---

## データ同期

### 美祢市例規

- 美祢市公式サイトの例規一覧 HTML をスクレイピングして文書 URL を取得。
- 各文書ページから本文 HTML を取得し、テーブルを含む条文構造を解析。
- `content_hash`（SHA-256）で変更検知。変更があれば `law_document_history` にスナップショット記録。

### 地方自治法（e-Gov API）

- `https://laws.e-gov.go.jp/api/1/lawdata/{法令ID}` から XML を取得。
- 条文（Article）・款（Paragraph）単位でパースして `law_articles` に保存。

### 地方公務員法（e-Gov API）

- `https://laws.e-gov.go.jp/api/1/lawdata/{法令ID}` から XML を取得。
- 条文（Article）・款（Paragraph）単位でパースして `law_articles` に保存。

### 美祢市議会 会議録

- `https://www2.city.mine.lg.jp/gyosei/shigikai/11159.html` から本会議、常任委員会、特別委員会の各一覧へ辿る。
- 年度、会議名、日程、PDF URLを収集する。
- PDFハッシュで変更検知し、変更があった日程のみPDF抽出・発言者タグ付け・表整形・検索投入を再実行する。

### 同期結果

`sync_runs.summary_json` に以下を記録：

```json
{
  "added": 3,
  "updated": 12,
  "unchanged": 485,
  "articles": 9842,
  "source": "all"
}
```

---

## フロントエンド構成

### タブ構成

| タブ | 機能 |
|---|---|
| ダッシュボード | 統計カード（例規数・条文数・同期回数）、改定情報、利用統計（キャッシュヒット・検索ランキング） |
| 閲覧 | 体系別閲覧（美祢市例規集 / 地方自治法 / 地方公務員法トグル。美祢市例規集は体系順・番号順、地方自治法と地方公務員法は条番号順）、全文ビュー、CSV ダウンロード、ブックマーク、印刷、変更履歴・差分ビュー |
| 例規検索 | 多フィールド AND/OR 検索、詳細絞り込みフィルター、マッチ根拠バッジ、ページネーション、関連条文レコメンド |
| 会議録検索システム | キーワード検索、発言者検索、質問者/答弁者フィルター、年度/期間/会議種別フィルター、ヒット箇所ジャンプ、会議録閲覧、表検索、検索履歴、発言集作成 |
| 質問 | 自然文照会、質問テンプレート、履歴再実行、候補条文グループ表示（条文展開） |
| ブックマーク | ブラウザ localStorage に保存した例規の一覧・再参照 |
| 設定 | 月次更新設定、手動同期、全件再索引、同期履歴、キャッシュ管理、同義語管理 |

### 変更履歴・差分ビュー

- 「変更履歴」ボタンを押すとモーダルを表示。
- 各履歴エントリに「全文を見る」ボタン。クリックで `/api/documents/:id/history/:historyId` を取得。
- 現在版の全文と比較して LCS ベースの行差分を計算・表示（削除行は赤、追加行は緑）。
- テキストが非常に長い場合（行数の積 > 200,000）は差分計算を省略。

### CSV エクスポート

- ブラウザが `GET /api/documents?format=csv&source=...` を直接ダウンロード。
- UTF-8 BOM（`\ufeff`）付きで出力し、Excel で文字化けしない。

---

## 認証

- 共通認証 `mine-trout-cash-api`（ポート 8787）の `/api/auth/*` に委譲。
- フロントエンドは `/api/auth/config` と `/api/auth/me` の応答を見てログイン UI の表示有無を判定する。
- セッション Cookie を共有する同一ドメイン前提。

---

## 外部連携 API

```
GET /api/reference/search?q=育児休業&limit=5
GET /api/reference/document/:id
POST /api/ask  { "query": "会計年度任用職員の育児休業要件は？" }
```

- 同一ホスト上の業務アプリから相対パスで利用。
- 回答は法的判断の断定ではなく、原文確認用の候補提示として扱うこと。
