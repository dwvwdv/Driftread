# 功能與 API 清單（當前狀態）

本檔描述 Driftread **目前** 有哪些功能、API、資料表與限制。
變更歷程請看 [CHANGELOG.md](CHANGELOG.md)，安全設計看 [SECURITY.md](SECURITY.md)。

---

## 1. 功能總覽

| 功能 | 狀態 | 說明 | 相關程式 |
|------|------|------|----------|
| 信息源瀏覽 | ✅ | 分頁、分類 / tag 篩選、關鍵字搜尋 | `routers/feeds.py`、`components/feed-list` |
| 文章預覽與全文閱讀 | ✅ | feed 詳情帶文章列表；閱讀頁顯示快取的全文 | `routers/articles.py`、`components/article-reader` |
| 猜你喜歡 | ✅ | 以訂閱與偏好推出未訂閱的 feed，以「喜歡 / 跳過」按鈕表態（無滑動手勢），另有「再推薦一批」 | `routers/recommendations.py`、`components/recommendations` |
| 用戶系統 | ⚠ | Supabase Auth（email / password）；JWT 由後端驗證。**前端的 Supabase 設定是 build 時編進 bundle 的，官方 GHCR image 帶空值 → 需自建 image 才可用**（見第 4 節） | `auth.py`、`services/auth.ts` |
| 訂閱 / 已讀 / 收藏 / 稍後讀 | ✅ | 均為 per-user，資料表開 RLS owner policy | `routers/me.py`、`components/my-feeds`、`components/bookmarks` |
| Auto-discover | ✅ | 貼任意網址自動找出 RSS / Atom feed（使用者觸發） | `services/feed_discovery.py`、`routers/discover.py` |
| **主動發現新的 RSS 源** | ✅ | 平台自己挖：文章外連 / blogroll / 目錄頁 → 待探測佇列 → 探測 → 候選審核佇列 → 入庫。**預設關閉**（`FEED_DISCOVERY_ENABLED`）| `services/link_harvest.py`、`directory_sources.py`、`discovery_probe.py`、`discovery_candidates.py`、`discovery.py`、`robots.py` |
| OPML 匯入 / 匯出 | ✅ | 與 Feedly / Inoreader 互通 | `routers/opml.py` |
| **自動定期抓取** | ✅ | `next_fetch_at` 驅動的到期佇列 + 自適應間隔 + conditional GET；由獨立 `worker` 容器輪詢 | `services/feed_refresh.py`、`worker.py` |
| Feed 健康度與自動封存 | ✅ | 連續失敗計數 + 原因；達 10 次自動封存 | `services/feed_refresh.py` |
| 後台手動匯入（JSON） | ✅ | `POST /api/admin/feeds`，需 admin key。只寫 metadata，排入到期佇列由排程器抓文章 | `routers/admin.py`、`components/admin` |
| 開放 API 匯入 | ✅ | `POST /api/admin/feeds/from-url`，供外部腳本 / 擴充使用 | `routers/admin.py` |
| 瀏覽器擴充 | ✅ | 任何網站一鍵加入 feed（Chromium 系，開發版載入） | `extension/` |

## 2. 推薦邏輯（猜你喜歡）

`GET /api/recommendations` 的評分方式（`_score_candidates`）：

| 訊號 | 加權 |
|------|------|
| category 命中 | +3 |
| 每個命中的 tag | +2 |
| language 命中 | +1 |

訊號來源：已登入者取其訂閱的 feed（category / tags / language）與 `user_preferences`；
未登入者只吃 query 帶的 `liked`。已訂閱與 `liked` / `disliked` 內的 feed 會被排除。
`liked` / `disliked` 各上限 50 筆，`limit` 為 1–50（預設 10）。

## 2b. 自動抓取管道

匯入後不需要任何人介入，feed 會持續進新文章。

**到期佇列**：`select_due_feeds()` 取 `archived_at IS NULL AND next_fetch_at <= now()`，
依 `next_fetch_at` 排序，命中 partial index `feeds_next_fetch_at_idx`。已封存的源永不入列。

**自適應間隔**（`next_interval()`，純函式）：

| 本輪結果 | 下次間隔 |
|----------|----------|
| 有新文章 | `max(下限, 現值 / 2)` |
| 無新文章（304 或 count 差值為 0）| `min(上限, max(現值, 下限) × 2)` |
| 抓取失敗 | 同上（加倍退避）|

新 feed 起始 60 分，夾在 `FEED_REFRESH_MIN/MAX_INTERVAL_MINUTES`（預設 15 分 ~ 24 小時）之間。

**conditional GET**：`feeds.etag` / `feeds.last_modified` 存上一輪的 validator，下一輪帶
`If-None-Match` / `If-Modified-Since`。304 視為「成功但無變更」——更新 `last_fetched_at`
與排程，不動文章、**不計失敗**。304 未回帶 validator 時保留舊值，否則後續每輪都會退回無條件抓取。

> 「有沒有新文章」一律以 **upsert 前後的 article count 差值** 判定，不用 `upsert_articles()`
> 的回傳值 —— 後者是被碰到的列數（含更新既有列），而 RSS feed 每輪都回同一批最新文章，
> 拿它判斷會讓每個源都被視為永遠活躍、退避失效。

**執行者**：`worker` 容器每 `FEED_REFRESH_TICK_SECONDS` 掃一輪；也可用
`POST /api/admin/feeds/refresh-due` 手動踢，或由外部排程器驅動（此時可不啟用 worker）。
單輪並發由 `asyncio.Semaphore` 限制，逐 feed 隔離例外 —— 一個壞源不會拖垮整批。

## 2c. 主動發現管道

> ⚠ **預設關閉。** 這是唯一一個會主動對「沒人要求過的第三方網站」發出請求的迴圈，
> 啟用前請先讀 [SECURITY.md 的自主發現章節](SECURITY.md)，特別是 DNS rebinding 那一節。
> `FEED_DISCOVERY_ENABLED=false` 時 worker 不跑這個迴圈，且 `POST /api/admin/discovery/run`
> 直接回 **503** —— 這個 flag 是真的 kill switch，與只管 worker 的 `FEED_REFRESH_ENABLED` 不同。

四個階段，由 `services/discovery.py::run_cycle()` 依序驅動，worker 與
`POST /api/admin/discovery/run` 共用同一個入口。

**1. 收割（harvest）**——把已知的東西挖出線索。

| 來源 | 網路請求 | 開關 |
|------|----------|------|
| 既有 feed 的文章外連（讀 `articles.content`）| **零**（refresh worker 早就抓好快取了）| 隨主開關 |
| 既有 feed 的網站首頁（blogroll / 友情連結）| 每個 feed 一次 | `FEED_DISCOVERY_BLOGROLL_ENABLED`（預設關）|
| `discovery_sources` 的目錄頁 | 每個來源一次 | `FEED_DISCOVERY_DIRECTORY_ENABLED`（預設關）|

文章外連挖掘是免費的，也是這個迴圈會自我複利的原因：源越多 → 文章越多 → 外連越多
→ 候選越多 → 核准後源又更多。

抽出的網址先過 `normalize_host()`：非 http(s)、IP literal、無點 host、超長 label、
`.local` / `.internal` / `.onion` 等保留 TLD 一律在**寫進資料庫之前**就丟掉。

正規化後的 host 是**去重鍵**，但**不是要抓的位址**：待探測列另存 `origin_of()` 算出的原始
scheme 與 authority。`http://www.legacy.org/post` 會以 `legacy.org` 去重、以
`http://www.legacy.org/` 抓取 —— 不少站只服務 `www.` 而讓 apex 解析失敗，也還有少數是
http-only，重建成 `https://<host>/` 會讓它們白白重試到 `exhausted`。

接著過
denylist（社群、影音、程式碼託管、百科、電商、短網址、CDN…），比對用後綴，但**部落格
平台的子網域例外**：`substack.com` 這個 apex 被封，`someone.substack.com` 保留，因為在
那些平台上子網域「就是」站點。已知遺漏：`medium.com/feed/@user` 是有效的，但 origin
正規化到不了，所以 `medium.com` 直接封掉。

`rel="nofollow"` **刻意忽略** —— 那是排名指令不是爬取指令，而 blogroll 連結常被 nofollow，
尊重它等於丟掉最好的信號。

**目錄來源的兩種形態**（`discovery_sources.kind`）：

- `links_page`：HTML 頁，每個外部 `<a href>` 變成一個待探測 host。
- `opml`：OPML/XML，每個 `outline/@xmlUrl` 本身就是 feed URL，直接成為待探測目標。
  用 `defusedxml` 解析（同 OPML 上傳的 billion-laughs / XXE 加固）。這條路以 **URL** 去重
  而非 host，因為一個站可以合法地有多個 feed；被封鎖的 host 因此要額外明確擋掉。

> **聚合站（HN / Reddit / lobste.rs）不需要任何新程式。** 把它們的 RSS 用
> `POST /api/admin/feeds` 當成普通 feed 收進來，文章外連挖掘就會自動撿走每個被投稿的
> 網域。刻意不寫這些站的 HTML 爬蟲：結構說變就變，每個都是獨立的維護負擔。

**2. 探測（probe）**——`services/discovery_probe.py`

到期佇列取 `status='pending' AND next_probe_at <= now()`，依
`referring_feed_count DESC, next_probe_at` 排序（命中 `discovery_targets_due_idx`）。
排序把證據放在時間之前是刻意的：探測預算是稀缺資源，永遠先花在最多不同來源背書的 host。

順序：`validate_fetch_url()`（SSRF，最先跑，在任何網路請求與 DB 寫入之前）→ denylist 複驗
→ robots.txt → 既有的 `discover_feeds()` 四階段掃描。

robots.txt 依 RFC 9309：2xx 照解析、4xx 全允許（多數站根本沒有）、5xx 全拒絕但算可達、
傳輸錯誤則不可達。`Crawl-delay` 尊重但夾在 30 秒，否則一句 `Crawl-delay: 86400` 就能釘住
一個探測槽位一整天。

**「不准」也分兩種**，這決定了目標是終態還是重排：

| robots.txt 的情況 | 結果 | attempts |
|-------------------|------|----------|
| 解析出 `Disallow` 規則 | `blocked`，終態 | 不增加（拒絕是答案不是故障，重試只會讓我們更失禮）|
| 5xx（站暫時壞掉）| `failed`，退避重試 | +1 |
| 傳輸錯誤 / 不可達 | `failed`，退避重試 | +1 |

5xx 依 RFC 9309 確實要當成「此刻不准」，但那是站台壞了，不是站台叫我們別來 —— 若把它記成
永久排除，站台修好之後就再也不會被看一眼。

**三個對外階段都套政策**：blogroll 一跳、目錄頁抓取、以及探測。政策由
`services/crawl_policy.py::make_gate()` 產生，經 `allow_url` 傳進唯一的 fetch choke point，
對初始 URL 與每個 redirect hop 都評估。

但**denylist 只套在探測**。收割與目錄階段讀的是「我們自己選的地方」——管理員設定的目錄頁，
或已經在 catalog 裡的 feed 的首頁；而 denylist 回答的是另一個問題：「這個 host 值得被收錄成
部落格嗎？」。把它套上去會永久擋死預設目錄清單（那份清單就放在 github.com）。這兩個階段仍
然過 URL 形狀檢查與 robots，而它們**抽出來的連結**照常過 denylist。

禮貌延遲也涵蓋「robots.txt → 首頁」這一段：探測剛對同一個 host 抓過 robots.txt，若不算進去，
這兩個請求會背靠背送出。

**「空結果代表什麼」**：`discover_feeds()` 預設吞掉自己的抓取錯誤後回 `[]`，所以光看回傳值，
「這站沒有 feed」和「這站掛了」長得一模一樣 —— 前者無止境重試是純浪費，後者不重試會丟掉真正
的源。探測階段因此傳 `raise_on_fetch_error=True`，直接拿到目標自己的抓取結果：

| `discover_feeds` 的結果 | 意思 | 目標狀態 |
|--------------------------|------|----------|
| 拋例外 | 這個頁面抓不到 | `failed`，退避重試 |
| `[]` | 抓到了，但它沒有宣告 feed | `done`，終態，不重試 |
| 有候選 | 找到 feed | `done`，記錄候選 |

> 早期版本是拿 robots.txt 那次抓取當可達性探針（反正幾毫秒前才打過同一個 host）。那不成立：
> `/robots.txt` 回 404 只證明伺服器回應了**那個**請求，不代表目標頁抓得到，於是首頁正在逾時
> 的站也會被判成「沒有 feed」而永不重看。改用目標自己的結果之後，這個判斷也不再依賴 robots
> 是否啟用。

**待探測目標的狀態機**（`discovery_targets.status`）：

| 狀態 | 意義 |
|------|------|
| `pending` | 可探測（或等 `next_probe_at`）——唯一會被探測的狀態 |
| `done` | 已探測且可達；`feeds_found` 可能是 0 |
| `blocked` | robots.txt 有 `Disallow` 規則，或命中 denylist |
| `exhausted` | 連續不可達達 `FEED_DISCOVERY_PROBE_MAX_ATTEMPTS` 次 |
| `rejected` | 管理員封鎖，**永不重新排入** |

重試退避從 `FEED_DISCOVERY_PROBE_RETRY_HOURS` 起逐次加倍，上限 30 天。

**3. 候選審核（candidates）**——`services/discovery_candidates.py`

探測到的 feed URL 進 `discovery_candidates`，帶來源 host 與「幾個不同的既有源連到這裡」。
`status` 為 `pending` / `approved` / `rejected` / `imported`。

**被拒的候選永不重新提議** —— 而它「本來一定會」回來，因為同一批文章每輪都還連著同一個
feed。所以寫入路徑從不 upsert 覆蓋 `status`，而是先查再插，既有列的更新一律加上
`.eq("status","pending")` 圍欄。拒絕時可勾選一併封鎖整個 host：這會把**該 host 的每一列**都設為 `rejected`（不只是眼前那一列 ——
待探測列以 URL 唯一，一個 host 可能有好幾列），而收割會跳過任何已存在於 `discovery_targets`
的 host，那就是封鎖永久生效的機制，不需要第五張表。

`approved` 是真實的中間態：先記審核結果再寫 `feeds`，所以 `feeds` 寫入失敗時審核決定不會
遺失，由下一輪的 `promote_approved()` 補上。

**自動入庫**：`FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS` 設為 N 時，被 N 個以上不同既有源
連到的候選跳過人工審核直接入庫。**預設 0 = 永不自動**。migration 006 的 trigger 會持續更新
pending 候選的 `referring_feed_count`，所以這個門檻對「事後累積的證據」有反應，而不是看發現
當時的快照。

**4. 入庫（promote）**

只寫 `feeds` 的 metadata 並設 `next_fetch_at = now()`，**絕不 inline 抓文章** —— 首次抓文由
既有的 refresh worker 負責（同 `POST /admin/feeds` 的做法）。`feeds.title` 是 NOT NULL 而候選
標題可能為空，此時退回我們自己算出的 host，而不是遠端文字。

**已存在的 feed 只連結、不覆寫。** 候選在佇列裡等待期間，同一個 URL 可能已被手動、由擴充
或由 OPML 匯入。此時入庫只把候選標成 `imported` 並指向既有列，不動它的標題、網站、分類與
標籤 —— 否則一次無害的重複核准就會用抓來的值和空白預設覆蓋掉人工整理過的資料。

核准時選的分類與標籤會**一併寫進候選列**（`approved_category` / `approved_tags`），而不是只
留在當下的請求裡。這樣萬一寫 `feeds` 失敗，下一輪的 `promote_approved()` 能重現管理員原本的
選擇，而不是把源匿名地無分類匯入。

第三方文字（`title` / `website_url`）寫入前過 `sanitize_text()` / `sanitize_http_url()`：
移除控制字元、零寬字元與 bidi override，並強制 http(s) scheme。詳見 SECURITY.md。

## 3. API 端點

所有端點前綴 `/api`。認證欄位：**公開** = 無需認證；**用戶** = 需 `Authorization: Bearer <Supabase JWT>`；**Admin** = 需 `X-API-Key`。

### Feeds / Articles（公開）

| Method | 路徑 | 說明 |
|--------|------|------|
| GET | `/feeds` | 列表；支援分頁、`category`、`tag`（單一 tag，對 `tags` 陣列做 contains）、`search`（`search` 上限 200 字） |
| GET | `/feeds/categories` | 所有分類 |
| GET | `/feeds/{feed_id}` | feed 詳情 + 文章（不存在回 404） |
| GET | `/feeds/{feed_id}/articles` | 該 feed 的文章分頁 |
| GET | `/articles/{article_id}` | 單篇文章全文（不存在回 404） |
| GET | `/recommendations` | 猜你喜歡（帶 token 時個人化） |
| GET | `/health` | 健康檢查（compose healthcheck 使用） |

### Discover（公開，有 rate limit）

| Method | 路徑 | 說明 |
|--------|------|------|
| POST | `/discover` | 從網址找出候選 feed（不落庫） |
| POST | `/discover/import` | 抓取並匯入指定 feed URL |

兩者各自獨立配額：**每個 client IP 每端點 20 requests / 60 秒**，超過回 `429` 並帶 `Retry-After`。
`url` / `feed_url` 上限 2048 字。

### 個人化（需用戶 token）

| Method | 路徑 | 說明 |
|--------|------|------|
| GET | `/me/feeds` | 我的訂閱 |
| POST | `/me/feeds/{feed_id}` | 訂閱（204） |
| DELETE | `/me/feeds/{feed_id}` | 取消訂閱（204） |
| POST | `/me/articles/{article_id}/read` | 標記已讀（204） |
| GET | `/me/reads` | 已讀的 article id 列表 |
| GET | `/me/bookmarks` | 收藏 / 稍後讀（依 `bookmark_type`） |
| POST | `/me/bookmarks` | 加入收藏（204） |
| DELETE | `/me/bookmarks/{article_id}` | 移除收藏（204） |
| GET | `/me/preferences` | 取得偏好 |
| PUT | `/me/preferences` | 更新偏好（`preferred_categories` / `preferred_languages` 各上限 50 筆） |
| POST | `/me/import/opml` | 匯入 OPML（檔案上限 5 MiB、單檔最多處理 200 個 outline） |
| GET | `/me/export/opml` | 匯出 OPML |

### Admin（需 `X-API-Key`）

| Method | 路徑 | 說明 |
|--------|------|------|
| GET | `/admin/feeds` | 分頁列出**全部** feed（含已封存），依 `next_fetch_at` 排序。`archived` 可選 true / false 過濾；`page_size` 預設 50、上限 200 |
| POST | `/admin/feeds` | 批次匯入 feed（JSON）。只寫 metadata 並設 `next_fetch_at = now()` |
| POST | `/admin/feeds/from-url` | 從網址匯入單一 feed（開放 API 入口）。同上，文章交給排程器 |
| POST | `/admin/feeds/{feed_id}/refresh` | 重抓該 feed，更新文章、健康度與排程 |
| POST | `/admin/feeds/refresh-due` | 手動跑一輪到期佇列。`limit` 1–500、`max_concurrency` 1–20，未指定時吃 env 預設 |
| PATCH | `/admin/feeds/{feed_id}/archive` | 封存 |
| PATCH | `/admin/feeds/{feed_id}/unarchive` | 解除封存 |
| GET | `/admin/feeds/unhealthy` | 健康度低於門檻的 feed，差的排前面。`threshold` 預設 50、`limit` 預設 200（上限 1000） |
| GET | `/admin/feeds/archived` | 已封存的 feed，`limit` 預設 200（上限 1000） |

### Admin — 主動發現（需 `X-API-Key`）

| Method | 路徑 | 說明 |
|--------|------|------|
| POST | `/admin/discovery/targets` | 種子網址進待探測佇列。`urls` 1–500 筆、每筆上限 2048 字。單筆失敗進 `rejected` 陣列而不讓整批失敗；已 `rejected` 的 host **不會**被重新排入 |
| GET | `/admin/discovery/targets` | 分頁列出待探測佇列，可用 `status` 過濾，依證據數排序。`page_size` 上限 200 |
| PATCH | `/admin/discovery/targets/{id}/block` | 永久封鎖該 host（設為 `rejected`）|
| GET | `/admin/discovery/candidates` | 候選審核佇列。`status` 預設 `pending`、`min_referrers` 可過濾證據數 |
| POST | `/admin/discovery/candidates/{id}/approve` | 核准入庫，可帶 `category` / `tags`。只寫 metadata，文章交給排程器。已被拒的候選回 **409** |
| POST | `/admin/discovery/candidates/{id}/reject` | 拒絕，可帶 `note`；`block_host=true` 一併封鎖整個網域 |
| GET | `/admin/discovery/sources` | 目錄來源清單 |
| POST | `/admin/discovery/sources` | 新增目錄來源（`kind` 為 `links_page` 或 `opml`），依 `url` upsert |
| PATCH | `/admin/discovery/sources/{id}` | 調整 `enabled` / `interval_hours` |
| POST | `/admin/discovery/sources/reload-defaults` | 從 `backend/seeds/discovery_sources.json` 重新載入預設清單（冪等，不會重設既有列的開關與間隔）|
| POST | `/admin/discovery/run` | 手動跑一輪。`harvest_limit` 1–100、`probe_limit` 1–200、`max_concurrency` 1–10、`directory_limit` 1–20，未指定時吃 env 預設。**`FEED_DISCOVERY_ENABLED=false` 時回 503** |
| GET | `/admin/discovery/stats` | 各狀態的計數 |

## 4. 前端路由

| 路徑 | 元件 |
|------|------|
| `/` | `feed-list` |
| `/feeds/:id` | `feed-detail` |
| `/articles/:id` | `article-reader` |
| `/recommendations` | `recommendations` |
| `/discover` | `discover` |
| `/login` | `login` |
| `/me/feeds` | `my-feeds` |
| `/me/bookmarks` | `bookmarks` |
| `/admin` | `admin` |
| `**` | 轉回 `/` |

前端設定在 `frontend/src/environments/`：`apiUrl` 正式為 `/api`（走 nginx 代理），
`supabaseUrl` / `supabaseAnonKey` 供瀏覽器端 Supabase Auth 使用（**anon key，不是 service_role**）。

⚠ 這三個值在 `npm run build` 時就被編進 bundle，沒有 runtime 替換機制。repo 內
`supabaseUrl` / `supabaseAnonKey` 是空字串，GHCR 的官方 frontend image 因此也是空的，
`AuthService.isConfigured()` 會回 `false`，所有需要登入的功能都不會運作 —— 必須填值後自建
image。見 [README 的說明](../README.md#-前端-supabase-設定是-build-時決定的)。

## 5. 資料表

由 `backend/migrations/*.sql` 定義，後端啟動時 `migrate.py` 自動套用（以 `_migrations` 表追蹤）。

| 表 | 來源 migration | 內容 |
|----|----------------|------|
| `feeds` | 001 + 003 + 005 + 006 | RSS 源本體（title / url / category / tags / language / archived_at…）＋健康度欄位 `consecutive_failures`、`last_failure_at`、`last_failure_reason`、`health_score`＋排程欄位 `next_fetch_at`、`fetch_interval_minutes`、`etag`、`last_modified`＋收割游標 `last_harvested_at`、`next_harvest_at` |
| `articles` | 001 + 005 | 快取文章，`feed_id` 外鍵 cascade delete。唯一鍵在 005 從全域 `UNIQUE(url)` 改為 `UNIQUE(feed_id, url)` |
| `user_feeds` | 002 | 訂閱關係 |
| `user_article_reads` | 002 | 已讀回報 |
| `user_bookmarks` | 002 | 收藏 / 稍後讀（`bookmark_type` 區分） |
| `user_preferences` | 002 | `preferred_categories` / `preferred_languages` |
| `discovery_targets` | 006 | 待探測佇列。`url` UNIQUE（不是 host —— 一個站可以有多個 feed），`host` 另建索引供去重；`status` / `attempts` / `next_probe_at` / `referring_feed_count` |
| `discovery_target_referrers` | 006 | `(target_id, feed_id)` 主鍵的分帳表，讓「幾個**不同**的既有源連到這裡」精確且重複收割時冪等 |
| `discovery_candidates` | 006 | 候選審核佇列。`feed_url` UNIQUE 就是「被拒的永不重新提議」的機制；`approved_category` / `approved_tags` 讓核准決定在寫 `feeds` 失敗時不會遺失 |
| `discovery_sources` | 006 | 管理員維護的目錄頁清單（`links_page` / `opml`）|
| `_migrations` | `migrate.py` 自建 | 已套用的 migration 檔名 |

RLS：四張 `user_*` 表為 owner-only policy（002）；`feeds` / `articles` 開 RLS 並給 public read policy（004）。
**四張 `discovery_*` 表開 RLS 但刻意不建任何 policy（006）** —— 連 SELECT 都沒有，所以 anon 與
authenticated 看不到任何列也寫不進去，只有 service_role 能繞過。這與 004 對公開 catalog 開 public
read 是相反的刻意選擇：誰連到誰是 scraping 敏感資料，anon key 洩漏不該能列舉待探測佇列。
後端使用 **service_role key** 繞過 RLS 進行寫入，權限改由 JWT 驗證與 `ADMIN_API_KEY` 控管。

索引：`feeds(category)`、`feeds(archived_at)`、`feeds(health_score)`、
`feeds(next_fetch_at) WHERE archived_at IS NULL`（partial，供到期佇列）、
`feeds(next_harvest_at) WHERE archived_at IS NULL`（partial，供收割佇列）、`articles(feed_id)`、
`articles(published_at DESC)`、`user_feeds(user_id)`、`user_feeds(feed_id)`、
`user_article_reads(user_id)`、`user_bookmarks(user_id, bookmark_type)`、
`discovery_targets(referring_feed_count DESC, next_probe_at) WHERE status='pending'`（partial，供探測佇列）、
`discovery_targets(host)`、`discovery_targets(status)`、
`discovery_candidates(referring_feed_count DESC, discovered_at DESC) WHERE status='pending'`（partial，供審核佇列）、
`discovery_candidates(status)`、`discovery_candidates(target_id)`、
`discovery_target_referrers(feed_id)`、`discovery_sources(next_harvest_at) WHERE enabled`。

清理待探測佇列（終態列可安全刪除 —— 候選是 `ON DELETE SET NULL`，審核歷史不受影響）：

```sql
DELETE FROM discovery_targets
 WHERE status IN ('done','exhausted','blocked')
   AND updated_at < now() - interval '180 days';
```

## 6. 生效中的限制與門檻

| 項目 | 值 | 位置 |
|------|-----|------|
| Request body | 6 MiB（超過回 413） | `main.py::MAX_REQUEST_BODY_BYTES` |
| 對外抓取回應上限 | 5 MiB（feed 與 discover 的 HTML 共用同一個上限） | `services/feed_discovery.py::MAX_FEED_BYTES` |
| OPML 檔案 | 5 MiB | `routers/opml.py::MAX_OPML_BYTES` |
| OPML outline 數 | 200 | `routers/opml.py::MAX_OPML_OUTLINES` |
| Rate limit | 20 req / 60s / IP / 端點 | `rate_limit.py` |
| Rate limiter 追蹤上限 | 10,000 clients | `rate_limit.py::MAX_TRACKED_CLIENTS` |
| 自動封存門檻 | 連續失敗 10 次 | `services/feed_refresh.py::AUTO_ARCHIVE_FAILURE_THRESHOLD`（`routers/admin.py` re-export）|
| 排程掃描間隔 | 300 秒（`FEED_REFRESH_TICK_SECONDS`）| `services/feed_refresh.py::tick_seconds()` |
| 單輪處理上限 | 50 個 feed（`FEED_REFRESH_BATCH_SIZE`，端點可指定 1–500）| `services/feed_refresh.py::batch_size()` |
| 單輪並發抓取上限 | 5（`FEED_REFRESH_CONCURRENCY`，端點可指定 1–20）| `services/feed_refresh.py::concurrency()` |
| 個別 feed 抓取間隔 | 15 分 ~ 1440 分，起始 60 分 | `services/feed_refresh.py::next_interval()` |
| Admin feed 列表 `page_size` | 預設 50、上限 200 | `routers/admin.py::list_all_feeds` |
| URL 欄位長度 | 2048 | `models.py`（discover 請求） |
| `search` 長度 | 200 | `routers/feeds.py` |
| `page_size`（feeds） | 預設 20、上限 100 | `routers/feeds.py` |
| Admin 列表 `limit` | 預設 200、上限 1000 | `routers/admin.py` |
| 對外抓取 User-Agent | `DISCOVERY_USER_AGENT`，預設 `Driftread/1.0` | `services/feed_discovery.py::user_agent()` |
| 偏好清單長度 | 各 50 | `models.py::UserPreferences` |
| Article upsert 批次 | 200 / 批 | `services/articles.py` |
| **主動發現總開關** | **關**（`FEED_DISCOVERY_ENABLED`）| `services/discovery_config.py` |
| 發現週期間隔 | 900 秒（`FEED_DISCOVERY_TICK_SECONDS`）| `discovery_config.py::tick_seconds()` |
| 單輪收割 feed 數 | 10（`..._HARVEST_BATCH_SIZE`）| `discovery_config.py` |
| 每個 feed 掃描文章數 | 20（`..._HARVEST_ARTICLES`）| `discovery_config.py` |
| 同一 feed 再收割間隔 | 168 小時（`..._HARVEST_INTERVAL_HOURS`）| `discovery_config.py` |
| 單一 feed 每輪貢獻網域上限 | 200（`..._HARVEST_MAX_LINKS_PER_FEED`）| `discovery_config.py` |
| 單篇文件解析的 anchor 上限 | 500 | `services/link_harvest.py::MAX_ANCHORS_PER_DOC` |
| 單篇文章實際解析的 HTML | 512 KiB | `services/link_harvest.py::MAX_HARVEST_HTML_BYTES` |
| blogroll / 目錄階段 | **各自預設關**（`..._BLOGROLL_ENABLED` / `..._DIRECTORY_ENABLED`）| `discovery_config.py` |
| 單輪目錄來源數 | 3（`..._DIRECTORY_BATCH_SIZE`）| `discovery_config.py` |
| 單一 OPML 目錄取用 feed 數 | 500 | `services/directory_sources.py::MAX_OPML_FEEDS_PER_SOURCE` |
| 單輪探測目標數 / 並發 | 20 / 3（`..._PROBE_BATCH_SIZE` / `..._PROBE_CONCURRENCY`）| `discovery_config.py` |
| 探測放棄門檻 / 重試基礎間隔 | 3 次 / 24 小時（逐次加倍，上限 30 天）| `discovery_probe.py::MAX_RETRY_HOURS` |
| 同站請求間隔 | 2 秒（`..._HOST_DELAY_SECONDS`），robots 的 `Crawl-delay` 更大時以它為準 | `discovery_config.py` |
| `Crawl-delay` 上限 | 30 秒 | `services/robots.py::MAX_CRAWL_DELAY_SECONDS` |
| robots.txt 大小 / 快取 | 512 KiB / 1 小時、2000 origin LRU | `services/robots.py` |
| 待探測佇列上限 | 50,000 個 pending（`..._MAX_FRONTIER_SIZE`）| `discovery_config.py` |
| 自動入庫門檻 | **0 = 關閉**（`..._AUTO_PROMOTE_MIN_REFERRERS`）| `discovery_config.py` |
| 候選標題長度 | 200 | `services/discovery_candidates.py::MAX_TITLE_LEN` |
| 種子網址批次 | 1–500 筆 | `models.py::SeedTargetsRequest` |

## 7. 技術棧與依賴

| 層 | 內容 |
|----|------|
| Frontend | Angular 21（Material + CDK）、`@supabase/supabase-js`、nginx 提供靜態檔與 `/api/` 代理 |
| Backend | FastAPI、pydantic v2、httpx、supabase-py、pyjwt、beautifulsoup4、defusedxml、psycopg2-binary、uvicorn |
| DB | Supabase Cloud（PostgreSQL + Auth） |
| 測試 | pytest + pytest-asyncio，`backend/tests/`（23 個測試檔、474 個測試） |
| 部署 | GHCR image + docker-compose（`api` / `worker` / `frontend`；worker 與 api 共用同一個 image，只換 `command`），前端接外部 `web_network` 供反向代理 |

`worker` 容器跑兩個獨立迴圈（refresh 與 discovery），共用同一個 event loop 與同一個 stop
event，各有自己的開關與 tick。兩者皆停用時 worker 記一行 log 後 exit 0（`restart: on-failure`
因此讓它維持停機）；某個迴圈死掉則回非零讓 compose 重啟。
