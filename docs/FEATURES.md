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
| Auto-discover | ✅ | 貼任意網址自動找出 RSS / Atom feed | `services/feed_discovery.py`、`routers/discover.py` |
| OPML 匯入 / 匯出 | ✅ | 與 Feedly / Inoreader 互通 | `routers/opml.py` |
| Feed 健康度與自動封存 | ✅ | 連續失敗計數 + 原因；達 10 次自動封存 | `routers/admin.py` |
| 後台手動匯入（JSON） | ✅ | `POST /api/admin/feeds`，需 admin key | `routers/admin.py`、`components/admin` |
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
| POST | `/admin/feeds` | 批次匯入 feed（JSON） |
| POST | `/admin/feeds/from-url` | 從網址匯入單一 feed（開放 API 入口） |
| POST | `/admin/feeds/{feed_id}/refresh` | 重抓該 feed，更新文章與健康度 |
| PATCH | `/admin/feeds/{feed_id}/archive` | 封存 |
| PATCH | `/admin/feeds/{feed_id}/unarchive` | 解除封存 |
| GET | `/admin/feeds/unhealthy` | 健康度低於門檻的 feed，差的排前面。`threshold` 預設 50、`limit` 預設 200（上限 1000） |
| GET | `/admin/feeds/archived` | 已封存的 feed，`limit` 預設 200（上限 1000） |

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
| `feeds` | 001 + 003 | RSS 源本體（title / url / category / tags / language / archived_at…）＋健康度欄位 `consecutive_failures`、`last_failure_at`、`last_failure_reason`、`health_score` |
| `articles` | 001 | 快取文章，`feed_id` 外鍵 cascade delete |
| `user_feeds` | 002 | 訂閱關係 |
| `user_article_reads` | 002 | 已讀回報 |
| `user_bookmarks` | 002 | 收藏 / 稍後讀（`bookmark_type` 區分） |
| `user_preferences` | 002 | `preferred_categories` / `preferred_languages` |
| `_migrations` | `migrate.py` 自建 | 已套用的 migration 檔名 |

RLS：四張 `user_*` 表為 owner-only policy（002）；`feeds` / `articles` 開 RLS 並給 public read policy（004）。
後端使用 **service_role key** 繞過 RLS 進行寫入，權限改由 JWT 驗證與 `ADMIN_API_KEY` 控管。

索引：`feeds(category)`、`feeds(archived_at)`、`feeds(health_score)`、`articles(feed_id)`、
`articles(published_at DESC)`、`user_feeds(user_id)`、`user_feeds(feed_id)`、
`user_article_reads(user_id)`、`user_bookmarks(user_id, bookmark_type)`。

## 6. 生效中的限制與門檻

| 項目 | 值 | 位置 |
|------|-----|------|
| Request body | 6 MiB（超過回 413） | `main.py::MAX_REQUEST_BODY_BYTES` |
| 對外抓取回應上限 | 5 MiB（feed 與 discover 的 HTML 共用同一個上限） | `services/feed_discovery.py::MAX_FEED_BYTES` |
| OPML 檔案 | 5 MiB | `routers/opml.py::MAX_OPML_BYTES` |
| OPML outline 數 | 200 | `routers/opml.py::MAX_OPML_OUTLINES` |
| Rate limit | 20 req / 60s / IP / 端點 | `rate_limit.py` |
| Rate limiter 追蹤上限 | 10,000 clients | `rate_limit.py::MAX_TRACKED_CLIENTS` |
| 自動封存門檻 | 連續失敗 10 次 | `routers/admin.py::AUTO_ARCHIVE_FAILURE_THRESHOLD` |
| URL 欄位長度 | 2048 | `models.py`（discover 請求） |
| `search` 長度 | 200 | `routers/feeds.py` |
| `page_size`（feeds） | 預設 20、上限 100 | `routers/feeds.py` |
| Admin 列表 `limit` | 預設 200、上限 1000 | `routers/admin.py` |
| 對外抓取 User-Agent | `DISCOVERY_USER_AGENT`，預設 `Driftread/1.0` | `services/feed_discovery.py::user_agent()` |
| 偏好清單長度 | 各 50 | `models.py::UserPreferences` |
| Article upsert 批次 | 200 / 批 | `services/articles.py` |

## 7. 技術棧與依賴

| 層 | 內容 |
|----|------|
| Frontend | Angular 21（Material + CDK）、`@supabase/supabase-js`、nginx 提供靜態檔與 `/api/` 代理 |
| Backend | FastAPI、pydantic v2、httpx、supabase-py、pyjwt、beautifulsoup4、defusedxml、psycopg2-binary、uvicorn |
| DB | Supabase Cloud（PostgreSQL + Auth） |
| 測試 | pytest + pytest-asyncio，`backend/tests/`（13 個測試檔） |
| 部署 | GHCR image + docker-compose，前端接外部 `web_network` 供反向代理 |
