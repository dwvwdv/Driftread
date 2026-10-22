# 個人閱讀出口（#78–#80）

所有出口需要永久使用者 Bearer token；backend 的 service-role 查詢明確使用認證得到的 `user_id`，不接受 caller 指定其他使用者。內容沿用 `article_publications`：只讀 normal 來源，排除 private、signal_only，正文遵循來源 fulltext policy。這些功能不呼叫 AI、產生摘要或發送郵件。

## 每日閱讀與個人 RSS

- `/me/digest` 顯示指定日期、IANA 時區的來源摘要；`GET /api/me/digest?date=YYYY-MM-DD&timezone=Asia/Taipei&limit=100` 使用當地午夜到次日午夜，包含 DST 的 23／25 小時日期。最多 100 筆，`truncated` 表示仍有資料。
- `GET /api/me/rss?limit=50` 匯出個人活躍訂閱的最近文章，最多 100 筆；只含來源摘要及原文連結。daily/RSS 排除 backfill，使用 publication timeline；RSS `pubDate` 採 `timeline_at`，僅舊資料缺少 timeline 時回退 `published_at`，避免來源錯誤的未來日期讓文章置頂。
- RSS 需要 Authorization header；目前提供登入後下載檔案，未實作可放進一般 RSS reader 的永久 token URL、排程寄送或公開分享。
- 前端切換帳號、登出與元件銷毀會取消在途請求，防止舊帳號回應顯示或下載。

## 正式唯讀 MCP

使用官方 Python MCP SDK 的 Streamable HTTP：`/api/mcp/`，每個 request 都帶 `Authorization: Bearer <access_token>`。支援 initialize、notifications、tools/list、tools/call；stateless transport 不共用帳號 session。

| Tool | 行為 |
|------|------|
| `subscriptions` | 個人未靜音且 normal 的訂閱、個人名稱；limit 1–100 |
| `reading_stream` | 個人活躍訂閱最近文章摘要；limit 1–100 |
| `search` | 個人活躍訂閱的 publication 全文索引搜尋；query 1–200 字元、limit 1–100 |
| `article` | 個人活躍訂閱的單篇 publication；只在 rights 允許時附正文 |
| `digest` | 指定日與時區的來源摘要；limit 1–100 |

全部工具標註 readOnlyHint；沒有寫入、feed 抓取或 AI 工具。CLI SDK client 可無 Origin；瀏覽器 Origin 必須與 Host 相同。部署時 reverse proxy 必須限制可信 Host。

來源收錄評審另設獨立授權的 `/api/admin/mcp/`，不改此個人唯讀介面；管理工具、專用 key 與可選人工覆核見 [來源評審](SOURCE_REVIEW.md)。

個人搜尋的一般正詞 AND／OR 查詢先使用原始 `articles.search_vector` 與許可摘要 expression 的兩個 GIN 索引聯集篩選候選 ID，再以 publication 的許可向量確認命中及排序。摘要索引使用與 summary-only projection 完全相同的表達式，保留摘要 500 字元裁切產生的詞；禁止正文不會影響結果相關度，也不回傳正文或向量。負詞／片語查詢刻意退回本人未靜音訂閱的許可向量比對，避免原始正文的負詞或政策裁切後的詞位置排除可見結果；此類查詢仍可能掃描訂閱歷史。摘要 expression 索引增加一份摘要／metadata 的索引儲存與文章寫入成本。

## 同步與離線快取的限制

`GET /api/me/sync?cursor=...&pending_ids=<uuid>` 使用帳號綁定的版本化游標；另一個使用者的游標與畸形游標回 400。游標不是權限憑證，每次仍須認證。最多 100 個 pending article IDs（extension 超過上限會要求先同步），回應會再次確認其訂閱／publication 權限。

這是「增量判斷是否有變更 + 有界快取快照」，不是任意長度閱讀歷史的 incremental delta。相關變更會回傳最近 100 篇的完整 replacement snapshot；client 整批替換快取。DELETE、取消／靜音訂閱、來源 private／signal_only 和 rights 更新都會失效舊快取；無相關變更時只推進 cursor，不重送 items。回應不包含正文或 search_vector。

`sync_clock` 全域單列鎖在交易內指派 sequence，snapshot 取得同一把鎖，避免跳過尚未提交的早期寫入；rollback 同時回滾 sequence 與 ledger。這會序列化相關寫入與 snapshot，是目前單一自架部署接受的成本。ledger 只記 table、operation、user_id 與時間，不留文章／使用者 payload；約保留 50,000 次 invalidation（每 1,000 次修剪，最多短暫多 999 筆），過期或超前 cursor 會 reset 至最近 100 篇。既有已授權 owner RLS 寫入由限定用途的 SECURITY DEFINER trigger 寫私有 ledger；固定 pg_catalog search_path，撤銷 PUBLIC／anon／authenticated EXECUTE。snapshot／search RPC 僅 service_role 可執行。

Extension 同步先拉權限 snapshot，剔除失權 pending operation，再用原帳號 connectionId 與 token 呼叫 `POST /api/me/sync/operations`（article_id、kind、enabled），最後拉回 authoritative snapshot。專用 service-only INVOKER RPC 同交易鎖定 normal 來源、本人未靜音訂閱與 publication 文章，再冪等寫入／刪除 read、favorite 或 read_later；user_id 只由認證取得。preflight 後撤權回 404，丟棄該操作並繼續更新。公開 reader 的原已讀／收藏 API 語義保留，不作為離線重放入口。

批次文章寫入與 sync ledger 的既有鎖順序可能與 replay 競跑；RPC 設 250ms lock timeout，捕捉鎖逾時／死鎖時由 exception subtransaction 回滾狀態與 ledger，回 503／Retry-After: 1。Client 保留 pending，下一次同步再送，不以 409 丟掉暫時繁忙的操作。網路失敗同樣保留 queue。已老化出最近 100 篇、仍有權限的 pending operation 可繼續重放。

本機快取只有摘要和狀態；有效期 24 小時，明確斷線、目前連線收到 401／403、成功切換帳號或 API 時清除，token 只存 Chrome session storage，不跨 Chrome 帳號同步。跨頁面的連線及快取變更由擴充 service worker 序列化，斷線／切帳號會等待已接受的 storage 寫入完成再清除或替換，拒絕舊 connectionId 的後續寫入；細節見 [extension 連線契約](../extension/README.md#安裝與連線)。候選連線驗證失敗保留目前帳號的快取與 pending，同一 API／帳號重新連線保留 queue。沒有 server push；離線時無法即時察覺撤權，須重新連線同步或等待快取過期。沒有完整歷史下載、正文離線閱讀或無限期離線支援。

## 行動裝置閱讀出口（PWA）

Issue #92 的行動出口採 **PWA**，不另建 Flutter app。理由：

- 沿用既有 Angular 前端、Offbeat 元件庫、Supabase 登入與 publication 讀取層，只多一份 manifest、service worker 與離線模組；Flutter 要再維護一套 UI、認證、權限與發佈流程（兩個商店的審核與簽章），對單一自架部署成本過高。
- `/me/sync` 的 invalidation／100 篇 replacement snapshot 與 `/me/sync/operations` 的離線重送已由 extension 驗證過契約，PWA 直接重用，不新增後端資料模型、API 或 migration。
- 同一個 frontend image 即可安裝到 Android／iOS 主畫面，部署順序不變。

### Service worker 快取策略

使用 `@angular/service-worker`（版本與 Angular 21 對齊），由 `frontend/ngsw-config.json` 設定，production build 產生 `ngsw-worker.js`／`ngsw.json`；註冊在 `src/app/offline/sw-registration.ts`，以 lazy import 執行，不使用 `provideServiceWorker()`，所以不增加 initial bundle。

- **只快取 app shell**：`index.html`、hash 檔名的 JS／CSS、manifest、icon（install 時 prefetch）。離線時導覽仍可開啟任何前台路由。
- **任何 `/api/` 回應都不進 Cache Storage**：沒有任何 asset／data group 涵蓋 `/api/**` 或 `/.well-known/**`，ngsw 對未設定的請求直接走網路；`navigationUrls` 也排除這兩個前綴，避免 OAuth／API 導覽被回 `index.html`。帶 Authorization 的私人資料只存在下述 IndexedDB，且依帳號分隔。`src/app/offline/ngsw-config.spec.ts` 守住這條。
- **`env.js` 不在 app shell**：它是容器啟動時 render 的 runtime config（只含 Supabase URL 與 anon key），改用 `freshness`（network-first，5 秒逾時才回快取），換 Supabase 設定後下次連線即生效；離線時用最後一次成功的版本，讓 Supabase client 能讀回本機 session。首次造訪時 SW 接管頁面後會再請求一次 `env.js` 填入此快取。
- 沒有應用內「有新版本」提示：新部署在之後的頁面載入生效（ngsw 預設行為）。

frontend nginx 對 `index.html`、`env.js`、`ngsw.json`、`ngsw-worker.js`、`safety-worker.js`、`manifest.webmanifest` 回 `Cache-Control: no-cache`，讓更新檢查與 runtime config 不被 HTTP 快取卡住；manifest 以 `application/manifest+json` 回應。目前 nginx 沒有 CSP，SW 與 manifest 都是同源資源；日後加 CSP 時需允許 `worker-src 'self'`、`manifest-src 'self'`。

### 離線閱讀與重新連線同步

`/me/offline` 列出本機快取、`/me/offline/:id` 顯示單篇摘要（`[innerHTML]` 經 Angular sanitizer，不使用 `bypassSecurityTrustHtml`），連線時提供「線上閱讀全文」與原文連結。語意照 extension（`extension/offline.js`）：

- 第一次按「同步」才建立本機快取（opt-in）。之後登入且連線時，若快取超過 15 分鐘或有待送操作，會在背景同步：觸發時機是載入帳號快取、`online` 事件、頁面回到前景（`visibilitychange`，涵蓋休眠後恢復但 `navigator.onLine` 一直為 true 的 PWA），以及頁面在前景時快取滿 15 分鐘的計時器；已有同步進行時不重複排入。
- `changed` 時以最近 100 篇整批替換（只保留白名單摘要欄位，不保存正文／search_vector），否則只推進 cursor。快取 24 小時後不再顯示，須連線重新同步，以免顯示已撤權內容。
- 已讀／收藏／稍後讀先樂觀疊加在快照上並 coalesce 寫入佇列（同一篇同一種只留最後期望狀態，最多 100 篇）。同步順序同 extension：先帶 `pending_ids` 拉 snapshot 剔除已失權的操作（只剔除本次有詢問的文章），逐筆 `POST /api/me/sync/operations`，最後再拉 authoritative snapshot。404／409 丟棄該操作並回到伺服器狀態（等同回滾，畫面提示）；503、網路失敗、401／403 保留佇列等下次同步。IndexedDB 寫入失敗立即回滾樂觀狀態。
- 頁尾狀態列顯示 ONLINE／OFFLINE 與待同步數量。

### 帳號隔離

- IndexedDB `driftread-offline` 以 `user_id` 為 key，並記錄唯一 owner；登入（或切換）為某帳號時，同一個交易內刪除其他帳號的快取與待送佇列。寫入時在交易內再比對 owner，舊帳號延遲回應或寫入不能把資料寫回。
- 本執行期間 session 結束（登出、refresh token 被撤銷）會清空整個離線資料庫；前端登出按鈕先清空再呼叫 Supabase sign-out，所以離線時登出（Supabase 無法連線、session 未移除）仍會清掉私人快取。
- 同步開始時鎖定該帳號的 access token 並明確放進 Authorization（`authInterceptor` 不覆寫既有 Authorization），每次請求前檢查帳號與世代未變；切換帳號後不會用 B 的 token 送出 A 的操作，A 的操作也已被刪除。
- 例外：access token 在離線期間過期時 Supabase 無法恢復 session，但沒有登出事件。此時 `/me/offline` 只在**離線**狀態下以唯讀方式顯示上次 owner 的 24 小時內快取（不能排入操作）；一旦連線且沒有 session 就不顯示，等使用者登入（同帳號保留、不同帳號刪除）。這與擴充「browser 重啟後保留 queue」的取捨相同；共用裝置請先登出。

### 已知限制

- 離線只有摘要，沒有正文離線閱讀；完整歷史下載與 server push 撤權同 extension 不提供。
- `navigator.onLine` 只反映網路介面；captive portal 仍視為線上，請求失敗時以錯誤訊息處理並保留佇列。
- iOS Safari 的 PWA 儲存可能被系統回收；IndexedDB 不可用（例如部分私密瀏覽）時離線功能停用，線上閱讀不受影響。

## 驗證

PWA：`src/app/offline/*.spec.ts` 以 fake-indexeddb 驗證快取依 user 分隔、切帳號／登出清空、延遲寫入被拒、invalidation／replacement snapshot、離線佇列重送（原帳號 token）、404 丟棄回滾、503／網路失敗保留、IndexedDB 寫入失敗回滾、帳號切換中斷重送，以及 SW 設定不涵蓋 `/api/`；元件 spec 覆蓋登入提示、唯讀 dormant、離線狀態、sanitizer 與登出先清快取。Production build 另以 Chromium（Playwright）對 fake API 走查 manifest、SW 註冊、離線重新載入、重新連線重送、Cache Storage 無 `/api/` 與 390px 版面；真手機安裝、iOS Safari 與真 Supabase 尚未驗證。

`backend/tests/test_sync_postgres.py` 使用獨立 PostgreSQL 測試資料庫驗證角色權限、owner RLS 寫入、交易 race／rollback、刪除與撤權、使用者搜尋隔離。`test_mcp.py` 使用官方 ClientSession 做 protocol interoperability；extension Node tests 覆蓋 account／options lifecycle、權限預檢、404 後重試及網路失敗。daily frontend tests 覆蓋登入、時區、空畫面、錯誤、帳號切換與 destroy。

個人搜尋另以 `backend/tests/sql/test_personal_search_candidates.sql` 驗證候選前後命中／排名、禁止正文、摘要裁切詞、負詞／片語／OR、owner／mute 與 metadata 契約，並重跑 migration；5,000 篇歷史資料的 `EXPLAIN ANALYZE` 驗證實際 RPC SQL 的選擇性查詢使用兩個 GIN，先縮減至候選後才執行許可 projection。

`.github/workflows/extension.yml` 在 extension 變更的 PR／push 執行 Node 測試，不取代安裝 Chrome 的實際 UI 走查。
