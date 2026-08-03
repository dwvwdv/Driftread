# Driftread 前端

Angular 21，standalone components + signals，UI 走自建的 **Offbeat** 設計系統。

```bash
npm ci
npm start          # ng serve，http://localhost:4200
npm run build      # production config（strictTemplates 與樣式預算全開）
npm test           # vitest + jsdom
npx prettier --write "./src/**/*.ts" "./src/**/*.html" "./src/**/*.scss"
```

開發模式透過 `fileReplacements` 換成 `environments/environment.development.ts`，
API 指向 `http://localhost:8000/api`；正式版是相對路徑 `/api`，由 nginx 代理。

---

## 沒有 Angular Material

Material 是刻意移除的，**不要重新引入**。它的 Material 3 語言（圓角、elevation
陰影、ripple、Roboto）與 Offbeat（零圓角、2px 邊框、實心偏移陰影）直接衝突，深度
覆寫 `--mat-sys-*` token 只會一路漏出 Material 感，而且每次 Angular 升版都可能失效。

只保留 `@angular/cdk`，而且只用兩個模組：`overlay`（toast、confirm dialog）與
`a11y`（`cdkTrapFocus`、`LiveAnnouncer`）。其餘都是原生元素加 CSS。

## 目錄

```
src/styles/           全域樣式。只有 styles.scss 可以 @use 其中會產出 CSS 的檔案
  _tokens.scss        Nord 原色 + 語意別名 + 深/淺兩套主題（含對比度量測記錄）
  _mixins.scss        offset-shadow / focus-ring / mono-label / 斷點（不產出 CSS）
  _components.scss    全域 recipe class：.ob-btn / .ob-input / .ob-select / .ob-chip …
  _prose.scss         文章第三方 HTML 的樣式（必須全域，見下）
  _reset.scss

src/app/ui/           Offbeat 元件庫
src/app/layouts/      PublicLayout / AdminLayout —— 前後台兩個獨立的 shell
src/app/features/     後台子頁
src/app/components/   前台頁面
```

## 三條規則

**1. 元件 SCSS 只用語意別名，不碰 `--nord-*`。**
raw palette 只出現在 `_tokens.scss`；語意層才是會隨主題翻轉的那一層。

**2. 「原生元素加塗裝」用全域 class，不做成元件。**
button、input、textarea、select、checkbox、chip、divider 全部保留原生元素，樣式在
`_components.scss`。兩個理由：原生元素自帶鍵盤操作、type-ahead 與行動裝置原生選單，
放棄 Material 後這些得靠平台買回來；而且 `anyComponentStyle` 預算是**每元件** 4kB，
mixin 被 12 個元件 include 就是 12 份各自計費，全域 class 只算一次。

`src/app/ui/` 只放真的有結構或行為的東西：card、tabs、paginator、toast、icon、
state、field、list-row、page-header、callout、stat、confirm。

**3. 文章內容的樣式必須放在全域。**
view encapsulation 會給元件樣式加 `_ngcontent` 屬性，而 `[innerHTML]` 注入的節點永遠
拿不到那個屬性——元件 SCSS 碰不到它。所以 `.prose` 在 `_prose.scss`。
文章內容一律走 `[innerHTML]` 讓 Angular 的 sanitizer 作用，**永遠不要用
`bypassSecurityTrustHtml`**。

## 主題

深色是預設（Offbeat 的原生形態）。優先序：

```
:root                              深色
:root:not([data-theme])            + @media (prefers-color-scheme: light) → 淺色
:root[data-theme='light'|'dark']   使用者明確選擇，永遠最優先
```

所以「跟隨系統」是**移除**屬性，不是把它設成某個值。選擇存在
`localStorage['driftread_theme']`，並由 `index.html` 的 inline script 在 first paint
前套用——沒有它，淺色使用者每次載入都會閃一下深色。改動 `ThemeService` 時記得那段
script 的 storage key 與屬性名要同步。

淺色主題以 Nord Snow Storm 當表面、Polar Night 當文字，**刻意不使用純白**。

## 對比度

Nord 是為語法高亮設計的低對比色票，數個自然搭配在 UI 文字上達不到 WCAG AA。
`_tokens.scss` 裡每個衍生值旁邊都記了量測結果與它存在的理由。動色票之前先讀那段註解——
最容易踩的是把主要按鈕「改回」frost3，那是 3.47:1，不合格。

## 無障礙

放棄 Material 等於放棄它的無障礙成果。新增互動元件時至少要有：可見的
`:focus-visible` 環、正確的 role/aria、完整鍵盤操作；overlay 類要用 CDK 的
`FocusTrap`，並且離場時把焦點還給開啟它的元素。行動版抽屜除了 transform 位移還要
`visibility: hidden`，否則鍵盤使用者會 Tab 進一個看不見的選單。

## 後台

`/admin/**` 是獨立的路由樹掛在 `AdminLayout` 下，前台沒有任何連到它的連結。
`adminGuard` **只檢查這個分頁有沒有輸入過金鑰，不是認證**——它無法驗證金鑰，能驗的
只有後端的 `_require_api_key`。金鑰存在 `sessionStorage`，不會出現在網址、不會被記錄、
也不會被畫回畫面上。細節見 [docs/FEATURES.md 的前端路由章節](../docs/FEATURES.md#4-前端路由)。
