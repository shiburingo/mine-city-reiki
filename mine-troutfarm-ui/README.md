# @mine-troutfarm/ui

美祢市養鱒場 業務システム共通 UI パッケージです。
ポータル配色・CSS 変数・パレット管理・共通 React コンポーネントを一元管理します。

## 提供内容

| エクスポート | 内容 |
|---|---|
| `./theme.css` | CSS 変数・カラートークン・Tailwind `@theme inline` マッピング・Noto Sans JP フォント定義 |
| `./palettes` | パレット定数・`applyPortalPalette`・`getPortalPaletteById` |
| `.`（デフォルト） | 上記 + `ThemeToggle`・`PortalHeader`・`PortalUiProvider`・`usePortalUi` |

## 使い方（Vite / React）

### 1) CSS を読み込む

```css
@import "@mine-troutfarm/ui/theme.css";
```

### 2) パレット + テーマを適用する（推奨）

```tsx
import { PortalUiProvider } from "@mine-troutfarm/ui";

// アプリルートをラップする
<PortalUiProvider>
  <App />
</PortalUiProvider>
```

`PortalUiProvider` は `next-themes` の `ThemeProvider` 内に置くか、内部で組み合わせてください。

### 3) ヘッダーを使う

```tsx
import { PortalHeader } from "@mine-troutfarm/ui";

<PortalHeader
  title="アプリ名"
  onOpenSettings={() => setOpen(true)}
  syncStatusText="同期済み"
  syncStatusTone="ok"
  user={currentUser}
  onLogout={handleLogout}
  authEnabled={true}
/>
```

### 4) パレットのみ使う

```ts
import {
  PORTAL_PALETTES,
  DEFAULT_PORTAL_PALETTE_ID,
  applyPortalPalette,
  getPortalPaletteById,
} from "@mine-troutfarm/ui/palettes";

const palette = getPortalPaletteById(DEFAULT_PORTAL_PALETTE_ID);
applyPortalPalette("light", palette);
```

## 使い方（Flask テンプレ）

- `theme.css` を `static/` 配下にコピーし、テンプレで読み込んでください。
- パレットは固定色で問題なければ JS 不要です。

## 同期対象ファイル

- `src/theme.css` — CSS 変数・フォント定義
- `src/palettes.ts` — パレット定数とユーティリティ
- `src/ThemeToggle.tsx` — ダーク/ライト切替ボタン（`next-themes` 依存）
- `src/PortalHeader.tsx` — 各アプリ共通ヘッダー（lucide-react / next-themes 依存）
- `src/PortalUiProvider.tsx` — パレット + テーマ状態管理コンテキスト
- `src/index.ts` — 上記すべてを re-export

## 消費プロジェクトへの組み込み

`package.json` に `file:../mine-troutfarm-ui` 参照を追加し、`npm install` 後に各プロジェクトの `mine-troutfarm-ui/` ディレクトリへシンボリックコピーされます。

```json
{
  "dependencies": {
    "@mine-troutfarm/ui": "file:../mine-troutfarm-ui"
  }
}
```

VSCode の型解決のため、消費プロジェクトの `tsconfig` の `include` に `"mine-troutfarm-ui/src"` を追加してください。

## ピア依存

消費プロジェクト側にインストールが必要です。

- `react` / `react-dom`
- `next-themes`
- `lucide-react`
- `tailwindcss` v4

## 注意

- 本パッケージはローカル利用専用です。npm 公開はしていません。
- `private: true` のため、`npm publish` は意図的に無効化されています。
