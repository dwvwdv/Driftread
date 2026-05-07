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
cp .env.example .env
# 編輯 .env — 至少設定 POSTGRES_PASSWORD 和 ADMIN_API_KEY
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
docker compose up db rest auth meta kong -d

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
