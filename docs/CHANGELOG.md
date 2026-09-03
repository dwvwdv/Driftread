# 變更紀錄（PR 逐筆）

本檔以 PR 為單位記錄 Driftread 至今的所有變更，依合併時間排序。
「當前狀態」請看 [FEATURES.md](FEATURES.md)；安全加固細節看 [SECURITY.md](SECURITY.md)。

> 註：`master` 是本專案的預設分支（CI 同時接受 `main` / `master`）。

---

## 階段一：專案骨架與 CI（PR #1–#4，2026-05-07）

| PR | 標題 | 內容 |
|----|------|------|
| #1 | Add CI/CD pipelines and deployment configuration | 建立 `.github/workflows/backend.yml`（Python 3.12 + pytest）與 `frontend.yml`（Node 22 + Angular build），path filter 只在對應目錄改動時觸發。當時部署目標是 Cloudflare Workers / Pages（`wrangler.jsonc`）。首版 README。 |
| #2 | Initial project setup: Driftread RSS reader platform | 專案初始骨架。Backend：`rss_parser.py`（RSS 2.0 + Atom、content/Dublin Core namespace、日期解析）、routers `feeds` / `articles` / `admin` / `recommendations`、`models.py`、`database.py`（Supabase client）、pytest 基礎。Frontend（Angular 21）：`feed-list` / `feed-detail` / `article-reader` / `recommendations` / `admin` / `nav` 元件與對應 service。 |
| #3 | Add support for master branch in CI/CD workflows | 兩個 workflow 的 trigger 與部署條件加入 `master`。 |
| #4 | Upgrade Cloudflare Wrangler to v4 in CI/CD workflows | Wrangler 升 v4，部署指令改依賴各目錄設定檔。 |

## 階段二：離開 Cloudflare，改走容器（PR #5，2026-05-07）

| PR | 標題 | 內容 |
|----|------|------|
| #5 | Migrate from Cloudflare to Docker Compose deployment | **架構轉向。** 新增 `docker-compose.yml`（api + frontend）、`backend/Dockerfile`（Python 3.12-slim + uvicorn）、`frontend/Dockerfile`（Node build → nginx alpine）、`frontend/nginx.conf`（`/api/` 反向代理 + SPA fallback）。移除 workflow 中的 Cloudflare 部署步驟。Python import 由 `backend.*` 改為同層 module import，pytest `pythonpath` 對應調整。新增 `.env.example`。 |

## 階段三：Supabase 自架 → 雲端（PR #6–#8，2026-05-13 ~ 05-16）

| PR | 標題 | 內容 |
|----|------|------|
| #6 | Add self-hosted Supabase Docker stack (dev + prod) | 在 compose 內加入完整自架 Supabase：PostgreSQL / PostgREST / GoTrue / Kong / pg-meta / Studio，`supabase/volumes/api/kong.yml` 宣告式路由與 anon / service_role key-auth，根層 `.env.example`（`POSTGRES_PASSWORD` / `JWT_SECRET` / `ANON_KEY` / `SERVICE_ROLE_KEY` / `ADMIN_API_KEY`）。 |
| #7 | Rename gen_keys.py to gen_env.py | `scripts/gen_keys.py` → `scripts/gen_env.py`（產生的是整份 `.env`，不只 key）。 |
| #8 | Migrate from self-hosted Supabase to Supabase Cloud | **架構再轉向，也是目前的形態。** 移除自架 db / rest / auth / meta / kong / studio 與相關 volume。`gen_env.py` 只再產生 `ADMIN_API_KEY`；`SUPABASE_URL` / `SUPABASE_KEY` 改為手動從 Supabase Dashboard 填入。frontend 服務接上外部 `web_network` 供反向代理使用。 |

## 階段四：CI 與本地開發體驗（PR #9–#10，2026-05-16）

| PR | 標題 | 內容 |
|----|------|------|
| #9 | Enable manual workflow dispatch for CI pipelines | 兩個 workflow 加上 `workflow_dispatch`，可從 Actions UI 手動觸發。 |
| #10 | Configure API endpoint for local development | 前端 `apiUrl` 由硬寫的 `https://driftread-api.workers.dev/api` 改為相對路徑 `/api`；compose 補上 api 的 network 設定。 |

## 階段五：資料庫 schema 與自動 migration（PR #11–#12，2026-05-16）

| PR | 標題 | 內容 |
|----|------|------|
| #11 | Add database schema and auto-migration system | `backend/migrations/001_initial_schema.sql`：`feeds` / `articles` 表、常用查詢索引、`feeds.updated_at` 觸發器。`backend/migrate.py`：以 `_migrations` 表追蹤已套用檔名，依序套用 `migrations/*.sql`，交易內執行、失敗 rollback，缺 `DATABASE_URL` 時只警告不中斷。接進 FastAPI `lifespan`，啟動時自動跑。新增 `psycopg2-binary`。 |
| #12 | Claude/setup docker compose api f0 w6 a | PR 描述是空的，但確實有內容（`git diff 3ecea3c^1 3ecea3c`）：移除殘留的 Cloudflare 設定檔 `backend/wrangler.jsonc`、`frontend/wrangler.jsonc`；`scripts/gen_env.py` 的 missing 檢查加入 `DATABASE_URL`；`CLAUDE.md` 寫入「環境變數必須同步三處」的規則。 |

## 階段六：使用者功能大版（PR #13，2026-05-16）

| PR | 標題 | 內容 |
|----|------|------|
| #13 | Add user authentication, feed discovery, and personal feed management | **功能面最大的一次。** 詳見下表。 |

PR #13 拆解：

- **Backend**
  - `auth.py`：以 Supabase JWT secret 驗證 bearer token，提供 `AuthUser` 與 optional user 依賴注入。
  - `services/feed_discovery.py`：從任意網址自動找出 RSS / Atom feed（解析 `<link>`、嘗試常見路徑、驗證內容），並帶 SSRF 防護（阻擋私網 / loopback）。
  - `routers/me.py`：訂閱（列表 / 訂閱 / 取消）、已讀回報與列表、收藏與稍後讀。
  - `routers/opml.py`：OPML 匯入 / 匯出。
  - `routers/discover.py`：`POST /discover`、`POST /discover/import`。
  - `routers/admin.py`：feed 健康度——記錄連續失敗次數與原因，達門檻自動封存。
  - Migration `002_user_features.sql`（`user_feeds` / `user_article_reads` / `user_bookmarks` / `user_preferences` + RLS owner policy）、`003_feed_health.sql`（健康度欄位）、`004_enable_rls_on_public_tables.sql`（`feeds` / `articles` 開 RLS + public read policy）。
- **Frontend**：`services/auth.ts`（Supabase client + session）、`login`、`discover`、`my-feeds`（含 OPML 匯入匯出）、`bookmarks`（收藏 / 稍後讀分頁）、`article-reader` 加上標記已讀與收藏、nav 依登入狀態變化、`MeService` / `DiscoverService`、auth interceptor 自動附上 JWT。
- **瀏覽器擴充**（`extension/`）：popup 顯示當前頁面偵測到的 feed 並一鍵匯入、content script 掃描 `<link>` feed 宣告、background 更新徽章。

## 階段七：安全與正確性加固（PR #14–#21，2026-07-23 ~ 07-30）

每天一筆，來源是同一個「持續改善專案」的排程任務。完整說明見 [SECURITY.md](SECURITY.md)。

| PR | 日期 | 標題 | 一句話 |
|----|------|------|--------|
| #14 | 07-23 | harden PostgREST filter building and batch article upserts | 修 PostgREST filter 注入（`search` / `preferred_categories`），並把 N+1 article upsert 改為批次。 |
| #15 | 07-24 | close SSRF gap in feed-fetching endpoints, add missing JWT secret | SSRF 守門只裝在 `/discover`，其他會真的抓取並落庫的端點都沒過守門；compose 漏傳 `SUPABASE_JWT_SECRET`。 |
| #16 | 07-25 | cap response size in fetch_and_parse, add URL length and admin list limits | `fetch_and_parse` 無上限緩衝回應 → 改共用 `fetch_with_cap`（串流 + 5 MiB 上限 + redirect 重驗）。 |
| #17 | 07-26 | cap request body size to close a memory-exhaustion vector | 全站沒有 request body 上限 → 新增 `MaxBodySizeMiddleware`（6 MiB，413）。 |
| #18 | 07-27 | rate-limit the public feed-discovery endpoints | 全站沒有 rate limit → 新增 `rate_limit.py`（每 IP 每端點 20 req / 60s）。 |
| #19 | 07-28 | harden OPML import against XML entity-expansion attacks | OPML 上傳改用 `defusedxml`，阻擋 billion laughs / XXE。 |
| #20 | 07-29 | use maybe_single() instead of single() for not-found lookups | 5 處 `.single()` 讓「查不到」回 500 而非 404，改 `.maybe_single()`。 |
| #21 | 07-30 | harden remote feed XML parsing against entity-expansion attacks | feed 本身的 XML 解析也改用 `defusedxml`（比 #19 更嚴重：`/discover` 無需登入）。 |

## 階段八：建立專案文件（PR #22，2026-07-30）

| PR | 標題 | 內容 |
|----|------|------|
| #22 | docs: 建立功能與變更紀錄文件，同步更新 README | 新增本檔與 `FEATURES.md`、`SECURITY.md`；README 從 Cloudflare / 自架 Supabase 的舊架構校正為實際的 GHCR + compose 流程。 |

review 過程中順帶修掉三個文件對不上實作、以及實作本身的缺口：

- `DISCOVERY_USER_AGENT` 只有 discover 路徑吃得到，`rss_parser.fetch_and_parse()` 硬寫 `Driftread/1.0`，導致 feed 匯入 / refresh / OPML 匯入全部忽略此設定。`feed_discovery._user_agent()` 改為公開的 `user_agent()` 並在兩處共用，加上回歸測試 `test_fetch_and_parse_uses_configured_user_agent`。
- `DISCOVERY_USER_AGENT` 也沒被 compose 傳進容器（`.env` 設了無效）→ 補進 `docker-compose.yml` 與 `.env.example`。
- compose 的 `frontend` 服務只有 `image:` 沒有 `build:`，`docker compose build frontend` 建不出東西 → 補上 `build: ./frontend`，讓「自建可用 image」這條路徑真的可行。
- **SSRF：discover 從 HTML 抽出的候選連結沒過守門**（詳見 [SECURITY.md](SECURITY.md) #22）。`fetch_with_cap()` 改為連初始 URL 一起驗證。
- `MAX_HTML_BYTES` 是從未被引用的死碼，刪除並把文件改為實際的單一 5 MiB 上限。
- README 的本地開發指令少了 `--env-file`，照著做起得來但每個請求都會因為 `SUPABASE_URL` 為空而失敗（後端只讀 `os.getenv()`，不自己載 dotenv）。
- README 的本地開發指令在同一個 shell block 裡先 `cd backend` 再 `cd frontend`，第二個會找不到目錄 → 拆成兩個 block。
- 記錄了一個**未修**的已知限制：SSRF 守門可被 DNS rebinding 繞過（見 [SECURITY.md](SECURITY.md) #22）。
- README 的本地開發段落直接引用 `../.env`，卻沒說它被 gitignore、全新 clone 需要先跑 `gen_env.py` → 補上前置步驟。
- FEATURES 誤稱推薦頁「可左右滑動表態」——那句話來自 PR #2 的描述，實際元件只有「喜歡 / 跳過」按鈕，沒有任何 pointer / touch / drag 處理 → 改為描述實際的按鈕。
- 本檔原先把 #12 記為「無獨立內容」（因為它的 PR 描述是空的），實際 diff 有三項改動 → 已補齊。

## 階段九：自動抓取管道（PR #23，2026-07-30）

| PR | 標題 | 內容 |
|----|------|------|
| #23 | feat: 自動定期抓取 feed，補上缺失的獲取管道 | 在此之前**只有匯入管道、沒有持續抓取管道**：整個 repo 沒有任何排程器（apscheduler / celery / cron / pg_cron 皆無命中），唯一讓 feed 再抓一次的方法是人工對 `POST /api/admin/feeds/{id}/refresh` 打一筆、一次一個，`last_fetched_at` 與 `health_score` 形同裝飾。本 PR 新增 `next_fetch_at` 驅動的到期佇列、自適應退避、conditional GET，以及獨立的 worker 容器。 |

### 新增

- **`backend/services/feed_refresh.py`** —— 核心。把原本內嵌在 `routers/admin.py` 的健康度／失敗記帳抽出成 `refresh_one()`，讓 HTTP 端點與排程器共用同一套語意；`refresh_due()` 以 `asyncio.Semaphore` 限制單輪並發、逐 feed 隔離例外；`next_interval()` 為純函式的自適應退避（有新文章間隔對半、沒動靜或失敗則加倍，夾在 15 分 ~ 24 小時之間）。
- **`backend/worker.py`** —— 獨立排程器行程，compose 新增 `worker` service，沿用同一個 GHCR image 只換 `command`。選擇獨立容器而非 in-process background task：後者綁在 api 生命週期，api 多副本會重複抓同一個 feed。收到 SIGTERM 會跑完本輪再收工；單輪失敗只記 log 不讓行程死掉（compose 的 restart 沒有 backoff，crash-loop 會反覆打 Supabase）。
- **`migrations/005_feed_scheduling.sql`** —— `feeds` 新增 `etag` / `last_modified` / `fetch_interval_minutes` / `next_fetch_at`，並建 partial index `feeds_next_fetch_at_idx (next_fetch_at) WHERE archived_at IS NULL` 供到期查詢。
- **conditional GET** —— `fetch_with_cap_response()`（`services/feed_discovery.py`）可帶 per-request header 並回傳 status 與 validator；`fetch_and_parse_conditional()`（`rss_parser.py`）帶 `If-None-Match` / `If-Modified-Since`，304 視為「成功但無變更」：更新 `last_fetched_at` 與排程、不動文章、不計失敗。原有的 `fetch_with_cap()` / `fetch_and_parse()` 簽章不變（改為薄包裝），既有 caller 與測試零改動。
- **`POST /api/admin/feeds/refresh-due`**（手動踢一輪到期佇列）與 **`GET /api/admin/feeds`**（分頁列出全部 feed，含已封存）。後者補上先前的空缺：公開 `GET /feeds` 濾掉封存、另兩個 admin 列表各有健康度／封存過濾，沒有任何端點能列舉整份目錄，外部排程器因此無法列舉待辦。

### 修正

- **三條匯入路徑不抓文章**：`POST /admin/feeds`、`POST /admin/feeds/from-url`、OPML 匯入只寫 metadata，留下 0 篇文章與 `last_fetched_at = NULL`，除非有人手動 refresh。三者改為在 upsert 時寫入 `next_fetch_at = now()`，交給排程器做第一次抓取。（bulk 端點內嵌抓取會 timeout，排程是正確解法。）
- **`article_count` 記的是「本批 upsert 數」而非累計**（`discover.py`、`admin.py`）。改以 upsert 前後各查一次 count，後者寫進 `article_count`。
- **`articles.url` 是全域 UNIQUE 而非 per-feed**（`001_initial_schema.sql`）。兩個 feed 轉載同一篇會互搶同一列，且 `on_conflict="url"` 會把該列的 `feed_id` 改成最後 refresh 的那個 feed。改為 `UNIQUE(feed_id, url)`，`services/articles.py` 的 `on_conflict` 同步改為 `feed_id,url`（兩處必須同 PR，否則 upsert 會指向不存在的約束）。

### review 過程中修掉的實作錯誤

- 304 的分支原本放在 `resp.is_redirect` 之後，但 **httpx 的 `is_redirect` 對任何 3xx 都回 True**（它只看 status code，判斷真正的轉址要用 `has_redirect_location`），因此 304 會先被當成轉址、再因為（本來就不該有的）缺少 `Location` 而被拒。改為把 304 提到 `is_redirect` 之前處理，並補上 `test_fetch_and_parse_conditional_handles_304` 釘住。
- 退避的「有沒有新文章」原先想用 `upsert_articles()` 的回傳值判斷，但它回的是**被碰到的列數（含更新既有列）**。RSS feed 每次都回同一批最新文章，這個數字幾乎恆等於 item 數，會讓每個 feed 都被判定為永遠活躍、退避完全失效。改用 count 差值，並以 `test_refresh_one_treats_zero_count_delta_as_unchanged` 釘住這條路徑。
- worker 的 restart policy 原本比照 `api` 寫成 `unless-stopped`，但該策略帶有 `always` 語意（只多尊重手動 stop），**不看 exit code**。`FEED_REFRESH_ENABLED=false` 時 `worker.py` 記一行 log 後 exit 0，會被反覆重啟成 start/exit 迴圈，而非文件描述的「保持停止」。改為 `on-failure`：只在非零退出時重啟，崩潰仍會自動恢復，刻意停用則維持停止。（由 PR #23 的自動 review 指出。）

### 環境變數

新增六個（皆有預設值、不填也能跑），依 `CLAUDE.md` 要求同步 `.env.example`、`docker-compose.yml`、`scripts/gen_env.py` 三處：
`FEED_REFRESH_ENABLED`（`true`）、`FEED_REFRESH_TICK_SECONDS`（`300`）、`FEED_REFRESH_BATCH_SIZE`（`50`）、`FEED_REFRESH_CONCURRENCY`（`5`）、`FEED_REFRESH_MIN_INTERVAL_MINUTES`（`15`）、`FEED_REFRESH_MAX_INTERVAL_MINUTES`（`1440`）。

### 測試

新增 `tests/test_feed_refresh.py`（29 個）與 `tests/test_worker.py`（9 個），擴充 `test_admin.py`、`test_rss_parser.py`。測試總數 64 → 117。

### 不在此 PR 範圍

種子源目錄（`CLAUDE.md` 的「大量 RSS 源資料庫」）仍未實作，DB 依然是空的出貨。那是內容收集問題而非管道問題，留作獨立 PR；此 PR 完成後，種子匯入只要打 `POST /admin/feeds` 就會被排程器自動接手第一次抓取。

## 階段十：主動發現新的 RSS 源（PR #24，2026-07-30）

| PR | 標題 | 內容 |
|----|------|------|
| #24 | feat: 主動發現新的 RSS 源 | 在此之前「發現」只有被動的一種：使用者貼一個網址到 `POST /api/discover`，`discover_feeds()` 才去探測。平台自己沒有任何找新源的機制，而階段九結尾記著「種子源目錄仍未實作，DB 依然是空的出貨」——空的 catalog 讓核心功能「猜你喜歡」無從推薦。本 PR 補上自主發現迴圈：收割 → 探測 → 候選審核 → 入庫，接著由既有的 refresh worker 抓文章。 |

### 新增

- **`migrations/006_feed_discovery.sql`** —— `feeds` 加收割游標兩欄與對應 partial index；四張新表：`discovery_targets`（待探測佇列）、`discovery_target_referrers`（`(target_id, feed_id)` 主鍵的分帳表）、`discovery_candidates`（審核佇列）、`discovery_sources`（目錄頁清單）。四張表開 RLS 且**刻意不建任何 policy**，連 SELECT 都沒有 —— 誰連到誰是 scraping 敏感資料，anon key 洩漏不該能列舉。
- **`services/link_harvest.py`** —— 文章外連挖掘（**零網路請求**，讀的是 refresh worker 早就快取好的 `articles.content`）與 blogroll 一跳抓取。含 host 正規化、`site_key`、denylist、anchor 抽取。
- **`services/directory_sources.py`** —— 目錄頁來源，`links_page`（HTML 連結頁）與 `opml`（`outline/@xmlUrl` 直接成為待探測的 feed URL）兩種形態，附一份預設清單 `backend/seeds/discovery_sources.json`。
- **`services/robots.py`** —— RFC 9309 語義、`Crawl-delay`（夾 30 秒）、有界 TTL/LRU cache。抓取走既有的 choke point，**絕不呼叫 `RobotFileParser.read()`**（它會自己 `urlopen`，繞過 SSRF gate、redirect 處理與位元組上限），已加測試釘住。
- **`services/discovery_probe.py`** —— 到期佇列（依證據數而非時間排序：探測預算是稀缺資源）、robots 與 denylist 閘門、重試退避、`exhausted` 終態。
- **`services/discovery_candidates.py`** —— 候選記錄、消毒、核准／拒絕、入庫、門檻自動入庫。
- **`services/discovery.py`** —— worker 與 admin endpoint 共用的 `run_cycle()`，就是 `refresh_due()` 今天的角色。
- **`routers/admin_discovery.py`** —— 12 個 `X-API-Key` 端點（種子網址、佇列檢視與封鎖、候選審核、目錄來源 CRUD、手動跑一輪、統計）。
- **`backend/env_utils.py`** —— 把 `feed_refresh._env_int` 抽成共用的 `env_int` / `env_float` / `env_flag`。
- **前端** —— `/admin` 加四個區塊：發現總覽與立即執行、候選審核、種子網址、目錄來源。

### 設計決策

- **聚合站（HN / Reddit / lobste.rs）不寫爬蟲**。把它們的 RSS 用既有的 `POST /admin/feeds` 當普通 feed 收進來，文章外連挖掘就會自動撿走每個被投稿的網域 —— 一行程式都不用寫。刻意不做這些站的 HTML 爬蟲：結構說變就變，每個都是獨立的維護負擔。
- **`FEED_DISCOVERY_ENABLED` 預設 false**，與 `FEED_REFRESH_ENABLED=true` 相反。refresh 只碰運維人員自己匯入的源；發現迴圈會去探測沒人要求過的第三方，既有部署拉 `:latest` 不該默默變成一台爬蟲。blogroll 與目錄兩個對外階段各自另開。
- **`POST /admin/discovery/run` 在停用時回 503**，刻意與 `/admin/feeds/refresh-due` 不同。對主動探測第三方的爬蟲，「已停用」必須真的是停用，否則那是站方寫信來時無法背書的說法。
- **`allow_url` 政策 hook 掛在唯一的 fetch choke point**，對初始 URL 與每個 redirect hop 都評估，順序在 `validate_fetch_url()` 之後。這同時關掉一條既有的繞過：denylist 只驗我們收割到的 host，但 choke point 會跟隨 5 次 redirect，所以 `blog.example.com` 可以 302 到任何被封的站（SECURITY.md #24 第 4 點）。

### review 過程中修掉的實作錯誤

- **denylist 把部落格平台的子網域一起吃掉了**。第一版無條件用後綴比對，於是 `substack.com` 這個 apex 被封的同時，`someone.substack.com` 也被封 —— 而那正是最值得探測的一類 host（在那些平台上子網域「就是」站點）。改為：完全比對一律封鎖（擋 apex），但平台子網域只認完全比對、不套後綴。由 `test_platform_apex_denied_but_subdomains_survive` 抓到。
- **OPML 路徑會讓被拒的 host 換個 URL 走回來**。待探測佇列以 URL 唯一（一個站可以合法地有多個 feed），所以「同 host 只探一次」這條規則在 OPML 路徑上不成立，被管理員封鎖的 host 只要出現在某份目錄清單的不同 URL 就會重新入列。`HostIndex` 因此多帶 `blocked_hosts` 與 `target_urls` 兩個集合，OPML 路徑明確擋掉。
- **`auto_promote_min_referrers()` 差點被 `_env_int` 的 `< 1 → 用預設` 規則吃掉**。`0` 在這個參數是有意義的「永不自動入庫」，照抄 `FEED_REFRESH_*` 的守則會把明確的關閉打回預設值、**默默打開自動入庫**。`env_int` 因此多一個 `minimum` 參數，並用兩個測試釘住（含對非零預設值的斷言）。
- **收割文章若照 `published_at` 排序會系統性挖錯東西**。該欄位可為 NULL，而 Postgres `DESC` 預設 `NULLS FIRST`，用它取「最近 20 篇」實際上會優先拿到沒有日期的文章。改用 `NOT NULL` 的 `fetched_at`。

### 自動 review（Codex）指出後修掉的問題

- **爬取政策只掛在探測階段**（P1）。`blogroll` 與目錄頁兩個階段的 `allow_url` 留在預設的 `None`，於是 `FEED_DISCOVERY_RESPECT_ROBOTS=true` 對它們形同虛設 —— 明明是本 PR 自己剛寫進 SECURITY.md 規則 9 的那種錯（政策要掛在唯一的 choke point，不要靠各 call site 記得）。政策抽成 `services/crawl_policy.py::make_gate()`，由 `run_cycle()` 建立一次後傳給三個階段。
- **入庫會覆寫既有 feed 的 metadata**（P1）。候選在佇列裡等的期間，同一個 URL 可能已被手動匯入；無條件 upsert 會把人工整理過的標題、分類、標籤換成抓來的值與空白預設。改為先查再寫，已存在就只連結不覆寫。
- **robots.txt 的 5xx 被當成永久排除**（P2）。RFC 9309 說 5xx 要當「此刻不准」，但那是站台壞了不是站台叫我們別來；記成 `blocked` 終態的話，站台修好後就再也不會被看一眼。`RobotsDecision` 加 `transient` 區分「解析出的 Disallow」與「5xx / 不可達」，後者走退避重試。
- **重建 `https://<正規化 host>/` 會丟掉能用的位址**（P2）。`normalize_host()` 去掉 `www.` 對去重是對的、對抓取是錯的：不少站只服務 `www.` 而 apex 解析失敗，也還有少數 http-only。新增 `origin_of()` 保留原始 scheme 與 authority 當抓取位址，正規化 host 只當去重鍵。
- **核准時選的分類與標籤在重試時會遺失**（P2）。`promote_approved()` 不帶參數呼叫，所以寫 `feeds` 失敗後的重試會把源無分類匯入。改為把選擇一起寫進候選列（`approved_category` / `approved_tags`，migration 006 尚未發布故直接補欄位）。

第二輪：

- **上一輪的修法自己造成的迴歸**（P2）。把政策 gate 接上收割階段之後，denylist 也跟著套到了「目錄頁本身」—— 而預設目錄清單就放在 `github.com`，於是每一個 shipped default 都會以「Blocked by crawl policy」失敗並無限重排。denylist 回答的是「這個 host 值得被收錄成部落格嗎」，那對「我們要從哪裡讀清單」是錯的問題。`make_gate()` 加 `apply_denylist` 參數，收割與目錄階段關掉它（仍過 URL 形狀檢查與 robots），探測維持全套；抽出來的連結照常過 denylist。已加測試直接對 `seeds/discovery_sources.json` 的每個 URL 斷言 gate 放行。
- **目標抓取失敗被當成「這站沒有 feed」**（P1）。robots.txt 回 404 只證明伺服器回應了**那個**請求，不代表目標頁抓得到；首頁正在逾時的站因此會被記成 `done` 終態而永不重看。`discover_feeds()` 加 `raise_on_fetch_error`（預設 False，公開端點行為不變），探測階段改拿目標自己的抓取結果來判斷。這也順帶消掉了「關掉 robots 會失去重試精確度」那個副作用 —— 判斷不再依賴 robots。
- **`block_host` 只擋掉一列**（P1）。待探測列以 URL 唯一，所以 OPML 目錄可以在同一個 host 留下好幾列；只拒絕候選的母列會讓兄弟列繼續是 `pending`、繼續被聯繫、繼續提議 feed。改為依 host 拒絕全部，`PATCH /targets/{id}/block` 也同步。
- **robots.txt 與首頁之間沒有禮貌延遲**（P2）。`delay_seconds` 原本只睡在 `_validate_feed()` 裡，所以探測剛抓完 robots.txt 就緊接著抓首頁，宣稱的 per-host 間隔沒有涵蓋這一段。改為初始請求之前也睡。

### 環境變數

新增 16 個 `FEED_DISCOVERY_*`（皆有預設值、不填也能跑），依 `CLAUDE.md` 要求同步 `.env.example`、`docker-compose.yml`（`api` 與 `worker` 兩個區塊）、`scripts/gen_env.py` 三處。

附帶修掉一個既有漏洞：**`LOG_LEVEL`** 被 `worker.py:24` 讀取，但三處都沒有 —— 在 `.env` 設它完全沒有效果，因為 compose 根本沒往下傳。

### 測試

新增 8 個測試檔（`test_discovery_config` / `test_robots` / `test_link_harvest` / `test_directory_sources` / `test_discovery_probe` / `test_discovery_candidates` / `test_discovery_cycle` / `test_admin_discovery`）與共用的 in-memory `tests/discovery_fakes.py`（會實際套用 filter 而非只記錄 op chain，讓測試能斷言結果狀態）。擴充 `test_feed_discovery.py` 與 `test_worker.py` —— 兩者的既有測試**原封不動**通過（diff 只有新增行）。測試總數 117 → 474。

migration 另外在真的 PostgreSQL 16 上跑過（起一個暫時 instance、補上 Supabase 的 `auth.users` / `auth.uid()` 與三個角色），驗證了：六個 migration 依序套用成功；006 單獨重跑乾淨（DO-guard 有效）；四張新表的 RLS 是「已啟用且零 policy」；partial index 的定義與述詞正確；以及 in-memory fake 驗證不了的 trigger 語意 —— 邊數重算而非遞增（重複收割不膨脹）、pending 候選跟著證據走而已審核的凍結、刪 feed 會 cascade 並讓計數下降、清理終態目標不會毀掉審核歷史（`ON DELETE SET NULL`）。

### 不在此 PR 範圍

- **pin-and-connect SSRF 加固**。DNS rebinding 這個從 #22 起就記錄但未修的繞過，在自主迴圈下嚴重度明顯上升：待探測佇列是從第三方文章 HTML 填的，只要讓一個連結出現在任何被收割 feed 的任何文章裡，就能讓迴圈依自己的排程、無人看管地重複去抓那個 host。**建議在任何環境把 `FEED_DISCOVERY_ENABLED` 設為 true 之前先落地**（SECURITY.md #24 第 2 點）。
- `routers/discover.py:41` 把遠端 HTML 抽出的 URL 直接餵進 `.in_("url", feed_urls)`，屬 #14 同類的暴露，本次未改。
- 前端 `article-reader.html` 的 `a.content` 用 `[innerHTML]`（既有、面積大得多）。
- **001 與 002 的 `CREATE TRIGGER` / `CREATE POLICY` 沒有存在性防護**。本 PR 在真的 PostgreSQL 16 上跑過驗證（見下），006 單獨重跑完全乾淨，但把 `_migrations` 整個清空後重跑會在 **001** 就炸掉（`trigger "feeds_updated_at" ... already exists`）。005 的註解其實已經推導出這個道理才替 `ADD CONSTRAINT` 加了 DO-guard，只是沒回頭補 001/002。實務上不會踩到（`_migrations` 只增不減），但手動修補過資料庫的人會在開機時被擋下。留作獨立的小 PR。

## 階段十一：DNS rebinding pin-and-connect 與 discover.py 的 filter 收尾（PR #25，2026-07-30）

同一個「持續改善專案」排程任務，補完 #24 明確列為「不在此 PR 範圍」的兩項。完整說明見 [SECURITY.md](SECURITY.md) 的對應章節。

- **pin-and-connect**（#22 的已知限制，#24 第 2 點稱其優先度應提高）。`validate_fetch_url()` 與實際連線各自獨立呼叫 `socket.getaddrinfo()`，中間沒有任何東西把兩次解析釘在一起 —— 短 TTL 或多筆 A 記錄的 DNS 答案可以讓驗證那次回公開 IP、連線那次回 `169.254.169.254` 之類的內網位址。新增 `services/feed_discovery.py::PinnedTransport`（包住 `httpx.AsyncHTTPTransport`）與 `_resolve_pinned_ips()`：在**傳輸層**單次解析、驗證每一筆位址、把連線目標換成解析出的 IP，同時保留原始 hostname 給 `Host` 標頭與 TLS SNI（`extensions["sni_hostname"]`）。透過新的 `ssrf_safe_client()` 工廠函式接上全部 6 個 `httpx.AsyncClient` 建構點（`feed_discovery.discover_feeds`、`rss_parser` 的兩個 fetch 函式、`robots._fetch`、`directory_sources._fetch`、`link_harvest._fetch_blogroll`），不逐一手動修改每個 call site 是刻意的 —— SECURITY.md 規則 9 講的就是這件事。`validate_fetch_url()` / `_is_safe_host()` 完全不動，既有測試全部照舊通過；新測試直接對 `PinnedTransport.handle_async_request()` 灌假的內層 transport 斷言連線目標、`Host`、`sni_hostname`。
- **`routers/discover.py:41`**（#24 附帶記錄的既存暴露）。`feed_url` 是從遠端 HTML 的 `<link rel="alternate">` 抽出來的第三方字串，原本整批塞進 `.in_("url", feed_urls)` 一個 PostgREST filter —— 與 #14 修的 `.or_()` 注入同一類問題，只是換一個進場方式。改為逐一 `.eq("url", feed_url).maybe_single()`，與 `services/discovery_candidates.py` 既有的作法一致，第三方字串完全不進 filter 語法。
- **PR review（九輪）又抓出十二個問題，同一輪補上**（完整說明見 SECURITY.md #25）：連線池 key 被換成 IP、兩個共用同一 IP 的不同 hostname 可能撞進同一條連線因而共用了錯的 TLS session（`ssrf_safe_client()` 補上 `PinnedTransport(limits=Limits(max_keepalive_connections=0))` 關掉 keep-alive 重用）；`_extract_feed_links()` 的候選數量沒有上限，補上 `MAX_FEED_LINK_CANDIDATES = 50`；只取第一筆解析位址丟了 dual-stack fallback，`_resolve_pinned_ip()` 改名 `_resolve_pinned_ips()` 回傳全部驗證過的位址並依序重試。第二輪對這三個修法本身又抓出兩個問題：候選數上限原本用 `find_all(..., limit=50)` 限制掃描到的 `<link>` 標籤數，會在正常頁面的真正 feed 宣告前就停手（改成篩選後才 cap）；位址 fallback 原本只接 `httpx.ConnectError`，漏接了語意上平行、且更常見的 `httpx.ConnectTimeout`（改成兩個都接）。第三輪又抓出位址 fallback 沒有共用一個 deadline，攻擊者可用回傳多筆公開但打不通位址的 DNS answer 把單次 `/api/discover` 請求拖到約「位址數 × 逾時」那麼久；改用風險較低的固定上限 `MAX_PINNED_CONNECT_ATTEMPTS = 2`（而非需要正確組出 httpx 內部 timeout extension、但這個 sandbox 沒有網路能驗證的共用 deadline 寫法）。第四輪又抓出這個上限本身沒顧到位址族——resolver 若把兩筆以上 AAAA 排在唯一一筆 A 前面，兩個名額會被 IPv6 佔滿；新增純函式 `_pick_pinned_ips()` 依「先出現的族優先」交錯排列後再截斷。第五輪又抓出 `PinnedTransport` 回傳前沒有還原 `request.url`，導致 httpx 的 cookie jar 會把 Set-Cookie 歸檔到連線用的 IP 而非真正的 hostname，讓帶 cookie 才能過關的重新導向鏈失敗；改用 `try/finally` 保證回傳或拋例外前一定把 `request.url` 還原成原始 hostname。第六輪又抓出明確 `transport=` 不會讓 `httpx.AsyncClient` 跳過環境代理（`HTTP_PROXY`/`HTTPS_PROXY`）偵測，命中 mount 時可能繞過 `PinnedTransport` 走 httpx 自己組的、沒有 pinning 的 proxy transport；`ssrf_safe_client()` 補上 `trust_env=False` 讓這批 fetch 明確不依賴環境代理設定（Driftread 本來就沒有記載代理支援）。第七輪又抓出 `_resolve_pinned_ips()` 在事件迴圈裡同步呼叫阻塞的 `socket.getaddrinfo()`，公開免認證的 `/api/discover` 每個候選 / fallback path 都觸發一次，攻擊者指向回應很慢的 DNS 就能卡住整個 worker 的事件迴圈；改成 `async def`，用 `await asyncio.to_thread(...)` 丟到執行緒池（`validate_fetch_url()`/`_is_safe_host()` 有同樣的既有問題，但那是本 PR 之前就存在、呼叫點遍布全專案的既有型態，不在本次範圍）。第八輪又抓出 `asyncio.to_thread()` 本身不帶逾時，攻擊者控制、回應很慢的 DNS 伺服器可以讓一次解析卡到系統 resolver 自己的逾時（遠長於 httpx 配置的 12–15 秒）；`_resolve_pinned_ips()` 加上可選的 `timeout` 參數並包進 `asyncio.wait_for(...)`，`PinnedTransport` 從 `request.extensions["timeout"]["connect"]`（`httpx.Client.build_request()` 幫每個請求組好的值）取值傳入，重用呼叫端原本設定的連線逾時。第九輪又抓出 `asyncio.wait_for()` 逾時後，`to_thread()` 提交的阻塞查詢仍在 process 共用的預設執行緒池裡跑，沒有任何純 Python 手段能中斷它；多個並發的惡意請求可以佔滿那個共用池，連健康 feed 的解析都會排在後面逾時。改用專屬、固定大小（`DNS_RESOLVER_MAX_WORKERS = 8`）的 `concurrent.futures.ThreadPoolExecutor`，`_resolve_pinned_ips()` 改走 `loop.run_in_executor(...)` 而非 `to_thread()`，把占用範圍限制在這一個小池子裡，不波及 app 其他地方共用的預設執行緒池。

### 測試

`test_feed_discovery.py` 新增 28 個測試（`_resolve_pinned_ips` 的正常 / 多筆位址 / 拒絕私網 / 多筆位址其一為私網 / DNS 失敗 / 逾時中止 / 走專屬執行緒池與其他共用預設池的工作互不干擾、`_pick_pinned_ips` 的族交錯 / 單一族 / 位址數少於上限、`_extract_feed_links` 的候選數上限 / 真正 feed 宣告排在許多無關 `<link>` 標籤之後仍找得到、`PinnedTransport` 的連線目標與 `Host`/SNI 保留、逾時取自 request 的連線逾時 extension、回傳成功或全部位址失敗後都還原 `request.url`、位址 fallback（`ConnectError` 與 `ConnectTimeout` 各一）、位址嘗試次數上限、多筆 AAAA 後仍 fallback 到 IPv4、全部位址失敗、DNS rebind 拒絕、`ssrf_safe_client` 的預設 transport / 預設關 keep-alive / 預設關 trust_env / 可覆寫 transport / 可覆寫 trust_env、專屬 DNS 執行緒池的大小固定）；`test_discover.py` 新增 1 個測試斷言帶 PostgREST 特殊字元的 `feed_url` 只會走 `.eq()`，`.in_()` 從未被呼叫。

## 階段十二：猜你喜歡誤把 category 當硬性篩選（PR #26，2026-07-31）

同一個「持續改善專案」排程任務。這次不是安全加固，是核心功能（`GET /api/recommendations`）本身的一個行為 bug，直接違背 CLAUDE.md／FEATURES.md 記載的產品目的：「根據用戶喜好推薦**未知** RSS 源，幫助挖掘**新**資訊源」。

- **問題**：`_score_candidates()` 已經把 category 當成 +3 分的評分訊號（與 tag +2、language +1 同一套設計），但 `get_recommendations()` 在評分**之前**又對候選池下了 `query.in_("category", list(categories))` —— 等於把「加權」實作成了「篩選」。任何有訂閱紀錄或偏好設定的使用者，從此再也看不到自己已知類別以外的 feed，「猜你喜歡」名副其實地只剩下「猜你已經喜歡的」，新使用者發現全新類別的路徑被自己的訂閱紀錄堵死了。tags 與 language 從來沒有被這樣處理，這是三個訊號裡唯一不一致的一個。
- **修法（第一版）**：刪掉那行 `query.in_("category", ...)`，讓 category 回到單純的評分訊號，候選池只用 `archived_at IS NULL` 與排除清單（訂閱 / liked / disliked）過濾，其餘交給既有的 `_score_candidates()` 排序。行為與 FEATURES.md 第 2 節一直記載的評分表一致——文件本來就沒宣稱過有這道篩選，是實作跟文件對不上。
- **PR review（Codex，P1）抓出第一版修法的迴歸**：候選池查詢仍是 `query.limit(limit * 5)`、不帶 `.order()`。對超過這個上限的 catalog 而言，拿掉 category 篩選後，PostgREST 回傳的（未排序）第一批候選完全有可能一筆都不落在使用者已知的類別裡——於是個人化訊號悄悄消失，`_score_candidates()` 對著一堆零分候選排序，即使資料庫裡明明有更相關的 feed。原本的硬性篩選雖然堵死了跨類別發現，但至少保證候選池裡「有」符合已知偏好的列。
- **修法（第二版，本次落地）**：把候選池拆成兩次查詢——`preferred`（`in_("category", known)`，保證已知偏好仍能可靠進到候選池）與 `exploratory`（`not_.in_("category", known)`，保留給未知類別，維持這個 PR 原本要修的「能發現新類別」）。兩池的大小依 `_EXPLORATION_POOL_SHARE`（0.3）從 `limit * 5` 切分，合併後才進 `_score_candidates()` 排序。兩個保證同時成立：已知偏好不再可能被「排到第一批之外」而消失，也不會回到第一版之前「有訂閱就完全看不到新類別」的狀態。`tags` / `language` 不受影響——它們從來不是查詢層的篩選條件，這次的迴歸與修法只針對 category。
- **PR review（Codex）對第二版又抓出兩個問題，同一輪補上（第三版，本次落地）**：
  1. **P1 — 保留的 exploration 名額沒有活到最後一步**。第二版只在候選**池**裡保留了 exploratory 的名額，但合併後仍是整批交給 `_score_candidates()` 排序、`candidates[:limit]` 取前 N 筆。只要有其他訊號的 exploratory 候選一律是 0 分，而 preferred 候選只要命中 category 就至少 3 分——只要 catalog 夠大、preferred 池湊得滿 `limit` 筆，最終回傳的頁面就會是清一色 preferred，池子裡留的 exploratory 名額從未真正出現在使用者看到的結果裡，等於白留。修法：`_fetch_candidate_pool()` 改回傳 `(preferred, exploratory)` 兩個獨立列表而不先合併；`get_recommendations()` 對兩邊分別評分，各自依 `_EXPLORATION_SHARE` 切出名額後才組成最終的 `top`，其中一邊筆數不足時用另一邊補滿到 `limit`。
  2. **P2 — 沒有分類的 feed 進不了 exploratory 池**。`not_.in_("category", known)` 編譯成 SQL 的 `NOT IN`，而 `category IS NULL` 的列跟 `NOT IN` 比較的結果是 unknown、不是 true，所以完全篩不到——`feeds.category` 本來就可為 NULL（migration 001），主動發現入庫在候選沒被指定分類時也確實會寫 `None`，這是常態不是邊界案例。任何有 category 訊號的使用者因此永遠看不到沒分類的 feed。修法：exploratory 名額再拆成「已知的其他分類」（`not_.in_`）與「完全沒分類」（新的第三次查詢，`is_("category", "null")`）兩份，各自獨立查詢。**刻意不用 `.or_()` 把兩個條件併成一個 filter 字串**——`categories` 集合可能包含使用者自己填的 `preferred_categories`（透過 `PUT /me/preferences` 完全可控），併進 `.or_()` 會重開 SECURITY.md #14 修過的同一類 PostgREST filter 注入。
- **PR review（Codex，P2）對第三版再抓出一個問題，同一輪補上（第四版，本次落地）**：exploratory 的兩份子查詢原本各自只拿 `exploration_n` 的一半（`other_category_n` / `uncategorized_n`），這代表其中一種類型完全沒有資料時（例如 catalog 裡根本沒有無分類的 feed），另一種類型即使有充足的候選也被腰斬在一半的名額——`limit` 明明有機會湊滿卻悄悄少交付。修法：兩份子查詢各自都用**完整**的 `exploration_n` 當上限去查，取回後才 `(other_category + uncategorized)[:exploration_n]` 合併截斷，讓任一邊都能在另一邊沒資料時獨自撐滿整個 exploration 名額。
- **PR review（Codex，P2）對第四版再抓出一個問題，同一輪補上（第五版，本次落地）**：`other_category + uncategorized` 這個串接本身仍有順序偏差——`other_category`排在前面，只要它自己就湊滿 `exploration_n`（有 catalog 的常態），`[:exploration_n]` 會把排在後面的 `uncategorized` 整批砍光，等於第三版才修好的「無分類 feed 永遠看不到」以另一種方式原地復活。修法：合併後、截斷**前**先 `random.shuffle()`，讓兩種子類型不論查詢順序都有公平機會留下——**shuffle 的順序是重點**：截斷之後才 shuffle 沒有意義，救不回已經被砍掉的列。新增的測試把 `random.shuffle` monkeypatch 成確定性的「反轉」而非依賴真隨機：只有 shuffle 真的發生在截斷**之前**，原本排在尾端（`uncategorized`）的列才可能在反轉後被救回、出現在最終結果——這個測試對「shuffle 放在截斷之後」這種等價但錯誤的寫法會失敗，能精準釘住順序本身。
- **PR review（Codex，P2）對第五版再抓出一個問題，同一輪補上（第六版，本次落地）**：quota 邏輯（先取滿 preferred 名額、再取 exploratory 名額）決定的是**哪些列會上頁**，但 concatenate 出來的 `top` 順序仍是「所有 preferred 排在所有 exploratory 前面」，不是照分數排——一個命中兩個 tag 的 exploratory 候選（+4）理應排在只命中 category 的 preferred 候選（+3）前面，但原本的組法讓 preferred 永遠先出現，前端依序消費這個陣列時，畫面順序就不再反映文件記載的評分權重。修法：把逐列算分的邏輯抽成 `_score()`（`_score_candidates()` 改呼叫它，行為不變），quota 選出 `top` 之後再依 `_score()` 對這個子集合整體重新排序一次——保留 quota 決定的名額分配，只修正最終顯示順序。
- **範圍以外**：子查詢個別仍不帶 `.order()`，各自取到的仍是 PostgREST 預設順序的前 N 筆，不是各自子集合的隨機樣本——這是既有行為（在這幾版修法之前就是如此），需要 server 端 `ORDER BY random()`（RPC）才能真正解決，留給以後。

### 測試

`backend/routers/recommendations.py` 先前完全沒有測試檔案——核心功能反而是測試覆蓋率的空白，這個 bug 也是因此才留到現在。`backend/tests/test_recommendations.py`：`_score_candidates()` 的 category / tag / language 個別加分與疊加排序；無任何訊號時只送一次不帶 category 篩選的查詢；有 category 訊號時斷言候選拆成三次獨立查詢——`in_("category", ...)` 的 preferred、`not_.in_("category", ...)` 的同類排除、`is_("category", "null")` 的無分類——且三者都正確帶上 id 排除條件（釘住第三版修法）；在 preferred 候選數遠超過 `limit` 的情境下，斷言 exploratory 候選仍會出現在最終回傳的分頁裡（釘住 P1 迴歸）；新增一個會真的依 `.limit(n)` 引數截斷資料的假 query builder（先前的假物件會忽略引數，測不出「要求的筆數不夠」這類 bug），斷言其中一種 exploratory 子類型完全無資料時，另一種仍能單獨湊滿 `limit`（釘住第四版修的 P2 迴歸）；monkeypatch `random.shuffle` 成確定性反轉，斷言 `other_category` 遠多於 `exploration_n` 時，排在尾端的 `uncategorized` 候選仍能出現在最終結果裡（釘住第五版修的 P2 迴歸，且對「截斷後才 shuffle」這種錯誤順序會失敗）；一個命中多個 tag 的 exploratory 候選對上只命中 category 的 preferred 候選，斷言前者出現在回傳陣列的第一位（釘住第六版修的 P2 迴歸）；已登入使用者的訂閱會被排除；`limit` 超出 1–50 範圍回 422。測試總數 474 → 487。

沒有網路能在這個 sandbox 安裝依賴跑 `pytest`（與 #25 同樣的既有限制），改用 `python3 -m py_compile` 與 `ruff check` 驗證語法與風格，交由 CI 跑完整測試。

## 階段十三：猜你喜歡的候選池改成資料庫端真隨機抽樣（PR #27，2026-08-01）

同一個「持續改善專案」排程任務，接續 PR #26「範圍以外」記下的已知限制：候選池子查詢個別仍不帶 `.order()`，在超過抓取上限的 catalog 上永遠只拿到 PostgREST 預設順序的前 N 筆，應用層的 `random.shuffle()` 只能重排這固定的一批，永遠碰不到表裡其餘的列——早期版本一直沒做，是因為 `supabase-py` 的 query builder 沒有 `ORDER BY random()`，必須換一顆 DB function。

- **修法**：`backend/migrations/007_random_feed_sampling.sql` 新增 `sample_feed_candidates(p_excluded_ids, p_categories, p_mode, p_limit)`，`ORDER BY random() LIMIT p_limit` 一次覆蓋 `_fetch_candidate_pool()` 需要的四種候選池形狀（`unfiltered` / `in_categories` / `not_in_categories` / `uncategorized`），用同一顆 function 而非四顆幾乎重複的，靠 `p_mode` 用 `=` 比對固定字面值切換分支——這個字面值由後端程式碼決定、從不是使用者輸入拼字串，不會重開 SECURITY.md #14 修過的 PostgREST filter injection 那類洞。`routers/recommendations.py` 把原本的 `db.table("feeds")...limit(n)` 四種查詢組合，換成呼叫這顆 function 的 `db.rpc(...)`（新的 `_sample_feeds()` 輔助函式），拆分候選池 / 選 preferred vs exploratory / 事後補滿 / 最終依分數重排的既有邏輯完全不動。
- **順手清掉一處連帶變成多餘的程式碼**：`get_recommendations()` 在完全沒有任何訊號（無 category / tag / language）時走的 fallback 分支，原本會對候選池再做一次 `random.shuffle()`——這在候選池本來就是 PostgREST 預設順序時是必要的，但候選池現在改由 `sample_feed_candidates()` 以 `ORDER BY random()` 抽出，這次應用層 shuffle 已經是對已經隨機過的資料再洗一次，純粹浪費，直接移除。
- **PR review（Codex，P1）**：新函式建在 `public` schema，Postgres 對新函式預設把 `EXECUTE` 授權給 `PUBLIC`，而 PostgREST 的 `anon` / `authenticated` 角色會繼承這個授權——意味著只要持有瀏覽器就看得到的 anon key，就能繞過後端直接呼叫這顆 RPC，帶入超出 API 限制（`limit` 1–50、id/category 陣列各上限 50）的巨大 `p_limit` 或陣列，反覆觸發整張 `feeds` 表的 `ORDER BY random()` 掃描，且完全不吃 `rate_limit.py` 的配額——一個未認證的資料庫資源耗盡路徑。修法：`REVOKE EXECUTE ... FROM PUBLIC` + `GRANT EXECUTE ... TO service_role`，比照專案裡其他寫入路徑（見 004、006）一律只信任 service_role 的既有原則；並在 function 內部加 `LIMIT LEAST(p_limit, 250)` 當 defense-in-depth——即使日後哪個呼叫路徑忘記做邊界檢查，這顆 function 本身也不會被騙去掃全表。
- **PR review（Codex，P1，同一輪再抓）**：上一版只 revoke `PUBLIC`，但 Supabase Cloud 專案會額外對 `postgres` 角色設定 default privileges，讓它建立的新物件直接把 `EXECUTE` 授權給 `anon` 與 `authenticated`——這是獨立於 `PUBLIC` 繼承鏈之外的直接授權，只 revoke `PUBLIC` 救不到它，anon key 仍能直接呼叫。修法：對 `anon` 與 `authenticated` 各自明確補上 `REVOKE EXECUTE`，三個 revoke 都下完才 `GRANT ... TO service_role`。
- **PR review（Codex，P1，第三輪）**：鎖住 RPC 本身的授權只堵住「繞過後端直接呼叫」這條路，堵不住「正常打 `GET /api/recommendations` 但瘋狂重複打」這條——這條路線完全不經過 RPC 授權檢查，而 migration 007 之後這個端點每次呼叫最多對 `feeds` 做三次 `ORDER BY random()` 全表掃描（掃描 + 排序，比原本單純的 `.limit()` 貴得多）。`/discover`、`/discover/import` 早在 SECURITY.md #18 就因為同樣的理由裝了 `rate_limit`，`/recommendations` 卻一直沒有——不是這次改動造成的舊缺口，但這次改動把它的代價從「可忽略」放大到「值得立刻補」。修法：`get_recommendations()` 加上 `Depends(rate_limit("recommendations"))`，沿用既有的每 client IP 每端點 20 requests / 60 秒配額，與 `/discover` 系列共用同一套機制、各自獨立配額桶。

### 測試

`backend/tests/test_recommendations.py` 改寫既有測試的 mock 層，從模擬 `db.table("feeds")` 的 query-builder chain 改成模擬 `db.rpc("sample_feed_candidates", params)`：新增 `_sampling_rpc()` 假物件，依 `params["p_mode"]` 分派到對應的假資料集，並依 `params["p_limit"]` 真的截斷回傳筆數（取代原本會忽略引數的假 `.limit()`），讓「要求的筆數不夠」這類 bug 仍測得出來。測試涵蓋的行為（三池拆分、exploration 名額存活到最終輸出、任一子池獨自撐滿名額、shuffle 必須在截斷前發生、依分數而非 quota 來源排序、已登入使用者排除自己的訂閱、`limit` 邊界）本身不變，只是斷言的對象從 query-builder 呼叫換成 `db.rpc` 呼叫參數。新增 `test_recommendations_rate_limited_after_threshold`，仿照 `test_discover.py` 的既有寫法：連打 `DEFAULT_MAX_REQUESTS` 次都拿 200，第 `DEFAULT_MAX_REQUESTS + 1` 次應回 429 並帶 `Retry-After`。測試總數 487 → 488。

沒有網路能在這個 sandbox 安裝依賴跑 `pytest`（與 #25、#26 同樣的既有限制），改用 `python3 -m py_compile` 與 `ruff check` 驗證語法與風格，並手動逐一推演每個測試案例的資料流確認邏輯正確，交由 CI 跑完整測試套件驗證。

## 階段十四：`validate_fetch_url()` 補上 #25 第七輪明確記下、當時未修的既有問題（PR #28，2026-08-02）

同一個「持續改善專案」排程任務。完整說明見 [SECURITY.md #26](SECURITY.md)。

- **問題**：#25 第七輪把 `PinnedTransport` 新增的第二道 DNS 解析（pin-and-connect 用的那次）從同步改成非同步，並在紀錄裡明確寫下「`validate_fetch_url()`/`_is_safe_host()` 有同樣的既有問題，但… 呼叫點遍布全專案… 不在本次範圍」——那次修法沒有動到**第一道**：`validate_fetch_url()` 本身，它在每一條抓取路徑最前面（包括完全公開免認證的 `POST /api/discover`）同步呼叫 `socket.getaddrinfo()`，攻擊者指向回應很慢的 DNS 就能卡住整個 worker 的事件迴圈——與 #25 第七輪修的是同一個服務層級 DoS，只是換了個尚未補上的呼叫點。
- **修法**：`_is_safe_host()` 改成 `async def`，解析借用 `_resolve_pinned_ips()` 已在用的同一個專屬、固定大小的 `_dns_resolver_executor`，並帶上新常數 `DNS_VALIDATION_TIMEOUT_SECONDS = 10.0`（`validate_fetch_url()` 執行時通常還沒有 client/request 存在、部分呼叫端如 `routers/opml.py` 也沒有現成 `timeout` 變數可借）。`validate_fetch_url()` 隨之改成 `async def`；`fetch_with_cap_response()` 與 `discover_feeds()` 改傳入各自函式簽章上原本就有的 `timeout`，其餘 10 個呼叫端（`rss_parser.py` 兩處、`services/feed_refresh.py`、`services/discovery_probe.py`、`routers/admin.py`、`routers/admin_discovery.py` 兩處、`routers/discover.py`、`routers/opml.py`）維持預設值——全部呼叫點都已在 `async def` 函式裡，只需加上 `await`。判斷邏輯（private / loopback / link-local / multicast / reserved）與 #25 的 pin-and-connect 完全不動，這次只是把既有的第一道門從同步搬成非同步。

### 測試

`backend/tests/test_feed_discovery.py` 新增 2 個測試：`test_is_safe_host_runs_dns_resolution_off_the_event_loop`（沿用 #25 第九輪驗證阻塞行為的手法，斷言解析發生在 `pinned-dns-resolve` 執行緒、事件迴圈期間仍能完成無關的 `asyncio.to_thread()` 工作）、`test_is_safe_host_rejects_dns_timeout`（逾時回傳 `False` 而非掛住或洩漏例外）。既有的 `test_normalize_url_*` 系列改成 `async def` + `await`——`unittest.mock.patch()` 對已改成 `async def` 的目標會自動改用 `AsyncMock`，既有的 `return_value=`/`side_effect=` 不必跟著改寫。沒有網路能在這個 sandbox 安裝依賴跑 `pytest`（與 #25/#27 同樣的既有限制），改用 `python3 -m py_compile` 與 `ruff check` 驗證語法與風格，交由 CI 跑完整測試。

## 階段十五：web 端全面重設計——Offbeat 設計系統與前後台結構分離（PR #29，2026-08-03）

前端從 `ng new` 的骨架長成一套有識別度的設計系統，並把前台與後台在結構上真正拆開。

### 起點

- 全站 CSS 約 150 行。`styles.scss` 43 行裡有 38 行是 Angular CLI 原始 boilerplate——未更動的 `mat.theme()`、stock azure palette、寫死的 `color-scheme: light`，沒有任何 token 層。
- `.center` / `.error { color: red }` / `.empty` 三條規則在四個元件的 SCSS 裡逐字重複。中性色一律寫成 `rgba(0,0,0,.x)`，另有 `#f5f5f5`、`#e3f2fd`、`#d32f2f` 等硬編碼散落 8 個檔案——任何深色主題都會直接壞掉。
- `app.html` 是未刪的 344 行 Angular 起始頁（Angular logo、`Hello, {{ title() }}`），沒有被引用；`app.scss` 是空檔；`app.spec.ts` 對著那個死掉的模板斷言。`index.html` 標題還是 `DriftreadFrontend`，`lang="en"`，整個 UI 卻是繁體中文且沒有任何 CJK 字型堆疊。
- **前後台沒有分離**：14 行扁平路由，`/admin` 只是其中一個 leaf，與前台共用同一個 shell；`nav.html` 把「後台」連結永久顯示給所有訪客；`/admin` 完全沒有 guard。後台是單一頁面塞 6 個 `mat-card`，直接呼叫 `HttpClient` 並手工組 `x-api-key` header 共 12 次。

### 設計系統（Offbeat：Nord × Brutalism）

兩層 token：raw palette 只出現在 `_tokens.scss`，其餘一律走語意別名，換主題只是 `<html>` 上換一個屬性。

**關於對比度**——Nord 是為語法高亮設計的低對比色票，數個自然搭配在 UI 文字上達不到 WCAG AA。逐一量測並在檔案內記錄：

- frost3 `#5E81AC` 配 snow2 文字只有 **3.47:1**，不能當主要按鈕底。主要按鈕改用 frost1 + polar0（**6.25:1**），兩個主題共用同一組值。
- aurora_red 是中間調，在 polar0（3.06:1）與 snow2（3.53:1）上都不合格。Aurora 因此拆成兩種形式：原色供填色 / 狀態點 / 邊框（≥3:1 即可），另加每主題的 `-ink` 變體供文字。
- polar3 在 polar0 上是 2.3:1（禁用為文字），在 snow2 上卻是 **6.7:1**——禁令是跟背景綁定的，淺色模式正好是它合法的地方。

偏移陰影改用 `box-shadow: Npx Npx 0 0` 而非參考文件的 `::after` + `z-index:-1`。視覺完全相同（零模糊零擴散＝實心位移矩形），但不需定位脈絡、不會被 stacking context 裁掉——sticky header 與 drawer transform 都會破壞後者。

### 元件庫

移除 `@angular/material` 與 `@angular/animations`，只留 `@angular/cdk`（overlay / a11y）。

分界不是隨意的：「原生元素加上塗裝」的東西（button / input / textarea / select / checkbox / chip / divider）留在原生元素上，樣式放全域 recipe class；只有真的有結構或行為的才做成元件。兩個理由——放棄 Material 等於放棄它的無障礙成果，靠平台把鍵盤操作、type-ahead 與行動裝置原生選單買回來比重寫一套 combobox ARIA 可靠得多；而且 `anyComponentStyle` 是每元件 4kB，mixin 被 12 個元件 include 就是 12 份各自計費，全域 class 只算一次。

無障礙補回的重點：`ob-tabs` 實作完整 WAI-ARIA tabs（roving tabindex、左右鍵與 Home/End，且焦點必須跟著選取移動，否則焦點會留在 `tabindex="-1"` 的 tab 上導致下一次 Tab 跳出整組）；`ob-icon` 用 `@switch` 選 literal `<path>`，不經 `innerHTML`、不碰 `bypassSecurityTrust`；`ob-error` 帶 `role="alert"`——它取代的 `<p class="error">` 對輔助技術完全隱形；toast 一律同步送 `LiveAnnouncer`。行動版側欄除了 transform 位移還要 `visibility: hidden`，否則鍵盤使用者會 Tab 進一個看不見的選單。

### 前後台分離

`/admin/**` 自成子路由樹掛在獨立的 `AdminLayout` 下（側欄、更緊的間距、STRUCTURE 6），前台移除「後台」連結。`adminGuard` 只檢查金鑰是否存在，註解裡寫死它不是認證。金鑰改存 `sessionStorage`——原本存在非持久化的 signal，重新整理就消失，所以每個面板都需要手動按「載入」，改成撐得過 F5 之後那個 workaround 連同解釋它的註解一起消失。

`AdminService` 收掉 12 處手工 header，並把原本一律 `失敗：<detail>` 的錯誤處理拆開：0（沒到達後端）、403 / 422（金鑰無效或遺漏 → 清除並送回 unlock）、409（狀態衝突不是失敗）、502（遠端 feed 抓取失敗，用 warning 不用 danger）、503（設定狀態，用 info）。

`auth.interceptor` 從「無條件貼在所有請求上」收斂為只貼自家 API 且跳過 `/admin/*`。

後台拆成 5 個子頁，並接上 5 個後端早就存在但前台從未呼叫的端點：`feeds/unhealthy`（健康度只存在資料庫裡沒人看得到）、`feeds/refresh-due`、`feeds/from-url`（原本加一個來源要手寫 JSON 陣列）、`discovery/targets` 清單與 `targets/{id}/block`（探測佇列原本是唯寫的）。

### 順手修掉的既有 bug

1. **猜你喜歡在收藏第 51 個之後永久失效**——`services/recommendation.ts` 無上限 append 所有 liked/disliked，後端上限 `max_length=50`，超過就 422。越常用這個功能的人越早壞掉。
2. **OPML 匯出必定 401**——`exportOpmlUrl()` 回傳的字串被塞進 `<a [href]>`，跟著連結走是瀏覽器導覽而不是 HttpClient 請求，interceptor 看不到它、不會帶 `Authorization`，但端點需要 JWT。
3. **未設定 Supabase 時登入表單仍可送出**——原本只有警告文字，輸入框和按鈕照常可用，送出後靜默失敗。
4. **登入頁按 Enter 沒反應**——原本只有 click handler，沒有 `<form>`。
5. **已收藏的文章重新打開時星號永遠是空的**——`favorited` / `readLater` 從來沒有從伺服器初始化過。
6. **猜你喜歡快速點擊會撞 429 且毫無說明**——`next()` 在翻完 15 張時自動重抓，一分鐘內就能燒光 20 次配額。deck 改為 50（後端上限）、重抓改為顯式操作、429 讀 `Retry-After` 倒數顯示。

### 驗證

`npm run build`（production config，`strictTemplates` 與樣式預算全開）與 `npm test`（vitest，30 個測試）通過，`prettier --check` 乾淨。

另以 Playwright 驅動 **production build**（非 `ng serve`）跑 30 項檢查全數通過，並攔截所有 `/api/` 回應以精確回傳狀態碼：`/admin` 無金鑰時導向 unlock、金鑰撐過重新整理、403 清除金鑰並退回 unlock、429 顯示 `Retry-After` 倒數、503 說明 kill switch、tabs 方向鍵、焦點環可見、drawer 的 Escape 與 `aria-expanded`、三個斷點皆無水平溢出、淺色表面不是純白、文章欄寬 720px。截圖涵蓋 14 個頁面 × 深/淺 × 三個斷點。

登入相關流程在這個環境**無法端到端驗證**——`environment.ts` 的 Supabase 設定是空字串（build 時寫死），`AuthService.isConfigured()` 回 `false`。

initial bundle 從 607 kB 降到 503 kB，仍超出預設 500 kB 預算 3.31 kB。已確認原因與本次改版無關：203 kB 的 `@supabase/supabase-js` 被 `app.config.ts` 的 auth interceptor 靜態引入而進了 initial bundle。改成延遲載入可省下這 203 kB，但那會動到完全無法在此驗證的登入流程，因此不放進這個 PR。

## 階段十七：`MaxBodySizeMiddleware` 補上串流位元組計數，堵住 chunked body 繞過 6 MiB 上限的缺口（PR #32，2026-08-04）

同一個「持續改善專案」排程任務，補完 [SECURITY.md #17](SECURITY.md) 上線時就記下的已知限制：`MaxBodySizeMiddleware` 只檢查請求宣告的 `Content-Length` header，以 chunked transfer-encoding 送出、不帶 `Content-Length` 的請求完全不受這道 6 MiB 上限約束，公開免認證的 `POST /api/discover`、`POST /api/discover/import` 因此仍是無上限的記憶體耗盡向量。

- **修法**：`MaxBodySizeMiddleware.__call__` 包一層 `receive`，逐則 ASGI 訊息累加已收到的 body 位元組數，累計超過上限就把 `receive` 之後一律回傳 `http.disconnect`，並同時包一層 `send` 吞掉 app 在那之後想送出的任何回應；`self.app(...)` 結束後，`finally` 區塊用**原始、未包裝**的 `send` 送出唯一真正抵達 client 的 `413`。`Content-Length` 過大時仍走原本「讀 body 前」的早期回絕；未宣告或宣告不實的請求則由新的串流計數兜底，兩條路徑互不取代。
- **CI 這次真的抓到一個問題**：第一版實作原本是讓自訂例外從 `receive` 一路冒出到 `self.app(...)` 外層的 `try/except`，但 FastAPI 的 body 解析本身包了一層寬鬆的 `except Exception`，會把途中冒出的任何例外吞掉、轉成它自己的 `HTTPException(400)`。這個 sandbox 一直沒有網路能裝依賴跑 `pytest`，只能用 `py_compile` / `ruff` 驗證語法，這類「執行期才會現形」的框架內部行為因此一直沒被抓到；這次 GitHub Actions 的 `Test` job（有完整依賴）第一次真的跑了這個新測試，斷言 413 卻收到 400，當場失敗。改用上面「讓 `receive` 回報斷線、`send` 期間全吞掉，最後由外層自己送出唯一回應」的設計後重推，CI 轉綠。完整分析見 [SECURITY.md #27](SECURITY.md)。
- **測試**：`backend/tests/test_main.py` 新增 `test_oversized_chunked_body_without_content_length_rejected`，用產生器當請求內容讓 `httpx` 不送出 `Content-Length`，斷言 413、請求確實沒有該 header、且 route 邏輯（`mock_db.table`）從未被觸發——這是這次唯一一段實際在裝有完整依賴的環境（CI）跑過、而非只靠推理驗證的變更。

## 階段十六：補齊 001 / 002 / 004 的 `CREATE TRIGGER` / `CREATE POLICY` 存在性防護（PR #31，2026-08-03）

同一個「持續改善專案」排程任務，接續階段十「不在此 PR 範圍」記下的已知限制：`001_initial_schema.sql` 與 `002_user_features.sql` 的 `CREATE TRIGGER` / `CREATE POLICY` 語句沒有 `IF NOT EXISTS`，把 `_migrations` 整個清空後重跑會在 **001** 就因為「trigger 已存在」而中止——`main.py` 的 lifespan 在服務請求前呼叫 `run_migrations()`，中止的 migration 會讓容器開機失敗。005 其實已經替 `ADD CONSTRAINT` 補過同類 DO-guard，006 的四張新表也全套 guard，只是沒回頭補 001/002。

- **範圍比原先記錄的稍大**：檢查全部 7 個 migration 檔後發現 `004_enable_rls_on_public_tables.sql` 的兩個 `CREATE POLICY`（`feeds_public_read` / `articles_public_read`）同樣沒有防護。只修 001/002 沒有意義——修完之後從頭重跑仍會在 004 中止，等於沒解決「`_migrations` 被清空後可以安全重跑到底」這個實際目標，因此一併補上。003/005/006/007 本來就已是 `IF NOT EXISTS` / `DO`-guard / 純 `CREATE OR REPLACE FUNCTION`，不需要改動。
- **修法**：`CREATE TRIGGER` 比照 006 的既有寫法，用 `DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = ... AND tgrelid = '<table>'::regclass) THEN CREATE TRIGGER ... END IF; END $$;` 包住（001 的 `feeds_updated_at`、002 的 `user_preferences_updated_at`）。`CREATE POLICY` 用同樣的 `DO` 結構改查 `pg_policies`（依 `schemaname` / `tablename` / `policyname` 判斷），包住 002 的四個 owner policy 與 004 的兩個 public-read policy。判斷邏輯與被包住的 DDL 本身完全不動，純粹加上存在性檢查。
- **驗證**：這個 sandbox 剛好裝有本機 `postgresql-16` 且 `psql` / `sudo` 可用，因此沒有停在「改完只能靠推理」——實際起了一個暫時 cluster、建好 `anon` / `authenticated` / `service_role` 三個角色與 `auth.users` / `auth.uid()`，依序套用全部 7 個 migration 檔（成功），接著清空 `_migrations` 並從 001 重新整個跑一遍：修改前的版本會如預期在 001 的 `CREATE TRIGGER feeds_updated_at` 那行炸掉（`trigger "feeds_updated_at" for relation "feeds" already exists`），精確重現了階段十記錄的既知限制；修改後的版本 7 個檔案全數重跑成功，且 `pg_trigger` / `pg_policies` 查詢確認 `feeds_updated_at`、`user_preferences_updated_at` 兩個 trigger 與六個 policy 都仍然只有一份、沒有重複建立。過程沒有動到 `backend/tests/`——現有測試套件裡沒有任何測試對這幾個 migration 檔的原始內容做斷言，也沒有能連真實 PostgreSQL 的既有 migration 測試（`migrate.py` 需要 `DATABASE_URL` 才會執行，pytest 套件走的是 mock 掉 Supabase client 的路徑）。

## 階段十七：文章全文顯示成 HTML 原始碼（PR #32，2026-08-05）

使用者回報在 `/articles/:id` 讀文章時整頁都是 `<p>` `<a href="…">` 這類標籤字面值，貼了一篇阮一峰《科技爱好者周刊》第 406 期作為範例。

### 根因

不在渲染層。`article-reader.html` 的主要分支一直是 `<div class="prose" [innerHTML]="a.content">`，會正常渲染；問題是**那個分支根本沒被走到**。

`rss_parser.py` 的 RSS 解析把 `<description>` 原封不動塞進 `summary`，`content` 只從 `content:encoded` 取。阮一峰的 feed（以及非常多 feed）整篇文章就放在 `<description>` 裡、完全不送 `content:encoded`——於是 `content` 是 NULL，reader 落到 `@else if (a.summary)` 這條 **`{{ }}` 插值**的後備分支，Angular 依約把那串 HTML 逐字轉義輸出，標籤就上了畫面。`components/bookmarks` 的預覽行是同一個 `{{ article.summary }}`，症狀相同。

順帶找到第二條會壞掉的路徑：`_text()` 只讀 `el.text`，也就是第一個子元素之前的文字。Atom 的 `type="xhtml"` content 是**真的子元素**而不是轉義字串，`.text` 只有子元素前的空白，所以那類 feed 的 `content` 一律被截成空字串——症狀是文章顯示「沒有快取內容」。

### 修法

**`backend/rss_parser.py`**——把 `content`（HTML）與 `summary`（純文字）的職責分清楚：

- `_inner_html()` 取代 content/description 欄位上的 `_text()`。CDATA／轉義的 HTML 仍然原樣從 `.text` 取出不動；有子元素時（Atom xhtml）改用 `_serialize()` 遞迴輸出，剝掉命名空間前綴、void element 不補結束標籤——輸出必須是瀏覽器認得的 HTML，不是 `ET.tostring()` 那種 `<ns0:p xmlns:ns0="…">` 的 XML round-trip。
- 沒有 `content:encoded`／Atom `<content>` 而 description／summary 本身是 markup 時，把它升格為 `content`。判定用的 `_TAG_RE` 刻意保守——`<` 後面必須接字母或 `/`，所以 `if x < 3 and y > 2` 這種純文字摘要不會被誤判成 markup 而升格。
- `summary` 一律經過 `_plain_text()`：先整段丟掉 `<script>` / `<style>`（那是原始碼不是文章），再把 block 標籤換成空格、inline 標籤直接刪掉，最後才解 entity。**不做截斷**——`summary` 是純 blurb 的 feed 仍然需要完整內容當 reader 的後備。

block／inline 分開處理是為了中文：一律換成空格會讓 `<p>這裡記錄<a>開源</a>。</p>` 變成「這裡記錄 開源 。」，讀起來像打錯字。entity 放在剝標籤**之後**解，是為了讓上游雙重轉義殘留的 `&lt;p&gt;` 變成看得見的文字，而不是被重新當成標籤刪掉。

**`backend/backfill.py`**——解析器只修得到之後再 upsert 的資料列，已經滑出 feed 視窗的舊文章永遠不會被再寫一次，所以歷史資料要回填。**回填直接呼叫解析器本身**（`_looks_like_markup()` / `_plain_text()`），不在 SQL 裡重寫一份規則；在 `main.py` 的 lifespan 於 migration 之後執行，記在同一張 `_migrations` 表。這個設計是走了冤枉路才定下來的，理由見下面第七輪。

**前端**——後端修完之後 `summary` 就是純文字了，但這兩處仍加了防禦，因為回填跑之前、或任何未來又混進 markup 的情況下畫面不該再出現裸標籤：

- 新增 `shared/html.ts`（`looksLikeHtml()` / `stripHtml()`），規則與後端逐條對齊，純字串處理不碰 DOM。
- `article-reader` 的 summary 後備改成先判斷 `summaryIsHtml()`：是 markup 才走 `[innerHTML]`（一樣經 DomSanitizer，不用 `bypassSecurityTrust`），否則維持 `{{ }}`。**不無條件用 `[innerHTML]`**——純文字摘要裡的 `if x < 3` 會被 sanitizer 當成標籤開頭吞掉後半句。
- `bookmarks` 的預覽行改吃預先算好的 `rows()`，`stripHtml()` 一份清單只跑一次；列表列是單行預覽，殘留 markup 應該變成文字而不是版面。

### 驗證

- `pytest`：548 passed（523 → 548，新增 19 個解析器測試與 6 個回填測試，涵蓋 description 升格、`content:encoded` 優先、純文字不被動、script/style 丟棄、Atom xhtml 序列化、entity 解碼順序、以及下面兩條 review 修正的判定規則）。
- `ng test`：85 passed（66 → 85，新增 `shared/html.spec.ts` 19 項）；`ng build` production 通過，`prettier --check` 乾淨。initial bundle 504.97 kB 與 master 完全相同，超出預設預算 4.97 kB 是既有狀況（`@supabase/supabase-js` 被靜態引入，見階段十五），與本次改動無關。
- 回填在**真的 PostgreSQL 16** 上跑過，不是只靠推理：起了暫時 cluster，用 16 列涵蓋前六輪每一個爭議案例的樣本（含使用者貼的那篇周刊的實際片段）驗證回填結果——body 有被救回 `content`、中文摘要沒有多餘空格、`if x < 3 and y > 2` 原封不動、`&amp;lt;p&amp;gt;` 正確解成可見文字、script/style 整段消失、`NULL`／空字串／`<p></p>` 都沒有炸掉。

### Codex review：不能把「引用了標籤的純文字」當成 HTML

判定 markup 的規則原本是「含有標籤形狀的東西」，Codex 指出這條規則會反過來吃掉資料。XML 規定兩者都必須轉義，所以文章本體送成 `&lt;p&gt;text&lt;/p&gt;`、和一句在講 HTML 的散文寫成 `Use &lt;p&gt; for paragraphs`，經過 XML parser 之後**長得一模一樣**——都帶著真的 `<` 字元。舊規則把後者判成 markup，於是 `_plain_text()` 會把那個 `<p>` 剝掉，前端 reader 也會把它丟進 `[innerHTML]` 讓瀏覽器吞掉。錯的方向很糟：**文字直接從畫面上消失**，而不只是難看。

本 PR 自己的測試就寫出了會被踩到的值——`test_summary_decodes_entities_after_stripping_tags` 斷言 summary 是 `Tom & Jerry wrote <p>`，那正好會被前端啟發式判成 legacy markup 而吞掉結尾。

**改用「有沒有結束標籤或自閉合標籤」當判準**（`_MARKUP_RE` / `MARKUP_RE` / migration 的同一條 regex）：會包住東西的 markup 一定有 `</x>` 或 `<x …/>`，而引用標籤名的散文幾乎不可能有。**有屬性刻意不算數**——講 HTML 的文章成天引用 `<a href="…">`。同一條規則同時套在三個地方：是不是要升格成 `content`、要不要剝標籤、以及前端走哪個渲染分支。剝標籤與升格都改成只對「真的是文件」的值動手，entity 解碼與空白收斂則兩種情況都照做。

現在誤判的方向也翻過來了：漏判（例如整段只用 `<br>` 分行的 legacy 內容）最多是標籤露在畫面上，看得見、修得掉，不會靜悄悄弄丟東西。

migration 也跟著拆成四步（升格 / 剝標籤 / 解 entity 與收空白 / 把只剩空字串的 summary 正規化成 NULL）。

### Codex review 第二輪：沒有斜線的 void 標籤

同一個 bot 接著指出 `<br>` / `<img>` 這種合法但沒有結束標籤也沒有 `/>` 的 void element 會被判成純文字。這條只對了一半，而且**兩輪 review 的方向是相反的**——把 bare `<br>` 收進 markup，等於把第一輪修掉的洞照原樣開回來（`use <br> to break lines` 會被剝成 `use  to break lines`）。

拆開處理：

- **收進來**：void element **帶屬性**的形式（`<img src="…">`、`<hr class="…">`）。圖片型部落格與網路漫畫的 description 常常就是一個 bare `<img>`，那張圖就是整篇文章；判成純文字的話 reader 會把標籤當文字印出來、圖片完全不顯示，這是真的會弄丟內容。限定在 void element 是關鍵——「有屬性就算數」會把 `<a href="…">` 一起收進去，那正是第一輪的地雷。
- **繼續排除**：**沒有屬性的** bare void 標籤。`one<br>two` 確實是 markup，但 `use <br> to break lines` 也長一樣，兩者無法區分；只有後者猜錯會弄丟文字，所以平手時判給「不動它」。前者猜錯只是畫面上多一個 `<br>`，看得見、修得掉。

三處（parser、前端、migration）同步更新，並在 PostgreSQL 16 上用 16 列樣本重跑確認三邊判定一致：`<img src="…" alt="today">` 與 `<IMG SRC="a.png">` 正確升格，`use <br> to break lines`、`one<br>two`、`Use <p> for paragraphs and <a href="…"> for links`、單獨一個 `<p>` 全部原封不動。

### Codex review 第三輪：引用成對標籤的散文（未採納，已寫成測試記錄）

第三輪指出 `Use &lt;strong&gt;bold&lt;/strong&gt; for emphasis` 這種引用**成對**標籤的散文會被判成文件。機制上正確，但這次沒有改，理由是實測之後可以確定它落在安全那一側：

```
prose：Use <strong>bold</strong> for emphasis
  → content='Use <strong>bold</strong> for emphasis'  summary='Use bold for emphasis'
真body：Hello <b>world</b>, welcome
  → content='Hello <b>world</b>, welcome'             summary='Hello world, welcome'
```

兩者是**完全相同的字串形狀**，沒有任何規則能分開。而且成對標籤被誤判時**一個字都不會少**——`bold` 完整保留，只有兩個標籤 token 被吃掉；這正是第一輪那個空的 `<p>` 被排除的原因（剝掉會變成 `Use  for paragraphs`，句子壞掉）。

反過來說，任何為了排除這句散文而收緊的規則，都會連帶排除 `Hello <b>world</b>, welcome` 這種 RSS 裡極常見的短內文，把它打回「畫面上顯示裸標籤」——那正是這個 PR 要修的原始 bug。三輪下來這條啟發式已經到達它的有效邊界，再收緊就是拿常見情況換罕見情況。

決定寫成 `test_prose_quoting_a_paired_tag_is_knowingly_treated_as_markup`，把「知道、且刻意接受」釘在程式碼裡，而不是只留在 PR 討論串。

### Codex review 第四輪：屬性值裡的 `>`（已修）

這輪不是啟發式取捨，是實打實的字串處理 bug。`<p title="2 &gt; 1"&gt;` 是合法 HTML，但所有 tag pattern 都用 `[^>]*` 吃到第一個 `>` 為止——那個 `>` 在引號**裡面**，於是剩下的 `1">Hi` 被當成內文存進 `summary`：

```
修前：<p title="2 > 1">Hi</p>                    → summary='1">Hi'
      <a href="x" title="a > b">link</a> tail    → summary='b">link tail'
修後：                                            → 'Hi' / 'link tail'
```

改成走引號的 `_ATTRS = (?:[^>"']|"[^"]*"|'[^']*')*`，三處同步（`_TAG_RE` / `_DROP_WHOLE_RE` / `_MARKUP_RE`，前端與 migration 亦同）。兩個實作細節：

- **三個分支的第一個字元互斥**（`"` 只由引號分支吃、`'` 同理、其餘由 `[^>"']`），所以星號是確定性的、不會指數回溯。feed 內容是遠端可控輸入，這是真的暴露面而非假想，因此補了計時測試（20000 字元的惡意輸入 < 1 秒）。
- **保留原本的 `[^>]*` 當最後一個分支**，處理引號不成對的壞標籤（`<p title="unclosed>`）——走引號的版本比對不到它。順序不能反。

順帶收緊了 void element 分支：改成 `[^><]*=[^><]*>`，要求標籤真的閉合且中間不能有 `<`。原本 `the <img tag is useful, x = 1` 這種沒閉合的散文會一路吃到後面不相干的 `=` 而被判成 markup。

migration 在同一個 cluster 上用 21 列樣本重跑，新增的 5 列（引號內 `>`、`<pre>`、引號不成對、未閉合散文）三邊判定與 Python 端逐列一致。

### Codex review 第五輪：數字實體（已修）

migration 只用固定的 `replace()` 清單解 `&#39;` 與幾個具名實體，但解析器走的是 `html.unescape()`——它認得所有形式。feed 裡 `&#8217;`（彎引號）和 `&#8212;` / `&#x2014;`（破折號）滿地都是，而永遠不會再被 upsert 的舊資料列會一直在預覽裡顯示那串原始碼。

新增 step 3：一個 `DO` 區塊逐列掃出數字實體並轉成字元（`migrate.py` 是整份檔案一次 execute，且 001/002/004/005/006 早就在用 `DO $$`，不會有語句切割問題）。排在具名實體之前，讓整體維持 `html.unescape()` 的**單次掃描**語意——雙重轉義的 `&amp;#8217;` 在這步找不到 `&#`，到 step 4 才變成看得見的文字 `&#8217;`，與 Python 一致。

實作時自己踩到並修掉一個坑：**十六進位分支原本靜默失效**。`('x' || '2014')::bit(32)` 在 Postgres 是向**右**補零，得到 `0x20140000` 而不是 `0x2014`，超出 Unicode 範圍後被例外處理吞掉，`&#x2014;` 原封不動留著。要 `lpad(hex, 8, '0')` 才對。是實跑 26 列樣本才看見的，不是推理出來的。

另外把「無效碼位」的行為對齊 Python：`&#0;`、`&#1114112;`、`&#xD800;` 這些 `chr()` 不接受的值，`html.unescape()` 產出 U+FFFD，migration 也照做。原本打算保留原始文字（其實比 `�` 好看），但那會讓「同一個 feed 的兩篇文章，一篇走 migration、一篇被重新 upsert」顯示不一樣——這種細微不一致以後會害人查半天，不值得。

前端 `stripHtml()` 有同一個缺口，一併補上，並把具名 + 數字合併成單一 regex 的**一次掃描**（原本是多次 `replace` 串接），語意才真的與 Python 相同。

### Codex review 第六輪：C1 範圍的數字實體（已修，並窮舉驗證）

`&#151;`、`&#145;`、`&#128;` 這些 0x80–0x9F 的數字實體，`html.unescape()` **不當成控制字元**——依 HTML5 規範改用 Windows-1252 對應字元，所以 `&#151;` 是破折號、`&#128;` 是歐元符號。舊 feed（凡是經過 Word 的）滿地都是。migration 存 `chr(151)` 會塞一個看不見的控制字元進去。

修法是把 Python 的 `html._invalid_charrefs` **程式產生**成 32 格對照表寫進 migration 與前端，不手抄——這種表手抄一定會錯。

**這輪真正的收穫是驗證方式改了。** 前五輪都是拿手挑的樣本比對，這次改成**窮舉**：0–255 全部碼位、0xFDCE–0xFDF1、各平面邊界、surrogate、noncharacter、超範圍值，每個都跑十進位與十六進位兩種寫法，加上字面空白字元，共 638 列，逐列 base64 匯出後與 Python 輸出**逐位元組**比對。

結果是它抓到兩個 Codex 沒提、我也沒想到的差異：

1. **`_invalid_codepoints` 沒實作**。除了 tab/LF/FF/CR 以外的控制字元，`html.unescape()` 是**整個丟掉**引用，不是轉成字元。這一組加上 Unicode noncharacter 共 126 個碼位、22 個區間，但最後 17 個是 `0xNFFFE/0xNFFFF` 的規律，可以縮成一行 `(code & 0xFFFE) = 0xFFFE`。
2. **step 4 的 WHERE 條件漏選**。原本為了避免整表重寫，只選「含實體 / 連續空白 / 首尾空白」的列——`line one<TAB>line two` 三個條件都不符合，於是那個 tab 永遠不會被正規化。這是我自己為了微優化而製造的 bug。**改成 `WHERE summary IS NOT NULL` 掃全表**：前三步本來就已經在重寫了，省這一次沒有意義，卻換來一整類「這個條件涵蓋到了嗎」的推理負擔。順帶補上 Postgres `[[:space:]]` 比 Python `\s`少的字元（NBSP 等）。

第 2 點特別值得記：**聰明的 WHERE 條件是這個 migration 唯一一個「我自己寫壞、而且五輪 review 都沒抓到」的地方**，是窮舉才逼出來的。

### Codex review 第七輪：兩個發現，一個共同根因（架構改掉）

第七輪指出兩件事：

1. **具名實體只解了六個**。`&rsquo;`、`&mdash;`、`&hellip;`、`&copy;` 這些常見的都沒解——Python 的 `html.unescape()` 認得 **2231 個**。
2. **我的數字實體解碼不是單次掃描**。`&#38;` 就是 `&`，解開它會**製造出**一個新的 `&#8217;`，而 `SELECT DISTINCT` 沒有順序保證，後面那輪會把它也解掉。`X&#38;#8217; and &#8217;Y` 應該是 `X&#8217; and ’Y`，我的版本會變成 `X’ and ’Y`。

兩條都對。但**第五、六、七輪（共四個發現）是同一個根因**：我在 PL/pgSQL 裡重寫 `html.unescape()`。每一輪都在補症狀——先是實體表不全、然後十六進位分支靜默失效、然後 C1 範圍存成控制字元、然後解碼會重掃自己的輸出。「2231 個具名實體」這個數字是最後一根稻草：真要對齊就得把 2231 筆表格產生進 SQL，再手寫一個單次掃描器。

**改掉架構：回填不再用 SQL，改成 Python 直接呼叫解析器。**

- 刪掉 `migrations/009_plain_text_article_summaries.sql`（145 行 PL/pgSQL）。
- 新增 `backend/backfill.py`：用 server-side cursor 分批走過 `articles`，每列丟進 `_repair()`，而 `_repair()` 直接呼叫 `_looks_like_markup()` 與 `_plain_text()`。與解析器的一致性**由建構保證**，不是靠比對維持。
- 在 `main.py` 的 lifespan 於 `run_migrations()` 之後呼叫，沿用同一張 `_migrations` 表追蹤，語意與既有 migration 完全一致（缺 `DATABASE_URL` 時只警告不中斷）。

**前端同一個根因，同一種解法**：`stripHtml()` 的六筆具名實體表 + 手寫數字解碼 + C1 對照表 + 丟棄碼位集合（共約 60 行），全部換成把字串交給瀏覽器自己的 HTML parser——它**就是** HTML 規範的實作，2231 個實體、C1 代換、單次掃描全都內建。先把 `<` 轉義再丟進去，這一步是關鍵：既擋住殘留的標籤形狀文字被當成元素吃掉（`Use <p> for paragraphs` 是要保留的散文），也保證餵進去的是惰性文字。

淨效果是**刪掉的程式碼比新增的多**，而且前六輪每一個爭議案例都仍然正確——16 列端到端樣本在真 PostgreSQL 上驗證，全部與解析器輸出一致。

**已知且刻意的差異**：控制字元引用（`&#1;`）在前端會被**輸出**而非丟棄。HTML 規範說輸出（附帶 parse error），`html.unescape()` 比規範更嚴格、選擇丟棄。要對齊就得把剛剛刪掉的碼位表加回來，而它換不到任何東西——後端根本不會存下這種值（解析器在寫入前就丟掉了），而且兩種寫法在畫面上都是看不見的。已寫成測試 `emits control-character references, where Python drops them` 記錄。

### 已知未處理

`<p></p>` 這種只有空標籤的 description 會被升格成 `content`，reader 於是渲染出一個空的 `.prose` 而不顯示「前往原文閱讀」。沒有加「必須有文字才升格」的條件是刻意的：那條件會連帶把 `<p><img src="…"></p>` 這種只有圖片的 description（圖片部落格、網路漫畫很常見，而且圖片就是內文）也擋掉，代價比它換到的好處大。

009 的第二段 UPDATE **不是冪等的**——entity 解碼本質上不可能冪等，重跑一次會把上游雙重轉義殘留的 `&lt;p&gt;` 再解一層變成 `<p>`。正常路徑不會踩到（`_migrations` 保證只跑一次），但階段十六提到有人手動清空過 `_migrations`；那種情況下這個檔案會對已經乾淨的 `summary` 多解一層。沒有為此加防護，因為判斷「這個 entity 是原文還是殘留」本身就沒有正確答案，而 refresh worker 之後的 upsert 會把值蓋回解析器算出的正確結果。

## 階段十八：`GET /api/recommendations` 的 `liked`/`disliked` 補上 UUID 型別驗證（PR #34，2026-08-05）

同一個「持續改善專案」排程任務。`feeds.id` 是 `migrations/001_initial_schema.sql` 宣告的 `UUID` 欄位，本專案其餘每個接 feed/article id 的端點都把對應參數宣告成 `UUID`，讓 FastAPI/pydantic 在進任何 DB 呼叫前就把格式錯誤的值擋成乾淨的 `422`——唯獨這個公開免認證端點的 `liked`/`disliked` 兩個 query 參數仍是 `list[str]`，只限制陣列長度，不限制每個字串要長得像 UUID。一個像 `?liked=not-a-uuid` 的請求會帶著這個字串一路走到 `.in_("id", liked)` 與 `sample_feed_candidates` RPC 型別是 `uuid[]` 的參數，讓 Postgres 端的型別轉換直接炸掉；`backend/` 全專案沒有任何地方對 `postgrest`/`APIError` 掛 `exception_handler`，未接住的例外變成裸的 FastAPI `500`，不是輸入驗證該給的 `4xx`。

- **修法**：`backend/routers/recommendations.py` 的 `liked`、`disliked` 型別改成 `list[UUID]`，比照本專案其他 id 參數一貫的宣告方式；兩個實際使用處（組 `excluded` 集合、`.in_("id", ...)`）在呼叫端用 `str(u)` 轉回字串，同樣沿用其餘 router 既有的 `str(feed_id)` 寫法，行為本身不變，只是把「格式錯誤」這一類輸入提前攔在請求驗證這一層。
- **測試**：`backend/tests/test_recommendations.py` 新增 `test_malformed_id_in_liked_or_disliked_is_rejected`（`liked`、`disliked` 各跑一次），斷言帶入非 UUID 字串回 `422`，且 `mock_db.table` / `mock_db.rpc` 都未被呼叫——證明是請求驗證層擋下，不是等 DB 端才處理。沒有網路能在本 sandbox 安裝依賴跑 `pytest`（與 #25–#27 同樣的既有限制），本機以 `python3 -m py_compile` 與 `ruff check` 驗證，交給 CI 實際跑過。
- 詳細前後對照見 [SECURITY.md #28](SECURITY.md)。

## 階段十九：Supabase `driftread` schema 隔離與 RLS hardening（2026-08-12）

- 所有 Driftread tables、functions 與 migration ledger 從共用 `public` 原地搬入 `driftread`。
- backend Supabase client 固定使用 `ClientOptions(schema="driftread")`；SQL migration、backfill
  與 function body 全改用 schema-qualified names。
- `_migrations` 開 RLS 並撤銷 anon/authenticated grants；公開 catalog、使用者資料與後台 discovery
  資料分別使用 public-read、permanent-user owner-only、service-role-only 權限模型。
- owner policy 額外拒絕 Supabase Anonymous Sign-In 帳號，並把 `auth.uid()` / `auth.jwt()` 包成
  init-plan subquery；functions 固定 `search_path=pg_catalog` 並收斂 EXECUTE grants。
- migration 010 將資料搬遷、Data API exposure、RLS 與 client 切換放在同一次 backend deployment，
  避免舊版 public-scoped client 與已搬移 tables 之間產生停機窗口。

## 階段二十：前端 Supabase 設定改為 runtime config，官方 GHCR image 免自建（2026-08-17）

TODO.md 建議開發批次第 2 批。`frontend/src/environments/environment.ts` 的 `supabaseUrl` /
`supabaseAnonKey` 在 `npm run build` 時就編進 JS bundle，repo 內留空，所以 CI 建出的
`ghcr.io/dwvwdv/driftread-frontend:latest` 永遠是空的——`AuthService.isConfigured()` 恆回
`false`，註冊 / 登入 / 訂閱 / 已讀 / 收藏 / 稍後讀全部不會運作，只能自建 image 才能用（見
`docs/FEATURES.md` 舊版第 4 節）。

- **修法**：新增一個小的 runtime config 讀取層，不改 `AuthService` 既有的登入 / 登出 /
  session restore 邏輯本身：
  - `frontend/src/app/services/runtime-config.ts`：`runtimeSupabaseConfig()` 讀
    `window.__env`，缺值時退回 `environment.ts`（與改動前行為一致，`ng serve` 不受影響）。
  - `frontend/public/env.js`：本地開發預設值（空字串），透過既有的 `public/` asset pipeline
    編進 bundle，與 `favicon.ico` 同一套機制。
  - `frontend/src/index.html`：在 Angular bundle 之前用一般（非 module）`<script src="env.js">`
    載入，確保 `window.__env` 在 `AuthService` 建構時已經存在。
  - `frontend/env.template.js` + `frontend/docker-entrypoint.d/15-render-driftread-env.sh`：
    nginx 官方 image 既有的 entrypoint 擴充點（`/docker-entrypoint.d/*.sh`，執行完才
    `exec nginx`），用 `envsubst` 把容器的 `SUPABASE_URL` / `SUPABASE_ANON_KEY` 渲染進
    `env.js`，不需要自訂 `ENTRYPOINT`。
  - `AuthService` 建構子唯一改動：兩個字串的來源從 `environment.*` 換成
    `runtimeSupabaseConfig()`，其餘 signIn / signUp / signOut / session signal 完全不動。
- **環境變數**：新增 `SUPABASE_ANON_KEY`（瀏覽器用的 anon / publishable key，與 backend 的
  `SUPABASE_KEY` service_role key 分開），三處同步更新：`.env.example`、
  `docker-compose.yml`（`frontend.environment`）、`scripts/gen_env.py`（併入
  `SUPABASE_URL` / `SUPABASE_KEY` 既有的手動填寫提示）。
- **測試**：`frontend/src/app/services/runtime-config.spec.ts` 覆蓋四種情境——`window.__env`
  未設定、`env.js` 出的是本地空預設值、entrypoint 真的渲染了值、只渲染了一半（驗證是
  field-by-field 退回，不是整組退回）。本 sandbox 的 npm registry allowlist 沒收錄
  vitest/jsdom 這條依賴鏈用到的一批套件（`whatwg-url`、`yargs`、`zod` 等，`npm ci` 在這批
  套件上收到 "no rule or allowlist entry allows host" 而失敗），因此無法在本機跑
  `ng test` / `ng build` 驗證，以 `node --check` 對新增檔案做語法檢查，交給 CI 實際跑過。
  `AuthService` 本身未新增測試——它的邏輯零改動，既有的登入相關元件測試一律用
  `{ provide: AuthService, useValue: ... }` mock 掉，不受這次改動影響。
- 對應文件更新：`docs/FEATURES.md` 第 4 節、`README.md`（環境變數表與「前端 Supabase 設定」章節）。

## 階段二十一：訂閱 CTA、單一訂閱狀態與 frontend CI 補跑單元測試（2026-08-18）

TODO.md 建議開發批次第 3 批。此前訂閱只能在「我的訂閱」頁面單向取消，feed 詳情、目錄卡片、
Discover 與「猜你喜歡」都沒有訂閱入口，也沒有任何地方能知道「這個 feed 我訂閱了沒」——每個頁面
各自推導，彼此不同步。

- **`frontend/src/app/services/subscription.ts`（新）**：`SubscriptionService`，單一訂閱狀態
  快取，登入後（依 user id keyed，同 `MyFeeds` 既有的 `loadedFor` pattern）載入一次，`isSubscribed()`
  供任何頁面查詢。`subscribe()` / `unsubscribe()` 樂觀更新本地狀態、失敗回滾、pending 期間忽略
  重複呼叫；`sync()` 讓已經自己抓過 `Feed[]` 的頁面（`MyFeeds`）直接回填快取，不必再多打一次
  `GET /me/feeds`；`markSubscribed()` / `markUnsubscribed()` 給後端已經順帶完成訂閱異動的情況
  （見下方 Discover 匯入）記錄狀態，不必再補一次多餘的 API 呼叫。
  - 有個真實的 race：重新登入的同一個 tick 裡，session 變化同時觸發這個 service 自己的
    reload effect，也可能觸發呼叫端自己的 `subscribe()`（例如登入後代下的訂閱）。若 reload 的
    伺服器快照剛好是那筆寫入 commit 之前抓到的，`_ids.set(new Set(serverIds))` 直接整組覆蓋
    就會把還在 in-flight 的樂觀新增蓋掉。`load()`/`sync()` 現在對 `_pending` 中的 id 保留本地
    樂觀值，其餘才信任伺服器快照。`subscription.spec.ts` 有這個情境的回歸測試。
- **訂閱入口**：
  - `components/feed-detail`：header 加「訂閱／已訂閱」按鈕。
  - `components/feed-list`：每張目錄卡片加快速訂閱；按鈕/已訂閱 chip 疊在卡片整體可點擊區
    （標題 `::after` 撐開的 hit area）之上（`position: relative; z-index: 1`），否則會被蓋住點不到。
  - `components/discover`：已收錄（`already_exists`）的候選除了「前往查看」，登入使用者可直接訂閱；
    新匯入（`POST /discover/import`）本來就會讓後端順便訂閱登入中的使用者
    （`backend/routers/discover.py`），前端呼叫 `markSubscribed()` 同步快取，不重複打
    `POST /me/feeds/{id}`。
  - `components/recommendations`（猜你喜歡）：卡片動作由「喜歡／跳過」兩個，拆成「喜歡／跳過／
    訂閱」三個獨立語意。訂閱同時仍記一筆本地「喜歡」信號——`user_feed_feedback` 之類的獨立
    `subscribed` 訊號與持久化是 TODO.md 之後「回饋持久化」批次的範圍，這批只先把 UI 動作分開。
  - `components/my-feeds`：取消訂閱／重新整理清單時透過 `subs.markUnsubscribed()` /
    `subs.sync()` 回寫共用快取，讓其他頁面不必整頁重新整理就會同步。
- **未登入時的訂閱**：以上入口在未登入時都導去 `/login?redirect=<原路徑>&subscribeFeed=<feed id>`，
  而不是把點擊吃掉或丟回首頁。`components/login` 的 `Login.submit()` 登入成功後讀這兩個 query
  param，代呼叫一次 `subscribe()` 再導回 `redirect`（沒有則回首頁），簽出時不會誤觸發。
- **frontend CI**：`.github/workflows/frontend.yml` 的 Build job 在 `npm run build` 前加
  `npm test`。此前 CI 只跑 production build，這個 repo 既有／新增的所有 `*.spec.ts` 從未被 CI
  執行過（`ng build` 用的 `tsconfig.app.json` 也刻意排除 `*.spec.ts`，型別錯誤都抓不到）。
  `@angular/build:unit-test`（Vitest + jsdom，非瀏覽器）在 GitHub Actions 會自動偵測
  `CI=true` 以 non-watch 模式單次執行，不需要額外安裝瀏覽器或加 `--no-watch`。
- **測試**：新增 `subscription.spec.ts`、`feed-detail.spec.ts`、`feed-list.spec.ts`、
  `discover.spec.ts`、`recommendations.spec.ts`、`login.spec.ts`。全部沿用既有測試慣例——純
  物件 fake + 手動記錄呼叫（本專案的 Vitest 設定裡沒有任何 spec 用 `jasmine.*`／`vi.*` mock
  API，一律手寫 fake），`Router.navigate`/`navigateByUrl` 用真的 `provideRouter([])` 換掉方法
  本體記錄呼叫參數，`SubscriptionService` 自己的 effect 測試用 `TestBed.flushEffects()`
  （Angular 17 起的正式 API，用在 `TestBed.inject()` 直接建立、不經過 `ComponentFixture` 的場合）。
  本 sandbox 的 npm registry allowlist 依然卡在同一批依賴鏈（`zod-to-json-schema` 等）上，
  `npm ci` 全部失敗，無法在本機跑 `ng build` / `ng test` 驗證——與階段二十的已知限制相同，
  交給 CI 實際跑過；已用人工重讀全部改動檔案一遍。
- 對應文件更新：`docs/FEATURES.md` 第 1 節、第 7 節（新增 Frontend 測試列）、`TODO.md`
  （批次 3 打勾，勾掉 frontend CI 補跑單元測試那條）。

## 階段二十二：手動 refresh response contract、bookmarks 複合 index（2026-08-19）

- **`POST /admin/feeds/{feed_id}/refresh` 補上型別化 response model**：原本
  `response_model=dict`，回傳的 dict 完全沒有欄位驗證與 OpenAPI schema。新增
  `models.py::FeedRefreshResult`（`inserted` / `feed_id` / `status` / `new_articles` /
  `total_articles`），沿用既有欄位名稱與語意——`inserted` 這個名字本身就是既有外部合約（瀏覽器
  擴充與外部腳本會讀），沒有改名。`status` 收斂成 `Literal["updated", "not_modified", "failed"]`，
  對齊 `services/feed_refresh.py::Status` 本來就有的型別。既有測試
  `test_refresh_feed_success_keeps_inserted_key` 不需要改動斷言就能通過，額外補了一條
  `status` 欄位的斷言。這條路由原本的測試就已經用 `patch()` 蓋掉
  `fetch_and_parse_conditional`，沒有打過真實網路，TODO.md 那條「測試不得依賴真實 DNS」其實
  早就成立，這次一併打勾。
- **`user_bookmarks` 補複合 index**：migration 013 新增
  `user_bookmarks_user_type_created_idx (user_id, bookmark_type, created_at DESC)`。
  `GET /me/bookmarks` 的查詢型態是 `.eq(user_id).eq(bookmark_type).order(created_at desc)`，
  既有的 `user_bookmarks_user_type_idx (user_id, bookmark_type)` 只覆蓋兩個等值篩選，
  `ORDER BY` 仍要另外排序；新 index 讓整條查詢一次索引掃描就能滿足，做法比照 migration 012
  幫 `user_article_reads` 補 keyset index 的先例——保留舊 index，只新增，不做風險較高的欄位替換。
- **`TODO.md` 補打勾**：盤點「技術與可靠性優化」整節時發現 `GET /me/bookmarks` 只回傳
  `ArticleSummary`（PR #37 就做了）、`GET /categories` 已經是 SQL 端 `DISTINCT` RPC
  （`driftread.list_feed_categories()`，migration 011）都是先前漏勾，一併補上；
  `user_feeds` / `user_article_reads` 的複合 index 現況也一併記錄——`user_feeds` 查詢只有
  單一等值篩選，PK 前導欄位已經夠用，不需要額外 index。
- 本 sandbox 的 pip index allowlist 卡在同一類限制（`pip install -r requirements.txt` 連
  `pytest` 都裝不出來），無法在本機跑 `pytest`，交給 CI 的 `backend.yml` 實際跑過；已對兩處改
  動（Pydantic model 型別、純 additive 的 index migration）做語法檢查與人工重讀。
- 對應文件更新：`docs/FEATURES.md` 第 5 節（索引清單補 012／013 兩條，先前也漏了 012）、
  `TODO.md`（本節五個項目打勾／補說明）。

## 階段二十三：部署／回滾 runbook，GHCR image 補 commit sha tag（2026-08-24）

- **新增 `docs/RUNBOOK.md`**：對照 `TODO.md`「補上升級與回滾 runbook，特別記錄 schema
  exposure、grant、RLS 與 runtime config 的部署順序」這條，寫一份給實際操作
  `docker-compose.yml` 的人看的操作手冊——一般部署四步、會動到 Supabase Dashboard
  Exposed Schemas／grant 的部署要先後順序（Dashboard 手動步驟必須先於帶新 migration 的
  `api` image 部署，理由是反過來的話 API 對新 schema／表的請求會直接壞掉而非優雅降級）、
  migration 010 保留的 `public._migrations` 相容 view 何時能安全移除、以及環境變數檢查
  指到 `CLAUDE.md` 既有的三處同步規則。
- **意外發現並修掉的缺口**：寫回滾章節時發現 `.github/workflows/{backend,frontend}.yml`
  的 `docker/build-push-action` 只打 `:latest` 一個 tag——這代表「回滾」在此之前根本沒有
  對應的 image 可指，只能等一次新的、修好的部署把 `:latest` 蓋掉。兩個 workflow 都加上
  `sha-${{ github.sha }}` 第二個 tag（`docker/build-push-action` 的 `tags:` 本來就支援多行
  多個 tag），永久保留、不會被覆寫，回滾 runbook 因此有真的可以操作的步驟：改
  `docker-compose.yml` 三個 `image:` 欄位指到 `:sha-<sha>`。
- **TODO.md 盤點**：連帶重讀「Migration 與部署」整節時發現兩條已經做了但沒打勾——
  migration runner 的 PostgreSQL advisory lock（`migrate.py::acquire_migration_lock`，
  `run_backfills()` 也共用同一把）、以及 migration／backfill 的可追蹤可重試狀態（兩者都記在
  `driftread._migrations`，成功才 commit，重跑會跳過已套用項目）——都補上勾。
- 本 sandbox 沒有網路能跑 `actions/lint` 之類的工具驗證 workflow YAML，用系統已有的
  PyYAML（`python3 -c "import yaml; yaml.safe_load(...)"`）對兩個改動過的 workflow 檔案
  各跑一次 `safe_load` 確認語法正確；多行 `tags:` 寫法本身沿用
  `docker/build-push-action@v6` 官方文件既有的用法。
- 對應文件更新：`docs/FEATURES.md` 第 7 節（部署列補 sha tag 與 RUNBOOK 連結）、
  `TODO.md`（「Migration 與部署」四項打勾／補說明）。

## 階段二十四：偏好設定 UI（2026-08-24）

TODO.md「P1：偏好、推薦與內容探索」的「建立偏好設定 UI，接上既有 `getPreferences()`／
`updatePreferences()`」——後端與 frontend service 早已存在（`routers/me.py` 的
`GET`/`PUT /me/preferences`、`services/me.ts` 的 `getPreferences()`/`updatePreferences()`），
只是沒有頁面可以呼叫它們。

- **`backend/migrations/014_feed_languages_rpc.sql`**：新增 `driftread.list_feed_languages()`，
  仿照 migration 011 的 `list_feed_categories()`——db-side `DISTINCT`、`REVOKE ALL FROM PUBLIC,
  anon, authenticated`，只有 service_role 能 `EXECUTE`。`routers/feeds.py` 新增
  `GET /feeds/languages`，回傳型別與既有的 `GET /feeds/categories` 一致（`list[str]`）。
- **`frontend/src/app/components/preferences`**（新元件，`/me/preferences`）：分類與語言各自
  以 `ob-chip` 呈現成可複選的 toggle 清單，選項來自 `GET /feeds/categories` /
  `GET /feeds/languages`（實際目錄的詞彙，不是寫死的清單），已選狀態載入自
  `GET /me/preferences`，按「儲存偏好」呼叫 `PUT /me/preferences`。沿用
  `bookmarks`/`my-feeds` 既有的「依 `auth.session()` 的 user id 判斷是否已載入」`effect()`
  寫法，避免 `AuthService` 還原 session 前就用空清單渲染。導覽列帳號選單與行動版抽屜都加上
  「偏好設定」連結，排在「收藏」之後。
- **不做的部分**：受控 category/tag vocabulary（同義詞、大小寫、多語標籤正規化）與推薦理由顯示
  是 TODO.md 同一節底下的獨立項目，留給各自的後續 PR；這批只接上既有的兩個欄位。
- **測試**：`backend/tests/test_feeds.py` 新增 `test_list_languages_uses_db_side_dedup`，比照
  既有的 `test_list_categories_uses_db_side_dedup`。`frontend/.../preferences.spec.ts` 覆蓋
  選項與已選狀態載入、toggle 的 immutable 更新、儲存成功/失敗的 toast 與 `saving()` 狀態。
- **本 sandbox 的已知限制**：`npm ci` 仍卡在 `zod-to-json-schema` 那條依賴鏈（403），
  `pip install pytest` 也被 PyPI allowlist 擋下，backend／frontend 測試都無法在本機實際執行，
  與階段二十一、二十二遇到的限制相同；已用 `python3 -m py_compile` 過 backend 改動、系統 `tsc`
  （`--ignoreConfig --noResolve`，僅語法檢查）過 frontend 改動，交給 CI 實際跑過驗證。
- 對應文件更新：`docs/FEATURES.md`（API 端點、DB function、前端路由）、`TODO.md`
  （「建立偏好設定 UI」打勾）。

## 階段二十五：Feed 目錄的語言篩選與可點擊標籤（2026-08-25）

TODO.md「P1：標籤、語言與偏好設定」剩下的兩項——「Feed tag 改為可點擊篩選」與「Feed 目錄加入
language、category、tag 的組合篩選」。`category`／`tag` 篩選、偏好設定 UI 的分類/語言 chip 都已
存在，這批把兩者接起來：目錄頁補上語言篩選，卡片上的標籤本身也能點。

- **`backend/routers/feeds.py`**：`GET /feeds` 新增 `language` 查詢參數，`query.eq("language",
  language)`，與既有 `category`／`tag` 篩選同一種 `AND` 疊加寫法。`feeds` 表本來就有
  `language` 欄位（`Feed` model 早已有），不需要新 migration；語言選項清單沿用階段二十四剛加的
  `GET /feeds/languages`。
- **`frontend/src/app/services/feed.ts`**：`getFeeds()` 簽名插入 `language` 參數（`page,
  pageSize, category, language, tag, search`），唯一呼叫端 `feed-list.ts` 一併更新。
- **`frontend/src/app/components/feed-list`**：
  - 篩選列加一個語言 `<select>`，選項來自新增的 `loadLanguages()`（`getLanguages()`，失敗時
    降級成「全部語言」而不擋頁面，與 `loadCategories()` 同一套容錯）。
  - 卡片上的標籤從純文字 `<li class="ob-chip">` 改成 `<button class="ob-chip">`，點擊即以該
    標籤篩選、再點一次清除（`filterByTag()`）——與偏好設定 UI 的 toggle chip 同一套寫法。目前
    篩選中的標籤會反白（`ob-chip--success`），篩選列上方另外顯示一個可點擊清除的「標籤篩選：
    ⟨tag⟩」提示。
  - 標籤按鈕疊在卡片標題連結的 stretched `::after` 之上（`position: relative; z-index: 1`），
    沿用 `.subscribe-btn`／`.subscribe-chip` 已有的做法，點擊標籤不會被卡片本身的導覽連結吃掉。
  - `hasFilters`／`clearFilters()` 一併涵蓋 `language`／`tag`。
- **測試**：`backend/tests/test_feeds.py` 新增 `test_list_feeds_filters_by_language`。
  `frontend/.../feed-list.spec.ts` 新增一個 describe block：分類/語言/標籤三者一起送進
  `getFeeds()`、點同一個標籤兩次會清除（toggle）、`hasFilters`／`clearFilters()` 涵蓋新欄位。
- **不做的部分**：受控 category/tag vocabulary（同義詞、大小寫、多語標籤正規化）仍是 TODO.md
  同一節底下的獨立項目；feed-detail／discover 頁面上的標籤目前維持純文字展示，沒有一併改成連回
  目錄頁篩選的連結（`feed-list` 本身也還沒有 query-param 同步，留給需要深連結時的後續 PR）。
- **本 sandbox 的已知限制**：`npm ci` 仍卡在 `zod-to-json-schema` 依賴鏈（403），
  `pip install pytest` 被 PyPI allowlist 擋下，backend／frontend 測試都無法在本機實際執行，與
  階段二十一至二十四相同；已用 `python3 -m py_compile` 過 backend 改動、系統 `tsc`
  （`--ignoreConfig --noResolve`，僅語法檢查）過 frontend 改動，交給 CI 實際跑過驗證。
- 對應文件更新：`docs/FEATURES.md`（`GET /feeds` 參數說明、信息源瀏覽功能列）、`TODO.md`
  （兩項打勾，「建議開發批次」第 5 項改為進行中）。
## 階段二十六：我的閱讀流、未讀數與已讀管理（2026-08-19）

TODO.md 建議開發批次第 4 批。此前「已讀」只有 `POST /me/articles/{id}/read`（單篇標記、無法
復原）與 `GET /me/reads`（回原始 read receipt id 列表，cursor 分頁但前端從未使用），沒有任何一個
地方能一次看到「所有已訂閱來源的新文章」——`/me/feeds` 只列訂閱本身，要讀新文章得逐一點進每個
feed 詳情頁翻最新 10 篇。也沒有未讀數，也沒有批次已讀。

- **`user_article_reads` 不新增表**：一列存在即代表「已讀」，這批只加查詢端的 DB function 與
  index，讀寫路徑仍是同一張 002 建的表——`DELETE` 該列就是「標為未讀」。
- **`backend/migrations/015_reading_stream.sql`（新）**：三個 `driftread` schema 內的 DB
  function，EXECUTE 只授權 `service_role`（同 `sample_feed_candidates` / `list_feed_categories`
  的鎖法）：
  - `list_reading_stream(...)`：跨 `user_feeds` 聚合每個已訂閱來源的 `articles`，LEFT JOIN
    `user_article_reads` 帶出 `is_read` / `read_at`，keyset 分頁。排序鍵是
    `COALESCE(published_at, fetched_at) DESC, id DESC`——未解析出發佈日期的文章退回抓取時間，
    避免落進 Postgres `DESC` 預設的 `NULLS FIRST` 卡在最前面，也讓 cursor 比較不必特別處理
    NULL。
  - `reading_stream_unread_counts(...)`：每個已訂閱來源的未讀數，LEFT JOIN 而非 anti-join，
    讀完的來源仍會列出、只是 0，前端拿來畫「各來源未讀數」的篩選下拉。
  - `mark_reading_stream_read(...)`：伺服器端一次 `INSERT ... SELECT ... ON CONFLICT DO
    NOTHING`，供「明確範圍」全部已讀用（單一來源／整個閱讀流），不必先把符合的 article id
    全部撈回 Python 再逐筆 upsert。
  - 新增 `articles(feed_id, fetched_at DESC)` 索引；`user_feeds` 與 `user_article_reads` 既有的
    複合主鍵已經覆蓋這批查詢在這兩張表上的存取模式，不必再加。
- **`backend/routers/me.py`**：
  - `DELETE /me/articles/{id}/read`——`mark_read` 的反向操作，標為未讀。
  - `POST /me/reads/mark-all`——帶 `article_ids` 就精準標那幾篇（目前頁面）；不帶則用
    `feed_id` / `before` 走上面的 RPC（明確範圍，三者都空即整個閱讀流全部已讀）。回傳
    `{marked}`。
  - `GET /me/stream`——聚合文章流，`cursor` / `limit`（上限 100）/ `feed_id` / `unread_only`。
  - `GET /me/stream/unread-counts`——總未讀數與各來源未讀數。
  - `utils.py` 新增 `encode_keyset_cursor` / `decode_keyset_cursor`，把 `GET /me/reads` 內
    原本寫死在 router 裡的 cursor 編解碼抽出來給 `/me/stream` 共用，行為不變。
- **`frontend/src/app/services/reading-stream.ts`（新）**：`ReadingStreamService`，
  `providedIn: 'root'` 單一快取，同時餵給導覽列帳號選單的未讀數 badge 與閱讀流頁面本身
  （同 `SubscriptionService` 讓多個元件共用一份狀態的角色，見階段二十一）。單篇標記已讀／未讀
  樂觀更新＋失敗回滾＋pending 期間忽略重複呼叫；「本頁全部已讀」送目前頁面未讀文章的 id 清單；
  「明確範圍全部已讀」（可選單一來源）不做本地樂觀更新——範圍可能涵蓋這頁從未載入過的文章，
  成功後改用伺服器回應重新整理未讀數，並把畫面上已載入、落在範圍內的列直接標成已讀。
- **`frontend/src/app/components/reading-stream`（新，`/me/stream`）**：主要閱讀入口。cursor
  「載入更多」（不一次載入全部）、總未讀 / 本頁未讀（`ObStat`）、「只看未讀」（server-side
  filter）與「隱藏已讀」（client-side 篩選，兩者刻意分開——前者改變 `GET /me/stream` 抓什麼，
  後者只影響已抓到的資料怎麼顯示）、來源篩選下拉（選項即 `reading_stream_unread_counts` 回傳的
  已訂閱來源清單，不必另外呼叫 `GET /me/feeds`）、逐篇已讀／未讀切換、兩種全部已讀動作
  （明確範圍那個帶 `ConfirmService` 確認對話框，訊息依是否有作用中的來源篩選而不同）。
  導覽列（`layouts/public-layout`）帳號選單第一個項目換成「我的閱讀」（帶未讀數 chip），原本
  排最前的「我的訂閱」讓出主要閱讀入口的角色，往後移一位，並在自己的頁首加一顆「前往我的閱讀」
  按鈕；`/me/feeds` 仍是唯一的來源管理入口（訂閱清單、OPML 匯入匯出），沒有拿掉任何既有功能。
- **測試**：新增 `test_me.py` 的 mark-unread／mark-all／stream／unread-counts 案例（沿用既有
  `mock_db` + `dependency_overrides` 慣例）；`reading-stream.spec.ts`（service，`TestBed.inject`
  + `TestBed.flushEffects()`，同 `subscription.spec.ts` 的模式）與
  `components/reading-stream/reading-stream.spec.ts`（元件，`TestBed.inject(ReadingStreamService)`
  拿與元件共用的同一個 root instance 斷言狀態，而不是碰元件自己 `protected` 的欄位——同
  `feed-detail.spec.ts` 對 `SubscriptionService` 的作法）。本 sandbox 對 PyPI 與 npm registry
  的存取都被 allowlist 擋下（`pip install pytest` 403、`npm ci` 卡在
  `zod-to-json-schema` 同一批依賴鏈），backend／frontend 測試都無法在本機實際執行——與階段
  二十一的已知限制相同，交給 CI 實際跑過；已用人工重讀全部改動檔案與呼叫鏈一遍。
- **刻意先不做**：`GET /me/stream` 目前只在讀者主動載入該頁時抓資料，不是即時／WebSocket
  推送新文章或即時更新未讀數；`article-reader` 開文章時仍各自呼叫 `POST /me/articles/{id}/read`，
  沒有回頭同步已載入的 `ReadingStreamService` 快取，所以在另一個分頁／視窗開文章不會立刻反映在
  已開啟的閱讀流頁面上，要等下一次 `reload()`。兩者都留給之後的批次或後續 PR。
- 對應文件更新：`docs/FEATURES.md` 第 1、3、4、5 節、`TODO.md`（批次 4「我的閱讀流」七項全部
  打勾，「建議開發批次」第 4 項打勾）。

## 階段二十七：Feed 完整文章列表（cursor 分頁、已讀／收藏內嵌切換）（2026-08-28）

TODO.md 建議開發批次第 7 批的第一部分。此前 feed 詳情頁只顯示 `GET /feeds/{feed_id}` 內嵌的最新
10 篇文章，完全沒用到 `GET /feeds/{feed_id}/articles`——這個 offset 分頁端點寫好之後，前端
`ArticleService.getArticles()` 從未被任何元件呼叫過。而且它排序只靠單一 `published_at` 欄位，
`published_at` 為 NULL 的文章會落進 Postgres `DESC` 預設的 `NULLS FIRST`，offset 分頁在新文章
持續進站的情況下也會讓「載入更多」重複或漏掉文章——同 migration 015 替我的閱讀流解決過的問題。

- **`backend/migrations/016_feed_article_list.sql`（新）**：`list_feed_articles(p_feed_id,
  p_user_id, p_cursor_sort_at, p_cursor_id, p_limit)`，`driftread` schema 內的 DB function，
  排序鍵與 cursor 形狀直接沿用 `list_reading_stream`（migration 015）的
  `COALESCE(published_at, fetched_at) DESC, id DESC`，索引也沿用同一個
  `articles_feed_id_sort_at_idx`，不需要新索引。`p_user_id` 可為 NULL——這個 function 服務公開
  端點，未登入呼叫時兩個 LEFT JOIN（`user_article_reads`／`user_bookmarks`，後者固定
  `bookmark_type = 'favorite'`）的條件都不成立，`is_read`／`is_bookmarked` 自然是 false，不需要
  額外分支。`SECURITY INVOKER`，EXECUTE 只授權 `service_role`，同 `list_reading_stream` 一套
  鎖法。
- **`backend/models.py`**：新增 `FeedArticle`（`ArticleSummary` 加 `fetched_at`／`is_read`／
  `is_bookmarked`）與 `PaginatedFeedArticles`；移除不再使用的 `PaginatedArticles`。
- **`backend/routers/articles.py`**：`GET /feeds/{feed_id}/articles` 從 `page`／`page_size`
  offset 分頁改成 `cursor`／`limit`（上限 100）keyset 分頁，寫法與 `routers/me.py` 的
  `/me/stream` 完全對稱（`decode_keyset_cursor` 解 400、`encode_keyset_cursor` 編下一頁
  cursor）。新增 `get_optional_user` 依賴（同 `routers/recommendations.py`／`discover.py` 已有的
  模式）：帶有效 token 時傳 `p_user_id`，否則傳 `None`，端點本身維持公開、不需要登入才能看文章
  列表。
- **`frontend/src/app/services/article.ts`**：`getArticles()` 簽名改成
  `(feedId, cursor?, limit?)`，回傳 `PaginatedFeedArticles`。
- **`frontend/src/app/components/feed-detail`**：文章列表獨立於 feed metadata 載入（各自的
  loading／error 狀態，互不阻塞），「載入更多」同 `reading-stream` 的 pattern；每列在登入後顯示
  已讀／收藏切換按鈕（未登入不顯示——單篇的已讀狀態沒有像訂閱那樣的「登入後回來完成」流程可以
  接，直接不出現比較誠實），樂觀更新＋失敗回滾＋pending 期間忽略重複點擊，寫法與
  `ReadingStreamService` 的 `markRead`/`markUnread` 同一套，但因為 feed 詳情頁本身不追蹤未讀數，
  這裡的 pending/patch 邏輯直接寫在元件裡，沒有另外拉一個 service。收藏固定走 `favorite` 類型
  （同「稍後讀」共用同一個 `POST /me/bookmarks`，這裡只是預設分類，UI 上仍可另外用既有的
  `/me/bookmarks` 頁面管理兩種收藏）。已讀文章的標題同 `reading-stream` 用 `.is-read` 降低對比而
  非劃掉，維持可讀性。
- **不做的部分**：`GET /feeds/{feed_id}` 內嵌的固定 10 篇 `articles` 欄位維持不動——沒有其他前端
  消費它，但這是開放 API 的一部分，拿掉有未知的外部風險，維持它的成本也接近零；`feed-detail` 頁
  本身已經不再讀這個欄位。
- **測試**：`backend/tests/test_articles.py` 新增 `list_feed_articles` 呼叫參數（含匿名／已登入
  兩種 `p_user_id`）、cursor 編解碼與分頁邊界的案例。`frontend/.../feed-detail.spec.ts` 新增
  `FeedDetail article list` describe block：首頁載入、載入更多、已讀／收藏樂觀更新與失敗回滾、
  pending 期間忽略重複點擊（用 `Subject` 卡住尚未 resolve 的請求驗證，而非假設 `of()` 的同步
  resolve 能測出 in-flight 狀態）、session 從 null 非同步解析出已登入身分後重新載入（見下）。
- **PR review 修正**（Codex）：`ngOnInit()` 原本只在元件建立時呼叫一次 `loadArticles()`；但
  `AuthService.session` 是非同步還原的持久化 session（見 `services/auth.ts`），直接訪問頁面時
  即使讀者其實已登入，`session()` 一開始仍是 `null`。原本的一次性載入會因此在還沒拿到 token 前
  就送出請求，`is_read`／`is_bookmarked` 全部回 false，且 session 還原後不會重新載入——同
  `bookmarks.ts`／`my-feeds.ts` 已經處理過的那類問題。改成建構子內的 `effect()`，依
  `auth.session()` 的使用者 id 觸發載入（`articlesLoadedFor` 記錄目前是替誰載入的，`undefined`
  代表「還沒載入過」，用來與「已登入但 id 為 null 沒有意義」的匿名情況區分），涵蓋初始匿名載入、
  session 非同步解析、登出與換帳號四種情況。測試沿用 `my-feeds.spec.ts` 的 pattern：
  `AuthService.session` 用真的 `signal()` 而非普通函式（否則 Angular 的 `effect()` 沒有訊號可以
  追蹤，測不出重新載入的行為），`session.set(...)` 後 `fixture.detectChanges()` 讓元件自己的
  effect 重新 flush。

  這個修法本身又引入新的競態：identity effect 觸發已登入的重新載入時，前一輪匿名請求可能還在
  飛行中——若它比已登入的回應晚到，會用全部是 false 的 `is_read`／`is_bookmarked` 蓋掉剛套用好
  的已登入狀態；同理，`loadMoreArticles()` 的回應若晚於一次新的 identity 重新載入落地，也會把
  舊身分的文章接到新載入的列表後面。Codex 第二輪抓到這點，修法是加一個 `articlesGeneration`
  計數器（`loadArticles()` 每次呼叫遞增，`loadMoreArticles()` 只讀取不遞增），`next`／`error`
  callback 落地時比對呼叫當下記下的值，不相符就丟棄——與 `ReadingStreamService` 的
  `_itemsGeneration` 同一套 pattern。新增迴歸測試：用 `Subject` 手動控制兩個請求的 resolve
  順序，讓匿名回應刻意晚於已登入回應落地，斷言最終畫面是已登入那份、不是被匿名回應蓋掉。

  Codex 第三輪接著抓出 `articlesGeneration` 還沒涵蓋到的另一半：讀者點了已讀／收藏切換、
  request 還沒回來時登出或換帳號，identity effect 會先把列表重新載入成新身分的資料，但舊切換的
  `error` callback 落地時原本會無條件把 `wasRead`／`wasBookmarked` 蓋回去——蓋的不是它自己那份
  已經不在畫面上的舊列表，而是新身分剛載入、正確的那份。`pendingRead`／`pendingBookmark` 也是同一
  個問題的另一面：舊切換的 callback 現在被 generation 檢查擋下，不會再走到原本清除 pending flag
  的那行，若同一個 article id 剛好也在新身分的列表裡（同一個 feed，通常就是），它的已讀／收藏
  按鈕會卡在永久 disabled。修法：`toggleRead()`／`toggleBookmark()` 進入時各自記下當下的
  `articlesGeneration`，`next`／`error` callback 落地時比對，不符就整段跳過；`loadArticles()`
  額外把 `pendingRead`／`pendingBookmark` 重置成空集合（放在遞增 generation 的同一個地方）——
  這兩個 pending set 的清除本來就只會發生在切換自己的 callback 裡，換代後那條路徑不會再走到，
  只能由取代它的那次載入自己負責清乾淨。迴歸測試：用 `Subject` 卡住一次 `markRead`，切換身分
  觸發重新載入並斷言 pending flag 立刻歸零（不是卡住），接著讓新身分的真實資料落地，最後讓卡住
  的舊 `markRead` 才失敗，斷言畫面停留在新身分的正確值、不是被蓋回舊的樂觀回滾值。
- **本 sandbox 的已知限制**：`pip install pytest`／`npm ci` 仍被 allowlist 擋下，backend／
  frontend 測試都無法在本機實際執行——與階段二十一至二十六相同；已用 `python3 -m py_compile`
  過 backend 改動、系統 `tsc`（`--ignoreConfig --noResolve`，僅語法檢查，過濾掉預期內的
  module-not-found／implicit-any／缺 test runner 型別錯誤後沒有其他訊息）過 frontend 改動，交給
  CI 實際跑過驗證。
- 對應文件更新：`docs/FEATURES.md`（Feeds/Articles API、文章預覽功能列、第 5 節資料表）、
  `TODO.md`（「Feed 完整文章列表」四項打勾，「建議開發批次」第 7 項改為進行中）。

## 階段二十八：`POST /api/discover/import` 改為要求登入（2026-09-03）

TODO.md「Auth 與安全」批次的第一項：這個端點原本 `get_optional_user`，未登入呼叫者一樣能把
遠端 feed 回應的第三方文字（`title`／`description`／`website_url`／`language`）直接 upsert 進
公開、無使用者範圍的 `feeds` catalog，供所有使用者的目錄瀏覽／發現／猜你喜歡共用。既有的
per-IP rate limit（每分鐘 20 次）擋不住輪換 IP 的長期灌入，且每次成功呼叫都會在全域目錄留下
一筆無法歸責的紀錄。

- **`backend/routers/discover.py`**：`discover_and_import` 的 `user` 參數從
  `AuthUser | None = Depends(get_optional_user)` 改成 `AuthUser = Depends(get_current_user)`，
  未帶合法 bearer token 在任何抓取或 DB 寫入之前就回 `401`。原本「已登入才順便訂閱」的
  `if user:` 分支跟著拿掉——訂閱一律發生。`POST /discover`（只回傳候選清單，從不寫入）維持
  公開不需要登入。
- **`frontend/src/app/components/discover/discover.ts`**：`importFeed()` 未登入時不再直接呼叫
  後端，改為導向 `/login?redirect=/discover`，同既有 `subscribeExisting()` 對已存在 feed 的
  處理模式；按鈕文字對應從「匯入到資料庫」改成「登入以匯入並訂閱」。
- **測試**：`backend/tests/test_discover.py` 新增未登入 401（DB 從未被呼叫）與已登入完整匯入
  ＋自動訂閱路徑兩個案例；既有的私網 URL／metadata URL／rate limit 系列測試（`test_discover.py`
  ／`test_main.py`／`test_rate_limit.py`）補上合法 bearer token，讓它們繼續驗證各自原本要測的
  行為（URL 驗證、rate limit），不被新加的 401 蓋過去。`discover.spec.ts` 新增對應的
  「未登入導向登入頁、不呼叫後端」案例，取代原本測「未登入匯入不標記訂閱」的案例（該行為已不
  適用——未登入現在根本不會呼叫匯入）。
- **本 sandbox 的已知限制**：`pip install pytest`／`npm ci` 仍被 allowlist 擋下，backend／
  frontend 測試都無法在本機實際執行——與階段二十一至二十七相同；已用 `python3 -m py_compile`
  與 `ruff check` 過 backend 改動、系統 `tsc`（`--ignoreConfig --noResolve`）過 frontend 改動，
  交給 CI 實際跑過驗證。
- 對應文件更新：`docs/SECURITY.md`（新增 #30）、`TODO.md`（「Auth 與安全」該項打勾）。
