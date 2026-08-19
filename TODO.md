# TODO

Driftread 的開發順序以「發現來源 → 訂閱 → 持續閱讀 → 回饋推薦」的核心閉環為主。
先完成 Supabase schema 遷移，再依 P0、P1、P2 逐步開發；暫不引入 embedding 或 AI 推薦。

## 狀態

- [ ] 尚未開始
- [~] 進行中
- [x] 已完成

## Phase 0：Supabase schema 隔離與資料安全（進行中）

- [x] 將 Driftread 的 table、function 與 migration ledger 從 `public` 搬到 `driftread` schema。
      （migration 010，見 `docs/FEATURES.md` 第 5 節）
- [~] 遷移期間保留 `public._migrations` 的 PostgreSQL 相容 view，避免舊版 backend 重啟時建立空 ledger；新版 backend 部署完成後再移除。
      （view 已建立；「新版 backend 部署完成後移除」是後續的營運步驟，尚未執行）
- [x] 將 Python／Supabase client 改為 scoped schema client，避免每次 query 手寫 schema。
      （`backend/database.py::get_client()` 固定 `ClientOptions(schema="driftread")`）
- [ ] 在 Supabase Data API 的 Exposed Schemas 加入 `driftread`，補齊 `anon`／`authenticated` 所需 grant。
      （Dashboard 設定，無法從程式碼驗證，維持未確認狀態）
- [x] 重新檢查所有 RLS policy：
  - 一般使用者資料必須以 `auth.uid() = user_id` 隔離。
  - UPDATE policy 同時包含 `USING` 與 `WITH CHECK`。
  - view 使用 `security_invoker`，或移到未暴露 schema。
  - function 預設使用 `SECURITY INVOKER`；必要的 `SECURITY DEFINER` function 不放在暴露 schema，並限制 execute 權限。
      （四項皆在 `backend/migrations/002_user_features.sql`、`010_schema_access.sql` 核實：
      owner policy 用 `(SELECT auth.uid())` 且都是 `FOR ALL`／涵蓋 `WITH CHECK`；
      `security_invoker` view 見 010；無任何 `SECURITY DEFINER` function；
      `driftread` 全 schema 的 function EXECUTE 先 REVOKE 再選擇性 GRANT）
- [ ] 一般使用者路徑改用 user JWT scoped client；`service_role` 只保留給抓取、後台、migration 與其他明確的系統工作。
      （查證後這項尚未開始：`backend/database.py` 全專案只有一個 client 建構點，永遠用
      `SUPABASE_KEY`／service_role，沒有任何 user-JWT scoped client；使用者隔離目前仍全靠
      應用層手動加 `user_id` 條件，不是 RLS + user JWT）
- [ ] 遷移完成後執行 Supabase database advisors、RLS 驗證與新舊 backend 部署順序測試。
      （需要連到真的 Supabase 專案才能執行，本次未做）

## P0：補齊核心閱讀閉環

### Runtime Supabase 設定

- [x] 官方 GHCR frontend image 改用容器啟動時產生的 runtime config。
- [x] 不在 frontend build 階段寫死 Supabase URL／publishable key。
- [ ] 驗證官方 image 的登入、登出、session restore、訂閱、已讀與收藏流程。
      （`AuthService` 的登入 / session 邏輯本身未改動，只換了兩個字串的來源，已靜態覆核；
      本 sandbox 無法起 Docker daemon 也無法完整 `npm ci`，尚未跑過真的容器 + 瀏覽器驗證。）

### 訂閱操作與狀態

- [x] 建立單一訂閱狀態查詢，避免各頁各自推導狀態。
      （`frontend/src/app/services/subscription.ts::SubscriptionService`，登入後載入一次並在
      feed 詳情、目錄卡片、Discover、猜你喜歡、我的訂閱之間共用同一份快取）
- [x] Feed 詳情加入「訂閱／取消訂閱」。
- [x] Feed 目錄卡片加入快速訂閱。
- [x] 「猜你喜歡」將「喜歡」「跳過」「訂閱」拆成三個獨立語意。
      （訂閱目前仍會同時記一筆本地「喜歡」信號供本次 session 評分用——尚未接上下面
      「推薦回饋持久化」批次規劃的獨立 `subscribed` 訊號與資料表）
- [x] Discover 已收錄結果允許登入使用者直接訂閱，不只提供「前往查看」。
- [x] 未登入操作保留原路徑，登入後回到原 Feed 並完成訂閱。
      （`/login?redirect=...&subscribeFeed=...`，`Login.submit()` 登入成功後代下單並導回）
- [x] 訂閱／取消訂閱需有 optimistic UI、失敗回滾與重複請求保護。
      （`SubscriptionService.subscribe()` / `unsubscribe()`：樂觀更新、失敗回滾、pending 期間
      忽略重複呼叫；`sync()` 對還在 pending 中的項目保留樂觀值，避免與登入後的重新載入互相
      蓋掉）

### 我的閱讀流

- [x] 新增聚合所有已訂閱來源的文章時間流。
      （`GET /me/stream`，`backend/migrations/013_reading_stream.sql::list_reading_stream`；
      前端 `components/reading-stream`，掛在 `/me/stream`）
- [x] 支援分頁或 cursor pagination，不一次載入全部文章。
      （keyset cursor，同 `GET /me/reads` 既有作法；`limit` 上限 100；前端「載入更多」）
- [x] 顯示總未讀數與各來源未讀數。
      （`GET /me/stream/unread-counts` ← `reading_stream_unread_counts()`；頁面上方
      `ObStat` 總未讀／本頁未讀，來源篩選下拉帶各來源未讀數，導覽列帳號選單也有未讀數 badge）
- [x] 支援「只看未讀」「隱藏已讀」與來源篩選。
      （「只看未讀」是 server-side `unread_only`；「隱藏已讀」是 client-side 篩選已載入資料，
      刻意跟「只看未讀」分開——一個決定抓什麼，一個只決定怎麼顯示；來源篩選是 `feed_id`）
- [x] 支援單篇標記已讀／未讀。
      （`POST` / `DELETE /me/articles/{id}/read`；前端樂觀更新 + 失敗回滾，同
      `SubscriptionService` 的 pattern）
- [x] 支援目前頁面全部標已讀，以及明確範圍的全部標已讀。
      （`POST /me/reads/mark-all`：帶 `article_ids` 是目前頁面；帶 `feed_id`／不帶則整個閱讀流
      是明確範圍，走 DB function `mark_reading_stream_read` 一次 `INSERT ... SELECT`；
      明確範圍那個在前端有 `ConfirmService` 確認對話框）
- [x] 「我的訂閱」保留來源管理入口，但主要閱讀入口改為文章流。
      （導覽列帳號選單「我的閱讀」排在「我的訂閱」之前；`/me/feeds` 頁首加「前往我的閱讀」按鈕，
      subtitle 改成「管理已訂閱的來源；要開始閱讀請前往『我的閱讀』」；OPML／取消訂閱等來源管理
      功能沒有被移除）

## P1：偏好、推薦與內容探索

### 標籤、語言與偏好設定

- [ ] Feed tag 改為可點擊篩選。
- [ ] Feed 目錄加入 language、category、tag 的組合篩選。
- [ ] 建立偏好設定 UI，接上既有 `getPreferences()`／`updatePreferences()`。
- [ ] 使用受控 category/tag vocabulary，處理同義詞、大小寫與多語標籤。
- [ ] 清楚區分來源標籤、使用者自訂資料夾與推薦偏好，避免三者混用。

### 推薦回饋持久化

- [ ] 新增 `user_feed_feedback`，至少保存：
  - `liked`
  - `disliked`
  - `skipped`
  - `subscribed`
  - `unsubscribed`
- [ ] 登入後回饋存入 Supabase，支援跨裝置；匿名狀態登入後可選擇合併。
- [ ] `disliked` 不只排除單一 Feed，也降低相關 category/tag 權重。
- [ ] `skipped` 只做短期降權，不等同明確不喜歡。
- [ ] 訂閱為強正向訊號；喜歡為正向；收藏／稍後讀文章所屬來源為中度正向。
- [ ] 保留約 30% exploration，避免推薦結果過度收窄。
- [ ] 顯示推薦理由，例如「因為你訂閱了 Python、資安」。
- [ ] 先以明確行為與可解釋權重迭代，不提前導入 embedding／AI 推薦。

### Feed 完整文章列表

- [ ] Feed 詳情接上既有 articles 分頁 API，不再固定只顯示最新 10 篇。
- [ ] 支援載入更多／cursor pagination。
- [ ] 保持排序穩定，避免 refresh 後重複或漏掉文章。
- [ ] 顯示已讀、收藏狀態，並能在列表直接切換。

### 匯入後自動分類

- [ ] OPML 與網址匯入完成後執行語言偵測。
- [ ] 依受控 vocabulary 產生 category 與 tag 建議。
- [ ] 保存分類信心與分類來源，允許後續重新分類。
- [ ] 低信心結果進入待確認狀態，不直接污染推薦訊號。
- [ ] OPML 匯入保留原始 folder 結構，並映射成使用者資料夾，而不是全域 Feed tag。

## P2：搜尋與進階來源管理

### 全文搜尋

- [ ] 使用 PostgreSQL Full Text Search 搜尋文章標題、摘要、作者與全文。
- [ ] Feed 名稱／描述搜尋與文章搜尋分開呈現。
- [ ] 建立適當的 `tsvector`／GIN index，避免 `%keyword%` 全表掃描。
- [ ] 支援 language-aware configuration；無法可靠斷詞時提供可預測的 fallback。
- [ ] 結果顯示命中摘要、來源、日期、已讀與收藏狀態。

### 資料夾與來源控制

- [ ] 使用者可建立、重新命名、排序與刪除資料夾。
- [ ] Feed 可加入多個資料夾，或明確限制為單一資料夾並在資料模型中固定。
- [ ] 支援來源靜音／暫停，不必取消訂閱。
- [ ] 支援每個來源的使用者自訂名稱。
- [ ] OPML export 保留 folder 與自訂名稱。
- [ ] 提供失效來源、長期未更新來源與重複來源的管理畫面。

## 技術與可靠性優化

### API 與查詢

- [x] `GET /me/reads` 加入 cursor pagination、limit 上限與穩定排序。
- [ ] Bookmark 列表只回傳列表所需摘要，不回傳完整 `Article.content`。
- [ ] `GET /categories` 改由 SQL `DISTINCT`／RPC 聚合，不把所有 Feed 拉回 Python 去重。
- [ ] 推薦候選移除大表 `ORDER BY random()`，改用 indexed random key、pivot sampling 或可擴充的抽樣策略。
- [ ] 為常用的 subscription、read receipt、bookmark、feedback 查詢補齊複合 index。
      （read receipt／閱讀流這半邊在批次 4 補了：`user_article_reads(user_id, read_at DESC,
      article_id DESC)`（012）與 `articles(feed_id, fetched_at DESC)`（013）；`user_feeds` 與
      `user_article_reads` 既有複合主鍵已覆蓋閱讀流查詢，沒有另外加。bookmark／feedback 那兩類
      查詢還沒動——`user_bookmarks` 目前只有 `(user_id, bookmark_type)`，`user_feed_feedback`
      這張表本身都還沒建（見上方「推薦回饋持久化」批次），留給之後的批次）
- [ ] 對 PostgREST／database 例外建立一致的 API error mapping，避免裸 500。
- [ ] 為單一 Feed 手動 refresh 固定 response contract，測試不得依賴真實 DNS。

### Migration 與部署

- [ ] migration runner 加 PostgreSQL advisory lock，避免多個 API replica 同時競跑。
- [ ] migration／backfill 各自具備可追蹤、可安全重試的狀態。
- [ ] 補上升級與回滾 runbook，特別記錄 schema exposure、grant、RLS 與 runtime config 的部署順序。
- [ ] backend Python dependencies 改為可重現安裝：鎖定版本或提交 lockfile，避免只寫無上限的最低版本。
- [ ] 定期檢查 Supabase changelog 與 auth／Data API breaking changes。

### Auth 與安全

- [ ] JWT 驗證由只接受 HS256 shared secret 改為依 Supabase JWKS 驗證 ES256／RS256 signing key。
- [ ] 支援 signing key rotation 與 JWKS cache refresh。
- [ ] 匿名 `/api/discover/import` 改為要求登入，或先寫入候選審核佇列，不直接寫入全域 catalog。
- [ ] 瀏覽器擴充若提供一般使用者使用，改採 PKCE 登入與個人訂閱，不保存 Admin API Key。
- [ ] 檢查 public client 僅使用 publishable key，任何 frontend／extension 都不得含 `service_role` 或 secret key。
- [ ] 為登入後的 user-scoped API 加上跨使用者資料隔離測試。

### Frontend 與 CI

- [x] frontend GitHub Actions 除了 production build，也必須執行現有單元測試。
      （`.github/workflows/frontend.yml` 的 Build job 在 `npm run build` 前加了 `npm test`
      步驟，跑 `@angular/build:unit-test`／Vitest，用 jsdom，不需要瀏覽器）
- [ ] 將 initial bundle 超過 warning budget 的既有 4.97 kB 消除，或依實際預算重新設定並記錄理由。
- [ ] 將 Supabase client 與非首屏功能延後載入，評估是否能直接降低 initial bundle。
- [ ] 為訂閱 CTA、我的閱讀流、偏好設定與推薦回饋補前端整合測試。
      （訂閱 CTA 這部分已完成：`subscription.spec.ts`、`feed-detail.spec.ts`、
      `feed-list.spec.ts`、`discover.spec.ts`、`recommendations.spec.ts`、`login.spec.ts`。
      我的閱讀流這部分批次 4 也補了：`reading-stream.spec.ts`（service）與
      `components/reading-stream/reading-stream.spec.ts`（元件）。偏好設定 UI、推薦回饋持久化
      都還沒實作，測試無從補起）
- [ ] backend 測試中的 DNS／外部網路依賴全部 mock，讓測試在隔離環境可重現。

## 建議開發批次

各批次保持可獨立部署與回滾；前一批完成驗證後再開始下一批。

1. [~] Supabase schema 隔離、RLS、scoped client 與相容部署。
2. [~] Runtime Supabase config，確保官方 image 的登入與個人功能可用。
      （runtime config 機制已實作，見上方「Runtime Supabase 設定」；真的容器 + 瀏覽器登入驗證尚未執行）
3. [x] 訂閱 CTA、訂閱狀態與核心流程整合。
4. [x] 我的閱讀流、未讀數與已讀管理。
5. [ ] 標籤／語言篩選、偏好設定與匯入後分類。
6. [ ] 回饋持久化、可解釋推薦權重與推薦理由。
7. [ ] Feed 完整文章分頁、全文搜尋與資料夾管理。
8. [ ] 查詢效能、migration lock、JWT/JWKS、extension auth 與 CI hardening。

## 完成定義

每個功能完成前至少需要：

- [ ] API contract 與權限模型已明確。
- [ ] RLS／跨使用者隔離測試通過。
- [ ] backend tests、frontend tests、production build 通過。
- [ ] 新 migration 在乾淨資料庫與現有資料升級路徑都驗證。
- [ ] 部署設定、必要環境變數與操作文件已更新。
- [ ] 手動走過「發現 → 訂閱 → 閱讀 → 回饋」受影響的完整流程。
