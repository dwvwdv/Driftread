# Driftread 漂流閱讀 — Project Context

## 專案概述

RSS 推薦平台，核心功能為「猜你喜歡」，幫助用戶挖掘心儀的資訊源。

### 功能需求

- **信息源瀏覽**：顯示 RSS 源資訊及文章預覽
- **全文閱讀**：支持在平台內閱讀文章全文
- **猜你喜歡**：根據用戶喜好推薦未知 RSS 源，幫助挖掘新資訊源
- **大量 RSS 源資料庫**：收集並維護大量 RSS 源
- **封存功能**：後台可將已不再更新的 RSS 源打入封存狀態
- **匙入功能**：後台可手動匙入 RSS 源（JSON 格式）
- **開放 API**：提供 API 端點，可隨時透過 API 匙入收集源

## 技術架構

| 層級 | 技術 |
|------|------|
| Frontend | Angular (latest) |
| Backend | Python (FastAPI) |
| Database | Supabase |
| Frontend 部署 | Cloudflare Pages |
| Backend 部署 | Cloudflare Workers (Python Workers) |

## 專案結構

```
driftread/
├── frontend/          # Angular 應用
│   └── wrangler.jsonc   # Cloudflare Pages 設定
├── backend/           # Python FastAPI
│   └── wrangler.jsonc   # Cloudflare Workers 設定
├── .github/
│   └── workflows/
│       ├── frontend.yml   # Angular build + Pages 部署
│       └── backend.yml    # Python 測試 + Workers 部署
├── CLAUDE.md
└── README.md
```

## CI / 部署

- 推送到 `main` 自動觸發對應 workflow
- `frontend/` 改動 → 只跑 `frontend.yml`
- `backend/` 改動 → 只跑 `backend.yml`

### 必要的 GitHub Secrets

| Secret | 說明 |
|--------|------|
| `CLOUDFLARE_API_TOKEN` | Cloudflare API Token（需有 Pages + Workers 權限）|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID |

## 開發規則

- 開發分支命名規則：`claude/<task>-<id>`
- 所有變更先開 PR，不直接推送 `main`

### 環境變數維護

**每次新增、移除或修改環境變數時，必須同步更新以下三個地方：**

1. `.env.example` — 範本與說明
2. `docker-compose.yml` — `environment:` 區塊
3. `scripts/gen_env.py` — missing 檢查或自動產生邏輯
