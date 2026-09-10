# @mine-troutfarm/ui (vendored)

このディレクトリは、このリポジトリに同梱している共有UIです。
実際の依存指定はフロントエンド内の `file:./mine-troutfarm-ui` です。
兄弟ディレクトリの古い `mine-troutfarm-ui` は参照・配布元にしません。

共有ソースの正本・版・配布先は、ポータルの `tools/shared-ui/catalog.json` で管理します。
既存パッケージの `0.1.0` と共有ソースの管理版は別です。同じパッケージ版でも、
承認済みのヘッダー・配色などの別仕様があるため、手作業で一括上書きしないでください。

[共有UIの検査・更新手順](../.shared-ui/README.md)に従って修正・配布してください。

```bash
npm run check:shared-ui
```

このコマンドは親のフロントエンドディレクトリで実行します。
