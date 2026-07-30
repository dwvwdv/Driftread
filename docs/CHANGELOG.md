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
| #12 | (合併 branch，無獨立內容) | 同 branch 的收尾合併。 |

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
