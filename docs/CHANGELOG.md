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

## 階段十六：補齊 001 / 002 / 004 的 `CREATE TRIGGER` / `CREATE POLICY` 存在性防護（PR #31，2026-08-03）

同一個「持續改善專案」排程任務，接續階段十「不在此 PR 範圍」記下的已知限制：`001_initial_schema.sql` 與 `002_user_features.sql` 的 `CREATE TRIGGER` / `CREATE POLICY` 語句沒有 `IF NOT EXISTS`，把 `_migrations` 整個清空後重跑會在 **001** 就因為「trigger 已存在」而中止——`main.py` 的 lifespan 在服務請求前呼叫 `run_migrations()`，中止的 migration 會讓容器開機失敗。005 其實已經替 `ADD CONSTRAINT` 補過同類 DO-guard，006 的四張新表也全套 guard，只是沒回頭補 001/002。

- **範圍比原先記錄的稍大**：檢查全部 7 個 migration 檔後發現 `004_enable_rls_on_public_tables.sql` 的兩個 `CREATE POLICY`（`feeds_public_read` / `articles_public_read`）同樣沒有防護。只修 001/002 沒有意義——修完之後從頭重跑仍會在 004 中止，等於沒解決「`_migrations` 被清空後可以安全重跑到底」這個實際目標，因此一併補上。003/005/006/007 本來就已是 `IF NOT EXISTS` / `DO`-guard / 純 `CREATE OR REPLACE FUNCTION`，不需要改動。
- **修法**：`CREATE TRIGGER` 比照 006 的既有寫法，用 `DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = ... AND tgrelid = '<table>'::regclass) THEN CREATE TRIGGER ... END IF; END $$;` 包住（001 的 `feeds_updated_at`、002 的 `user_preferences_updated_at`）。`CREATE POLICY` 用同樣的 `DO` 結構改查 `pg_policies`（依 `schemaname` / `tablename` / `policyname` 判斷），包住 002 的四個 owner policy 與 004 的兩個 public-read policy。判斷邏輯與被包住的 DDL 本身完全不動，純粹加上存在性檢查。
- **驗證**：這個 sandbox 剛好裝有本機 `postgresql-16` 且 `psql` / `sudo` 可用，因此沒有停在「改完只能靠推理」——實際起了一個暫時 cluster、建好 `anon` / `authenticated` / `service_role` 三個角色與 `auth.users` / `auth.uid()`，依序套用全部 7 個 migration 檔（成功），接著清空 `_migrations` 並從 001 重新整個跑一遍：修改前的版本會如預期在 001 的 `CREATE TRIGGER feeds_updated_at` 那行炸掉（`trigger "feeds_updated_at" for relation "feeds" already exists`），精確重現了階段十記錄的既知限制；修改後的版本 7 個檔案全數重跑成功，且 `pg_trigger` / `pg_policies` 查詢確認 `feeds_updated_at`、`user_preferences_updated_at` 兩個 trigger 與六個 policy 都仍然只有一份、沒有重複建立。過程沒有動到 `backend/tests/`——現有測試套件裡沒有任何測試對這幾個 migration 檔的原始內容做斷言，也沒有能連真實 PostgreSQL 的既有 migration 測試（`migrate.py` 需要 `DATABASE_URL` 才會執行，pytest 套件走的是 mock 掉 Supabase client 的路徑）。
