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
      （`GET /me/stream`，`backend/migrations/015_reading_stream.sql::list_reading_stream`；
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
- [x] `ReadingStreamService` 補齊 pending-write 與並發 GET 的 reconciliation（同
      `SubscriptionService` 的 `beginFetch`/`confirmWrite`/`asOf` ticketing，見該檔案開頭註解）。
      （PR #43 code review 過程中確認的已知缺口，當時故意不修：`_itemsGeneration`／
      `_countsGeneration` 只解決「較新請求蓋掉較舊請求」，沒解決「這篇文章有 pending 寫入，GET
      回來的舊快照不能贏」。現改為單一共用的 `version` 序號，`load()`/`loadMore()` 依
      `_pending`（in-flight 寫入優先於快照）與 `_confirmedRead`/`_confirmedReadAt`（比 GET
      的 `asOf` 更新的已確認寫入優先於快照）重建回傳的頁面；未讀數改成「最後一次接受的 GET
      快照＋尚未確認被該快照涵蓋的本地 delta」，GET 回應一律套用而不是整批丟棄——舊版在還沒有任何
      快照時就因為與本地寫入競速而整包丟棄回應，還是照樣把 `countsLoaded` 設成 true，未讀數會卡在
      clamp 後的錯誤值到永遠；`markAllReadInView` 現在也會把批次的目標 id 一併登記進
      `_pending`，同一篇文章的單篇 markRead/markUnread 與批次寫入互斥，不會各自套用一次
      optimistic delta 而重複計算。三個情境各自新增 `reading-stream.spec.ts` 案例）
- [ ] `markAllReadInScope` 與同一篇文章的 pending markRead/markUnread 之間，仍有一個未解的
      排序歧義：若某篇文章的 markUnread 已經在伺服器端 commit（該文章變成未讀），但回應還沒
      送達 client（`isPending` 仍是 true），此時一個涵蓋該文章的 `markAllReadInScope` 緊接著
      在伺服器端 commit（把這篇文章標已讀）——目前的寫法會因為 `isPending` 為 true 而跳過更新
      這篇文章的本地列與 `confirmReadState`；等到那個延遲的 markUnread 回應終於抵達，它的
      success handler 仍會照常呼叫 `confirmReadState(id, false, null)`，把已經被 mark-all
      覆蓋過的「已讀」真相誤蓋回「未讀」，且未讀數的 delta 也會被錯誤地重新加回去。
      根因是 client 端無法從回應抵達順序推斷兩個獨立寫入在伺服器端真正的 commit 順序——除非
      API 額外回傳可比較的列版本／時間戳，否則任何用「哪個回應先抵達」或「哪個先呼叫
      confirmReadState」當作決勝規則的修法，都只是把現有的不確定性換一個方向，不能真正解決。
      是窄視窗（需要兩個獨立寫入短時間內命中同一篇文章）且不影響伺服器端資料正確性，只影響
      UI 顯示到下次 reload 為止；PR #56 code review（Codex，P2）提出，因為需要新的決勝政策或
      API 合約變更才能穩妥解決，留在這裡待人工決定方向，未在該 PR 內強行修。
- [ ] 未讀數的 `_countDeltas`：一篇文章的寫入仍是 pending 時，它的 delta entry 無論 ticket
      為何一律疊加（見 `recomputeCounts()` 的機制註解），這是刻意的選擇，但也有已知、同上一項
      同類的窄視窗代價——若某篇文章的 markRead 已經在伺服器端 commit，但它自己的回應還沒送達
      client（entry 仍是 pending），此時一個「之後才發出、但先抵達」的 `loadCounts()` GET
      剛好命中已經反映這次寫入之後的伺服器狀態，`recomputeCounts()` 仍會把這篇文章的 `-1`
      delta 疊加在這個「其實已經包含這次寫入」的 baseline 之上，造成未讀數被多扣一次，直到
      下一次完整的 `loadCounts()` 刷新才會修正。反過來讓 pending 的 entry 也比照已確認寫入去
      跟 baseline 的 ticket 比較，會直接讓本節上面「`ReadingStreamService` 補齊
      pending-write...」那項修掉的原始 bug 重新出現（一個寫入還沒真的送達伺服器就被 baseline
      判定「已經反映」而整個丟棄，未讀數永久少算直到下次刷新）——這兩個方向的 bug 無法只靠
      client 端的 ticket 比較同時解掉，需要 API 額外提供能比較兩者先後的依據（例如列版本／
      時間戳）才能穩妥解決，同上一項的根因。PR #56 code review（Codex，P2）提出，故意保留現狀
      （一律疊加）而不修，因為對調方向只是把已修好的 bug 換回來，並在 `recomputeCounts()`
      的註解記錄取捨理由。
- [x] `_countDeltas` 原本以 article id 為 key 合併淨值（一篇文章的多次寫入互相抵銷成一個
      數字），但這會讓「較舊、已確認、但還沒被目前 baseline 反映」的寫入跟「較新、仍
      pending」的寫入疊在一起被誤判——例如：文章從已讀狀態 `markUnread` 成功確認後，發出
      `loadCounts()`，該次 GET 還沒回來前又對同一篇文章 `markRead`：合併淨值會直接把
      `+1`／`-1` 相消刪掉整條記錄；等 GET 回來（剛好只反映了 `markUnread`，還沒反映
      `markRead`）就少了「還有一個 pending 寫入尚未疊加」的紀錄，`markRead` 之後真的成功時
      未讀數也不會再更新，卡在錯誤值。修法：`_countDeltas` 改成不合併、每個寫入各自一筆
      entry（`{articleId, feedId, amount, pending, confirmedAt}`），成功時原地標記
      `confirmCountDelta()`、失敗時整條移除 `removeCountDelta()`，不再用相消的方式處理
      rollback。同一輪也修掉 `reconcileItems()` 的另一個問題：文章有 pending 寫入時，原本是
      整個回傳快照前的舊物件（`return prior`），這會連同已讀狀態一起把 feed 重新抓取可能
      更新過的標題／摘要／作者等欄位一起蓋回舊值；改成只從舊物件合併 `is_read`／`read_at`
      兩個實際在競爭的欄位。PR #56 code review（Codex，P2 ×2）提出，`reading-stream.spec.ts`
      各補一個案例。
- [x] `markAllReadInView` 的樂觀套用階段對每個 target 各呼叫一次 `commitCountDelta()`，
      而 `recomputeCounts()` 是 O(目前未平倉 entry 數)，對同一批 target 逐一呼叫等於把
      準備階段做成 O(n²)——單一來源一次全部標已讀的文章數大時（例如一次數百篇），這段還沒送出
      任何 HTTP 請求就先卡住 UI thread 的準備工作會明顯變慢；失敗批次的 rollback 迴圈原本也是
      逐篇呼叫 `removeCountDelta()`，有同樣的問題。修法：新增 `pushCountDelta()`（只建立
      entry，不觸發 recompute）與 `removeCountDeltas()`（批次移除＋只 recompute 一次），
      `markAllReadInView` 的樂觀套用與失敗 rollback 都改成先批次處理、迴圈結束後才呼叫一次
      `recomputeCounts()`；單篇 `markRead`/`markUnread` 沿用的 `commitCountDelta()`/
      `removeCountDelta()`（單篇呼叫即 recompute）不受影響。PR #56 code review
      （Codex，P2，效能）提出。未新增測試——這是可觀察行為不變、只有內部呼叫次數改變的效能
      修正，既有的大批次（620 篇）正確性測試已經覆蓋修改後的邏輯仍然算對，錄影 signal
      `.set()` 呼叫次數需要暴露內部實作細節，不值得為此新增測試耦合。

### 標籤、語言與偏好設定

- [x] Feed tag 改為可點擊篩選。
      （`frontend/src/app/components/feed-list`：分類卡片與作用中篩選列上的標籤都是
      `<button class="ob-chip">`，點擊即以該標籤篩選，再點一次清除——與偏好設定 UI 的
      toggle chip 同一套寫法）
- [x] Feed 目錄加入 language、category、tag 的組合篩選。
      （`GET /feeds` 新增 `language` 查詢參數，與既有 `category`／`tag` 一樣是 `AND` 疊加；
      前端加一個語言 `<select>`，選項來自 `GET /feeds/languages`，與分類下拉同一套寫法）
- [x] 建立偏好設定 UI，接上既有 `getPreferences()`／`updatePreferences()`。
      （`frontend/src/app/components/preferences`，`/me/preferences`；分類／語言選項各自來自
      `GET /feeds/categories`／新增的 `GET /feeds/languages`（migration 014），不是寫死清單）
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

- [x] Feed 詳情接上既有 articles 分頁 API，不再固定只顯示最新 10 篇。
      （`GET /feeds/{feed_id}/articles` 從 offset 分頁改成 keyset／cursor 分頁，見下）
- [x] 支援載入更多／cursor pagination。
      （`backend/migrations/016_feed_article_list.sql::list_feed_articles`，沿用
      `list_reading_stream` 同一套 `COALESCE(published_at, fetched_at) DESC, id DESC` 排序鍵與
      cursor 形狀；前端 feed-detail 頁「載入更多」）
- [x] 保持排序穩定，避免 refresh 後重複或漏掉文章。
      （同上——舊版用 `.range()` offset 分頁＋單欄 `published_at` 排序，NULL 值排序不穩定，
      新文章插隊時翻頁也會重複/漏掉；改用 keyset 分頁後兩個問題都不存在）
- [x] 顯示已讀、收藏狀態，並能在列表直接切換。
      （`list_feed_articles` 在呼叫者已登入時一併帶出 `is_read`／`is_bookmarked`（收藏類型固定
      `favorite`）；未登入者兩者皆為 false。前端每列有已讀／收藏切換按鈕，僅登入後顯示，
      樂觀更新＋失敗回滾，同 `ReadingStreamService`／`SubscriptionService` 的既有 pattern）

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
- [x] Bookmark 列表只回傳列表所需摘要，不回傳完整 `Article.content`。
      （`routers/me.py::list_bookmarks` 已回傳 `ArticleSummary`，DB 端也只 select 摘要欄位；
      `test_list_bookmarks_omits_article_content` 已覆蓋，這裡先前只是漏勾）
- [x] `GET /categories` 改由 SQL `DISTINCT`／RPC 聚合，不把所有 Feed 拉回 Python 去重。
      （`driftread.list_feed_categories()`，見 migration 011，`routers/feeds.py` 用 `db.rpc(...)` 呼叫，
      這裡也是先前漏勾）
- [x] 推薦候選移除大表 `ORDER BY random()`，改用 indexed random key、pivot sampling 或可擴充的抽樣策略。
      （migration 018：`feeds.sample_key`，索引化 `double precision DEFAULT random()`；
      `sample_feed_candidates()` 改為從隨機 pivot 值起做有界索引範圍掃描，pivot 落在 key
      空間尾端時用第二段有界掃描從頭補滿，兩段都是 index scan 不再是全表排序；本地
      200k 列驗證：舊版 ~56ms、新版 ~2.7ms，且差距隨表變大而擴大）
- [x] 為常用的 subscription、read receipt、bookmark、feedback 查詢補齊複合 index。
      （`user_feeds` 靠 PK 前導欄位已足夠；`user_article_reads` 見 migration 012；
      `user_bookmarks` 新增 migration 013：`(user_id, bookmark_type, created_at DESC)`，
      滿足 `GET /me/bookmarks` 的等值篩選加 `ORDER BY created_at DESC`；閱讀流查詢另補
      `articles(feed_id, fetched_at DESC)`（見 migration 015，`user_feeds`／
      `user_article_reads` 既有複合主鍵已覆蓋閱讀流查詢，沒有另外加）。
      feedback 資料表本身尚未建立，見下方「推薦回饋持久化」，屆時一併補 index）
- [x] 對 PostgREST／database 例外建立一致的 API error mapping，避免裸 500。
      （`backend/errors.py::map_postgrest_error`，`main.py` 以 `app.exception_handler(APIError)`
      註冊；unique/foreign-key/not-null/check violation/invalid input 分別映射到
      409／409／400／400／400；`PGRST116`（`.single()`／`maybe_single()` 的零筆或多筆）只有
      零筆才映射 404，多筆是資料/查詢異常，落回通用 500；`42501`（insufficient_privilege）
      刻意不映射成 403——本專案唯一的 DB client 永遠用 service_role key，沒有
      per-request 身分，`42501` 只可能是 key／grant 設定錯誤，也落回通用 500。未知或缺
      `code` 的同樣一律回通用 500，不把 `message`／`details` 洩漏給呼叫端——真正的錯誤內容
      只寫進 server-side log）
- [x] 為單一 Feed 手動 refresh 固定 response contract，測試不得依賴真實 DNS。
      （測試本來就已 mock `fetch_and_parse_conditional`，不打真實網路；
      response contract 部分新增 `FeedRefreshResult` Pydantic model，取代原本的 `response_model=dict`，
      欄位名稱與既有外部合約（`inserted` 等）保持不變）

### Migration 與部署

- [x] migration runner 加 PostgreSQL advisory lock，避免多個 API replica 同時競跑。
      （`migrate.py::acquire_migration_lock`，`pg_advisory_lock`，`run_backfills()` 共用
      同一把鎖——這裡先前也是漏勾）
- [x] migration／backfill 各自具備可追蹤、可安全重試的狀態。
      （兩者都記在同一張 `driftread._migrations`：套用成功才 commit，重跑會先查表跳過已套用的
      項目，先前也是漏勾）
- [x] 補上升級與回滾 runbook，特別記錄 schema exposure、grant、RLS 與 runtime config 的部署順序。
      （新增 `docs/RUNBOOK.md`；順便發現 GHCR image 過去只打 `:latest` tag、沒有任何可回滾的
      版本化 tag，一併把 `.github/workflows/{backend,frontend}.yml` 改成同時打
      `sha-<commit sha>`，回滾 runbook 才有真的能操作的步驟）
- [ ] backend Python dependencies 改為可重現安裝：鎖定版本或提交 lockfile，避免只寫無上限的最低版本。
      （本 sandbox 的 pip index allowlist 擋掉 PyPI，無法在本機解析版本產生真的 lockfile，
      留給有網路的環境做）
- [ ] 定期檢查 Supabase changelog 與 auth／Data API breaking changes。

### Auth 與安全

- [x] JWT 驗證由只接受 HS256 shared secret 改為依 Supabase JWKS 驗證 ES256／RS256 signing key。
      （`auth._verify_token` 依 token header 的 `alg` 分流：`HS256` 仍用 `SUPABASE_JWT_SECRET`
      （尚未輪替 signing key 的專案），`ES256`／`RS256` 改用 `jwt.PyJWKClient` 向 JWKS 端點取
      `kid` 對應公鑰驗證，兩條路徑各自固定死驗證方式與金鑰來源，不互相借用，見
      `docs/SECURITY.md` #31）
- [x] 支援 signing key rotation 與 JWKS cache refresh。
      （交給 `PyJWKClient` 自己的機制：`cache_keys=True` 快取 JWKS 文件 300 秒，快取裡找不到
      的 `kid` 觸發一次無條件重新抓取，剛輪替的新 signing key 立刻可驗證，不必等 TTL 過期）
- [x] 匿名 `/api/discover/import` 改為要求登入，或先寫入候選審核佇列，不直接寫入全域 catalog。
      （改為要求登入：`user: AuthUser = Depends(get_current_user)`，未帶合法 token 在任何抓取／
      DB 寫入前回 401；`POST /discover` 仍公開，只回傳候選清單不寫入。前端 `Discover.importFeed()`
      未登入時導向 `/login?redirect=/discover`，同既有 `subscribeExisting()` 的模式。見
      `docs/SECURITY.md` #30）
- [ ] 瀏覽器擴充若提供一般使用者使用，改採 PKCE 登入與個人訂閱，不保存 Admin API Key。
- [ ] 檢查 public client 僅使用 publishable key，任何 frontend／extension 都不得含 `service_role` 或 secret key。
- [x] 為登入後的 user-scoped API 加上跨使用者資料隔離測試。
      （`backend/tests/test_me_isolation.py`：`routers/me.py` 全部 14 個端點、
      `routers/opml.py` 的 `export_opml` 與 `import_opml`，各自以兩個不同使用者的 JWT
      呼叫兩次，斷言送進 Supabase 查詢／RPC 的 `user_id` 對應各自呼叫者，兩次呼叫的值
      不同——防的是「硬寫 user_id」「跨請求沿用前一個使用者」這類會讓應用層隔離悄悄失效
      的回歸，因為這些資料表的 RLS 對本專案自己的查詢不生效（service_role client 繞過
      RLS，見 Phase 0 與 docs/FEATURES.md 第 5 節），唯一的隔離機制就是每個 handler 自己
      記得用 `user.id` 過濾。`import_opml` 額外把 `validate_fetch_url`／`fetch_and_parse`
      兩個外部呼叫換成假函式，讓匯入真的走到 `user_feeds` 寫入那一步才能斷言）

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
5. [~] 標籤／語言篩選、偏好設定與匯入後分類。
      （標籤／語言篩選與偏好設定 UI 已完成，見上方「標籤、語言與偏好設定」；匯入後自動分類尚未開始）
6. [ ] 回饋持久化、可解釋推薦權重與推薦理由。
7. [~] Feed 完整文章分頁、全文搜尋與資料夾管理。
      （Feed 完整文章分頁已完成，見上方「Feed 完整文章列表」；全文搜尋與資料夾管理尚未開始）
8. [ ] 查詢效能、migration lock、JWT/JWKS、extension auth 與 CI hardening。

## 完成定義

每個功能完成前至少需要：

- [ ] API contract 與權限模型已明確。
- [ ] RLS／跨使用者隔離測試通過。
- [ ] backend tests、frontend tests、production build 通過。
- [ ] 新 migration 在乾淨資料庫與現有資料升級路徑都驗證。
- [ ] 部署設定、必要環境變數與操作文件已更新。
- [ ] 手動走過「發現 → 訂閱 → 閱讀 → 回饋」受影響的完整流程。
