# 共有UIの保守

このディレクトリの `check.mjs` と `lock.json` は、ポータルの
`tools/shared-ui/catalog.json` で管理する共有ソースの配布物です。
ロックの `release` は共有ソースの管理版であり、アプリや既存npmパッケージの版ではありません。
テーマ・ヘッダー・画面切替だけでなく、各システムのUI基本部品も照合対象です。

## このリポジトリだけで検査する

フロントエンドの `package.json` があるディレクトリで実行します。

```bash
npm run check:shared-ui
npm run build
```

追加依存・兄弟リポジトリ・ネットワーク・本番認証情報は不要です。
ビルド前とGitHub Actionsで、共有ファイルの変更・削除・未登録追加を検出します。
`node_modules`、`dist`、`.git`、`.DS_Store` は検査対象外です。
これは差分検査であり、改ざんに対する電子署名検証ではありません。

## 共通部品を修正する

1. ポータルの `tools/shared-ui/catalog.json` で対象ファイルの `source` を探し、`canonical` が示す正本を修正します。同じfamilyに別仕様がある場合はそちらも確認します。
2. 全リポジトリが同じ親ディレクトリにある環境で、ポータルから次のプレビューを実行します。
3. 表示される配布先・別仕様の確認対象・差分をレビューしてから `--write` で配布します。
4. 横断検査、各システムの型・ビルド・関連UIテストを実施します。リポジトリごとにGit差分をレビューしてコミットし、通常のCI・配備手順に進みます。

```bash
# NEXT_VERSION に catalog.json より大きい MAJOR.MINOR.PATCH を設定して実行
npm run shared-ui:release -- --version "$NEXT_VERSION"
npm run shared-ui:release -- --version "$NEXT_VERSION" --write
npm run check:shared-ui:workspace
```

版番号は現在の `catalog.json` より大きい版を指定してください。
別仕様の確認が必要な場合、プレビューの `reviewVariants` を調査してから
`--reviewed-variants` を明示します。この指定は別仕様への自動上書きを意味しません。
配布先独自の未登録編集・削除が見つかった場合、全体の書込みを中止します。
自動削除や強制上書きはありません。必要な変更は正本へ移すか、理由付きの別仕様として登録します。

公開済みの管理版へ追従するときは、最新のポータルを取得したうえで次を実行します。

```bash
npm run shared-ui:sync
npm run shared-ui:sync -- --write
```

基本UI部品の共通部分と別仕様を再調査する場合、ポータルから
`npm run shared-ui:inventory` を実行すると、ファイル・ハッシュ・配布先をJSONで確認できます。

どちらのコマンドも、`--write` なしでは読み取り専用です。
Gitコミット、push、npm公開、本番配備は行いません。作業中の開発サーバーや同時編集は止めてから同期してください。
配布先の `lock.json` のハッシュだけを手で合わせて検査を通す運用はしないでください。

詳細な調査結果、正本の選定、パッケージ化の判断は、ポータルの
`docs/shared-ui-maintenance.md` を参照してください。
