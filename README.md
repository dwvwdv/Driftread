# Driftread 漂流閱讀

RSS 推薦平台 — 挖掘你心儀的資訊源。

核心是「猜你喜歡」：從你訂閱與偏好的訊號中，推出你還不知道但可能喜歡的 RSS 源。

## 技術架構

| 層級 | 技術 |
|------|------|
| Frontend | Angular 21（Material）+ nginx |
| Backend | Python 3.12 / FastAPI |
| Database | Supabase Cloud（PostgreSQL + Auth）|
| 部署 | Docker image 推至 GHCR，以 docker-compose 運行 |

## 主要功能

- **信息源瀏覽**：分頁、分類 / tag 篩選、關鍵字搜尋
- **全文閱讀**：文章全文快取在平台內，直接讀
- **猜你喜歡**：依 category / tags / language 加權評分，推薦未訂閱的 feed
- **用戶系統**：Supabase Auth 註冊 / 登入；訂閱、已讀、收藏、稍後讀
- **Auto-discover**：貼任意網址，自動找出 RSS / Atom feed
- **OPML 匯入 / 匯出**：與 Feedly / Inoreader 互通
- **自動定期抓取**：獨立 `worker` 容器輪詢到期佇列，抓取間隔依實際更新狀況自適應（有新文章就縮短、沒動靜就拉長），並以 ETag / Last-Modified 避免重複下載
- **Feed 健康度監測**：連續失敗計數與原因，達 10 次自動封存
- **瀏覽器擴充**：`extension/` 目錄，安裝後可在任何網站一鍵加入 feed
- **開放 API**：`POST /api/admin/feeds/from-url` 供外部腳本匯入

完整功能與 API 清單見 [`docs/FEATURES.md`](docs/FEATURES.md)。

## 文件

| 文件 | 內容 |
|------|------|
| [`docs/FEATURES.md`](docs/FEATURES.md) | 當前功能、API 端點、資料表、生效中的各項限制 |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | 逐 PR 變更紀錄（#1–#21）與架構演進 |
| [`docs/SECURITY.md`](docs/SECURITY.md) | 安全加固紀錄（#14–#21）與改動時的注意事項 |
| [`extension/README.md`](extension/README.md) | 瀏覽器擴充安裝與設定 |
| [`CLAUDE.md`](CLAUDE.md) | 專案上下文與開發規則 |

---

## 部署（Docker Compose）

### 前置條件

- Docker + Docker Compose
- 一個 Supabase Cloud 專案
- 一個對外的反向代理，接在外部 Docker network `web_network` 上（compose 不對外發布 port）

### 步驟

```bash
# 1. 產生 .env（會從 .env.example 複製，並自動產生 ADMIN_API_KEY）
python3 scripts/gen_env.py

# 2. 依腳本提示，手動填入 Supabase 相關變數
#    SUPABASE_URL / SUPABASE_KEY / DATABASE_URL / SUPABASE_JWT_SECRET

# 3. 建立反向代理用的外部 network（若尚未存在）
docker network create web_network

# 4. 啟動
docker compose up -d
```

Compose 有三個服務：

| 服務 | 內容 |
|------|------|
| `api` | FastAPI。不發布 port，只能透過 `frontend` 的 nginx 容器存取（`/api/` 反向代理到 `api:8000`）|
| `worker` | 自動抓取排程器。與 `api` 共用同一個 image，只換 `command` 跑 `worker.py`。不對外服務，只接 `default` network |
| `frontend` | Angular build 產物 + nginx。接 `web_network` 供外部反向代理 |

資料庫 migration 由後端啟動時的 `backend/migrate.py` 自動套用 `backend/migrations/*.sql`。
`worker` 以 `depends_on: api: service_healthy` 等 migration 落地後才開始輪詢，自己不跑 migration。

不想跑 `worker` 的話，設 `FEED_REFRESH_ENABLED=false` 並改由外部排程器定期呼叫
`POST /api/admin/feeds/refresh-due`（需 `X-API-Key`）即可，抓取邏輯完全相同。

### 停止

```bash
docker compose down
```

### 環境變數

| 變數 | 必填 | 說明 |
|------|------|------|
| `SUPABASE_URL` | ✅ | Supabase 專案 URL（Dashboard → Settings → API）|
| `SUPABASE_KEY` | ✅ | **service_role key**，非 anon key。後端需繞過 RLS 寫入 feeds / articles |
| `DATABASE_URL` | ✅ | 直連 PostgreSQL 連線字串，供 migration 使用（Dashboard → Settings → Database）|
| `SUPABASE_JWT_SECRET` | ✅ | 驗證用戶 `Authorization: Bearer` token（Dashboard → Settings → API → JWT Settings）|
| `ADMIN_API_KEY` | ✅ | 後台 / 開放 API 的 `X-API-Key` 標頭，由 `gen_env.py` 自動產生 |
| `CORS_ORIGINS` | | 逗號分隔的允許來源，預設 `*` |
| `DISCOVERY_USER_AGENT` | | 所有對外抓取（discover 與 feed 匯入 / refresh）的 User-Agent，預設 `Driftread/1.0` |
| `FEED_REFRESH_ENABLED` | | 是否啟用自動抓取，預設 `true`。設 `false` 時 `worker` 記一行 log 後退出（`api` 不受影響）|
| `FEED_REFRESH_TICK_SECONDS` | | 多久掃一次到期佇列，預設 `300`。**這不是抓取頻率** —— 個別 feed 的間隔由 `feeds.fetch_interval_minutes` 自適應決定 |
| `FEED_REFRESH_BATCH_SIZE` | | 單輪最多處理幾個到期 feed，預設 `50` |
| `FEED_REFRESH_CONCURRENCY` | | 單輪同時對外抓取的上限，預設 `5` |
| `FEED_REFRESH_MIN_INTERVAL_MINUTES` | | 個別 feed 抓取間隔下限，預設 `15` |
| `FEED_REFRESH_MAX_INTERVAL_MINUTES` | | 個別 feed 抓取間隔上限，預設 `1440`（24 小時）|

> `SUPABASE_KEY` 是 service_role key，**絕對不可暴露給瀏覽器**。前端的 Supabase 設定在
> `frontend/src/environments/`，那裡用的是 anon key。

新增 / 移除環境變數時，必須同步更新 `.env.example`、`docker-compose.yml`、`scripts/gen_env.py` 三處（見 `CLAUDE.md`）。

### ⚠ 前端 Supabase 設定是 build 時決定的

上面那些環境變數只餵給 **backend**。瀏覽器端的 Supabase Auth 另外讀
`frontend/src/environments/environment.ts` 的 `supabaseUrl` / `supabaseAnonKey`，
而這兩個值在 `npm run build` 時就被編進 JS bundle —— compose 沒有任何 runtime 替換機制。

repo 內這兩個值目前是**空字串**，因此 GHCR 上由 CI 建出的 `driftread-frontend:latest`
也是空的。`AuthService` 遇到空值時 `isConfigured()` 回 `false`，
**註冊 / 登入 / 訂閱 / 已讀 / 收藏 / 稍後讀全部不會運作**（瀏覽功能與猜你喜歡的匿名模式不受影響）。

要啟用用戶功能，必須自己建前端 image：

```bash
# 1. 填入 frontend/src/environments/environment.ts
#    supabaseUrl:     https://your-project-id.supabase.co
#    supabaseAnonKey: <anon key，不是 service_role key>

# 2. 從原始碼重建 frontend image（compose 的 frontend 服務有 build: ./frontend）
docker compose build frontend
docker compose up -d frontend
```

（本地開發走 `npm start` 時讀的是 `environment.development.ts`，同樣要自己填。）

> ⚠ 自建的 image 沿用 `ghcr.io/dwvwdv/driftread-frontend:latest` 這個 tag，
> 所以之後跑 `docker compose pull` 會用官方（空值）image 蓋掉它。
> 長期部署建議 fork 後讓自己的 CI 推到自己的 GHCR，或在 compose 裡改成自己的 image 名稱。

> 這是目前的已知限制：沒有 runtime 注入機制，所以官方 image 無法直接開啟用戶功能。
> 若要讓同一個 image 在不同部署帶不同 Supabase 專案，需要改成啟動時載入
> `assets/config.json` 之類的作法——尚未實作。

---

## 本地開發

`.env` 有被 gitignore，全新 clone 不會有這個檔案，所以先產生它（與上面部署段的第 1、2 步相同；已經做過就跳過）：

```bash
python3 scripts/gen_env.py
# 再依提示手動填入 SUPABASE_URL / SUPABASE_KEY / DATABASE_URL / SUPABASE_JWT_SECRET
```

然後兩個服務各開一個 terminal，都從 repo 根目錄開始：

```bash
# Backend（terminal 1）
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --env-file ../.env   # http://localhost:8000，docs 在 /docs

# Backend 測試（在 backend/ 底下）
pytest
```

```bash
# Frontend（terminal 2）
cd frontend
npm install
npm start                                      # http://localhost:4200
```

前端 development 設定（`frontend/src/environments/environment.development.ts`）預設打
`http://localhost:8000/api`，因此本地開發時後端直接跑在 8000 即可，不需要 nginx。

後端只讀 `os.getenv()`，自己不載入 dotenv，所以 `--env-file` 是必要的 —— 少了它 `SUPABASE_URL` /
`SUPABASE_KEY` 會是空的，server 起得來但每個 API 請求都會失敗。
（`uvicorn[standard]` 已含 python-dotenv，不需額外安裝；也可以改成 `backend/.env` 並傳 `--env-file .env`。）

缺 `DATABASE_URL` 時 migration 只會發出警告，不會中斷啟動。

---

## CI / 部署流程

推送到 `main` / `master` 時，依改動路徑觸發對應 workflow（也支援手動 `workflow_dispatch`）：

| Workflow | 觸發路徑 | 內容 |
|----------|----------|------|
| `.github/workflows/backend.yml` | `backend/**` | pytest → build & push `ghcr.io/dwvwdv/driftread-api:latest` |
| `.github/workflows/frontend.yml` | `frontend/**` | `npm ci` + `npm run build` → build & push `ghcr.io/dwvwdv/driftread-frontend:latest` |

Pull request 會跑測試 / build，但不推 image。

### 必要的 GitHub Secrets

| Secret | 說明 |
|--------|------|
| `GITHUB_TOKEN` | 自動提供，用於推送 image 至 GHCR |

## 開發規則

- 分支命名：`claude/<task>-<id>`
- 所有變更先開 PR，不直接推 `main` / `master`
