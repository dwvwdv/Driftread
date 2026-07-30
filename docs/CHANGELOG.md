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

### 環境變數

新增六個（皆有預設值、不填也能跑），依 `CLAUDE.md` 要求同步 `.env.example`、`docker-compose.yml`、`scripts/gen_env.py` 三處：
`FEED_REFRESH_ENABLED`（`true`）、`FEED_REFRESH_TICK_SECONDS`（`300`）、`FEED_REFRESH_BATCH_SIZE`（`50`）、`FEED_REFRESH_CONCURRENCY`（`5`）、`FEED_REFRESH_MIN_INTERVAL_MINUTES`（`15`）、`FEED_REFRESH_MAX_INTERVAL_MINUTES`（`1440`）。

### 測試

新增 `tests/test_feed_refresh.py`（29 個）與 `tests/test_worker.py`（9 個），擴充 `test_admin.py`、`test_rss_parser.py`。測試總數 64 → 117。

### 不在此 PR 範圍

種子源目錄（`CLAUDE.md` 的「大量 RSS 源資料庫」）仍未實作，DB 依然是空的出貨。那是內容收集問題而非管道問題，留作獨立 PR；此 PR 完成後，種子匯入只要打 `POST /admin/feeds` 就會被排程器自動接手第一次抓取。
