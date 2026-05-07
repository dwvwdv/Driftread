# Driftread 漂流閱讀

RSS 推薦平台 — 挖掘你心儀的資訊源。

## 技術架構

- **Frontend**: Angular (latest) + Cloudflare Pages
- **Backend**: Python FastAPI + Cloudflare Workers
- **Database**: Supabase

## 開發

```bash
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
