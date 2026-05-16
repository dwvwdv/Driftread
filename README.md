# Driftread 漂流閱讀

RSS 推薦平台 — 挖掘你心儀的資訊源。

## 技術架構

- **Frontend**: Angular (latest) + Cloudflare Pages
- **Backend**: Python FastAPI + Cloudflare Workers
- **Database**: Supabase

## 本地 / 自架部署（Docker）

### 前置條件

- Docker + Docker Compose

### 啟動

```bash
# 1. 生成所有 secret，自動寫入 .env
python3 scripts/gen_env.py

# 2. 啟動
docker compose up --build
```

| 服務 | 端點 |
|------|------|
| 前端 | http://localhost |
| API | http://localhost/api |
| Supabase Studio | http://localhost:54323 |
| PostgreSQL（直連）| localhost:5432 |

Migration 會在 `db` 容器首次啟動時自動套用 `supabase/migrations/` 內的所有 `.sql`。

### 停止

```bash
docker compose down          # 保留資料
docker compose down -v       # 清除資料（重置資料庫）
```

---

## 開發（不用 Docker）

```bash
# 先啟動資料庫層（或用上面的 docker compose）
docker compose up db rest meta kong -d

# Frontend
cd frontend && npm install && ng serve

# Backend
cd backend && pip install -r requirements.txt && uvicorn main:app --reload
```

## 部署

推送到 `main` branch 後，GitHub Actions 自動部署：
- Frontend → Cloudflare Pages (`driftread`)
- Backend → Cloudflare Workers (`driftread-api`)

## 必要的 GitHub Secrets

| Secret | 說明 |
|--------|------|
| `CLOUDFLARE_API_TOKEN` | Cloudflare API Token（需有 Pages + Workers 權限）|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID |

## 後端額外環境變數

| 變數 | 說明 |
|------|------|
| `SUPABASE_JWT_SECRET` | Supabase Auth 的 JWT secret，用於驗證 `Authorization: Bearer` token |
| `DISCOVERY_USER_AGENT` | Auto-discover 抓取網頁時使用的 User-Agent（選填）|

## 主要功能

- **信息源瀏覽 / 閱讀 / 推薦**：核心三件套
- **用戶系統**：Supabase Auth 註冊 / 登入；訂閱、已讀、收藏、稍後讀
- **Auto-discover**：貼任意網址，自動找出 RSS / Atom feed
- **OPML 匯入 / 匯出**：與 Feedly / Inoreader 互通
- **Feed 健康度監測**：連續失敗自動降低分數、達門檻自動封存
- **瀏覽器擴充**：`extension/` 目錄，安裝後可在任何網站一鍵加入 feed
- **Open API**：`/api/admin/feeds/from-url` 端點供外部腳本匯入
