# 技術仕様

## 目的

- 美祢市例規集と地方自治法を横断検索・条文単位で参照できるようにする。
- 同ホスト上の業務アプリから REST API 経由で条文検索・参照ができるようにする。
- 自然文質問に対して関連条文候補を提示する（法的判断の断定はしない）。

---

## データベーステーブル構成

| テーブル | 役割 |
|---|---|
| `law_documents` | 文書単位。`source`（mine-city / egov）、タイトル、法令種別、全文、コンテンツハッシュ等を保持。 |
| `law_articles` | 条文単位。`document_id` FK、条番号・条名・親パス・本文・検索用テキスト。`sort_key` で順序管理。 |
| `law_search_terms` | 転置インデックス。`(target_type, target_id, term, weight)` の形で文書・条文それぞれに term を紐付ける。`weight` は重み（タイトル > 条番号 > 本文）。 |
| `law_synonyms` | 同義語辞書。`(canonical_term, synonym_term, priority, is_active)`。 |
| `law_document_history` | 変更履歴スナップショット。同期時に内容が変わった場合のみ `full_text` を含めて記録。 |
| `sync_settings` | 月次更新設定（日・時・分・タイムゾーン・対象ソース）、最終同期日時、エラー、`cache_generation`。 |
| `sync_runs` | 同期実行ログ。`summary_json` に追加件数・更新件数・変更なし件数・条文合計を保存。 |
| `search_query_cache` | 検索結果キャッシュ。キーは正規化クエリ + ソース + limit の SHA-256。`cache_generation` で旧キャッシュを無効化。 |
| `ask_query_cache` | 質問応答キャッシュ。同上。 |

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

## データ同期

### 美祢市例規

- 美祢市公式サイトの例規一覧 HTML をスクレイピングして文書 URL を取得。
- 各文書ページから本文 HTML を取得し、テーブルを含む条文構造を解析。
- `content_hash`（SHA-256）で変更検知。変更があれば `law_document_history` にスナップショット記録。

### 地方自治法（e-Gov API）

- `https://laws.e-gov.go.jp/api/1/lawdata/{法令ID}` から XML を取得。
- 条文（Article）・款（Paragraph）単位でパースして `law_articles` に保存。

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
| 閲覧 | 例規一覧（法令種別グループ）、全文ビュー、CSV ダウンロード、ブックマーク、印刷、変更履歴・差分ビュー |
| 例規検索 | 多フィールド AND/OR 検索、詳細絞り込みフィルター、マッチ根拠バッジ、ページネーション、関連条文レコメンド |
| 質問 | 自然文照会、質問テンプレート、履歴再実行、候補条文グループ表示（条文展開） |
| ブックマーク | ブラウザ localStorage に保存した例規の一覧・再参照 |
| 同期設定 | 月次更新設定、手動同期、同期履歴、キャッシュ管理、同義語管理 |

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

- `mine-troutfarm` 認証サービス（ポート 8787）に委譲。
- `VITE_AUTH_ENABLED=true` の場合のみログイン UI を表示。
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
