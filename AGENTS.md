# Driftread 漂流閱讀 — AI 協作規範

> **本檔是 Claude Code 與 Codex 共用的唯一專案規範**。`CLAUDE.md` 只有一行 `@AGENTS.md`，
> 負責讓讀不到 `AGENTS.md` 的 Claude Code session 也拿得到同一份內容——**不要在 `CLAUDE.md` 寫任何東西**。
>
> **本檔只放索引與全域規則，細節一律寫進 `docs/`** ⚠️：Codex 讀 `AGENTS.md` 的預設上限是 32 KiB，超過的部分會被直接截掉，
> 而 Claude 每次對話都會整份載入本檔。`scripts/check_docs.py` 擋下超過 30 KiB 的本檔。
> 新功能的設計理由、踩過的坑寫進下方「改 X 前先讀 Y」對應的那份文件，本檔頂多加一行連結。

## 專案概述

RSS 推薦平台，核心功能為「猜你喜歡」，幫助用戶挖掘心儀的資訊源。

- **信息源瀏覽**：顯示 RSS 源資訊及文章預覽
- **全文閱讀**：支持在平台內閱讀文章全文
- **猜你喜歡**：根據用戶喜好推薦未知 RSS 源，幫助挖掘新資訊源
- **大量 RSS 源資料庫**：收集並維護大量 RSS 源（自動抓取＋主動發現）
- **封存功能**：後台可將已不再更新的 RSS 源打入封存狀態
- **匯入功能**：後台可手動匯入 RSS 源（JSON 格式）
- **開放 API**：提供 API 端點，可隨時透過 API 匯入收集源

功能、API、資料表與生效中的限制的**當前狀態**一律以 [docs/FEATURES.md](docs/FEATURES.md) 為準，本檔不重複列。

## 技術架構

| 層級 | 技術 |
|------|------|
| Frontend | Angular (latest)，自建 Offbeat 元件庫（Nord × Brutalism）|
| Backend | Python 3.12 (FastAPI)；`api` 與 `worker` 兩個容器共用同一個 image |
| Database | Supabase Cloud（專屬 `driftread` schema）|
| 部署 | Docker（image 推至 GHCR，docker-compose 運行）|

## 專案結構

```
driftread/
├── frontend/          # Angular 應用（Dockerfile → GHCR）
│   ├── src/styles/    # Offbeat design token 層（Nord × Brutalism）
│   └── src/app/ui/    # 自建 Offbeat 元件庫 —— 不要重新引入 Angular Material
├── backend/           # Python FastAPI（Dockerfile → GHCR）
│   ├── migrations/    # 開機時由 migrate.py 依檔名順序自動套用
│   ├── routers/       # HTTP 端點
│   ├── services/      # 抓取、發現、保存、設定等業務邏輯
│   └── tests/         # pytest（含真 PostgreSQL 的 SQL fixture）
├── supabase/          # migration 010 的可審閱分層原稿（與 010 必須逐字一致）
├── extension/         # 瀏覽器擴充（一鍵加入 feed）
├── docs/              # 專案文件（見下方「改 X 前先讀 Y」）
│   └── changelog-archive/  # 舊階段的變更紀錄，只搬不改
├── scripts/
│   ├── gen_env.py     # 產生 .env
│   └── check_docs.py  # 文件一致性檢查（CI：docs.yml）
├── docker-compose.yml # 正式環境部署（api / worker / frontend）
├── TODO.md            # 開發路線與待辦
├── AGENTS.md          # 本檔：AI 協作規範的唯一來源
├── CLAUDE.md          # 只有一行 @AGENTS.md
└── README.md
```

### 資料與外連說明 ⚠️

撰寫文件、PR 或對外說明時，以下事實不可含糊帶過：

- **使用者資料**（訂閱、已讀、收藏／稍後讀、推薦回饋、偏好）存在 Supabase `driftread` schema 的 `user_*` 表，RLS 為 permanent-user owner-only。
  但 **backend 目前全程使用 `service_role` client**，使用者隔離實際上靠應用層手動加 `user_id` 條件（`tests/test_me_isolation.py` 守住），
  RLS 只是縱深防禦——**新增任何 `/me/*` 查詢都必須自己帶 `user_id` 條件並補隔離測試**（現況與後續計畫見 `TODO.md` Phase 0）。
- **伺服器會主動對外連線**：排程抓取已收錄的 feed、使用者觸發的 Auto-discover、後台匯入，以及**預設關閉**的主動發現（`FEED_DISCOVERY_ENABLED`）。
  所有對外抓取都經過 SSRF 守門與大小上限，主動爬取遵守 robots；不要寫成「只在使用者操作時連網」。
- `service_role` key 只屬於 backend；前端只拿 anon／publishable key，且是容器啟動時 render 進 `env.js` 的 runtime config，不編進 bundle。

## 改 X 前先讀 Y ⚠️

不要只根據目前的 diff 推測專案規則：下列文件記錄了刻意的設計決策與已接受的 trade-off，動到對應的程式前先讀完相關段落。

| 要動的東西 | 先讀 |
|-----------|------|
| 任何對外抓取（feed、discover、OPML 目錄、robots）、XML 解析、PostgREST filter、公開端點、第三方文字落庫、認證 | [docs/SECURITY.md](docs/SECURITY.md) 的「目前的防線總覽」與「改動時要注意的事」 |
| 推薦邏輯（猜你喜歡的評分、候選抽樣、回饋權重） | [docs/FEATURES.md](docs/FEATURES.md) 的「推薦邏輯（猜你喜歡）」 |
| 自動抓取排程、自適應間隔、健康度與自動封存 | [docs/FEATURES.md](docs/FEATURES.md) 的「自動抓取管道」 |
| 主動發現（外連採集、blogroll、目錄頁、探測、候選審核） | [docs/FEATURES.md](docs/FEATURES.md) 的「主動發現管道」、[docs/SECURITY.md](docs/SECURITY.md) #24 |
| 文章寫入、增量抽取、正文保存／縮減、worker heartbeat、中文種子 | [docs/CRAWLER_OPERATIONS.md](docs/CRAWLER_OPERATIONS.md) |
| `app_settings` 全域設定、探測方向與名額、設定版本衝突 | [docs/GLOBAL_SETTINGS.md](docs/GLOBAL_SETTINGS.md) |
| 資料表、RLS、schema 隔離、migration | [docs/FEATURES.md](docs/FEATURES.md) 的「資料表」、本檔「資料庫與 migration」 |
| API 端點、前端路由、各項上限與門檻 | [docs/FEATURES.md](docs/FEATURES.md) 的「API 端點」「前端路由」「生效中的限制與門檻」 |
| 前端樣式、元件庫、主題、文章內容渲染 | [frontend/README.md](frontend/README.md) 的「三條規則」「主題」「對比度」 |
| 部署順序、會動到 Supabase Dashboard 設定的變更、回滾 | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| 瀏覽器擴充 | [extension/README.md](extension/README.md) |
| 某個功能為什麼長這樣、過去 review 否決過什麼 | [docs/CHANGELOG.md](docs/CHANGELOG.md)（舊階段在 [docs/changelog-archive/](docs/changelog-archive/)）|

## 快速開始

```bash
python3 scripts/gen_env.py                      # 產生 .env，再依提示填 Supabase 相關變數

# Backend
cd backend && pip install -r requirements.txt
uvicorn main:app --reload --env-file ../.env    # 後端不自己載 dotenv，--env-file 必要
pytest                                          # 提交前應全數通過

# 需要真 PostgreSQL 的測試（未設定時自動 skip，CI 一定會跑）
DRIFTREAD_TEST_DATABASE_URL=postgresql://... pytest

# Frontend
cd frontend && npm ci
npm start                                       # http://localhost:4200
npm test && npm run build                       # 提交前兩者都要過（含 anyComponentStyle 預算）

# 文件一致性（改到任何 .md、改名或刪檔時）
pip install -r scripts/requirements-docs.txt
python3 scripts/check_docs.py
```

部署與環境變數的完整說明見 [README.md](README.md) 與 [docs/RUNBOOK.md](docs/RUNBOOK.md)。

## CI / 部署

依改動路徑觸發對應 workflow（也支援 `workflow_dispatch`）；PR 只跑測試／build，推送到 `main`／`master` 才推 image（`:latest` 與 `:sha-<commit>`）：

| Workflow | 觸發路徑 | 內容 |
|----------|----------|------|
| `.github/workflows/backend.yml` | `backend/**`、`supabase/**` | pytest（含 PostgreSQL 17 service）→ `driftread-api` image |
| `.github/workflows/frontend.yml` | `frontend/**` | `npm test` + `npm run build` → `driftread-frontend` image |
| `.github/workflows/docs.yml` | 每次 PR／push（不設路徑篩選，改名或刪檔也可能讓文件連結失效） | `scripts/check_docs.py` |

必要的 GitHub Secrets 只有自動提供的 `GITHUB_TOKEN`（推送 image 至 GHCR）。

## 開發規範

### 分支與 PR

- 開發分支命名規則：`claude/<task>-<id>`
- 所有變更先開 PR，不直接推送 `main`／`master`
- 完成變更並通過適當驗證後，自動上傳專案分支並建立非 draft 的 Open PR，附上變更說明與驗證結果
- 每次建立或更新 PR 後，檢查 review 並確認事件 hook 訂閱；收到有效的修改要求後修復、驗證與更新 PR。Hook 工具不可用時明確回報；使用者目前選擇不建立替代輪詢排程
- 沙箱裡跑不了的驗證（Docker、真 Supabase、瀏覽器）要在 PR 與 changelog 裡如實寫「未驗證」與原因，不可寫成已驗證

### 環境變數維護 ⚠️

**每次新增、移除或修改環境變數時，必須同步更新以下三個地方：**

1. `.env.example` — 範本與說明
2. `docker-compose.yml` — `environment:` 區塊（`api` 與 `worker` 都用到的變數兩個區塊都要加）
3. `scripts/gen_env.py` — missing 檢查或自動產生邏輯

漏掉 compose 那一處時，`.env` 設了也不會進容器（SECURITY.md #15 與 CHANGELOG 階段八都踩過）。

### 資料庫與 migration ⚠️

- `backend/migrate.py` 在 `api` 開機時依**檔名排序**套用 `backend/migrations/*.sql`，以 `driftread._migrations` 記錄**檔名**，並以 advisory lock 與 backfill 序列化。
- **新的 migration 一律用 `YYYYMMDDHHMMSS_<name>.sql` 命名**（舊的 `0NN_` 系列已停止新增，時間戳排序必在其後）。
- **已合併的 migration 檔不得修改**：ledger 只看檔名，改了內容不會在已部署的資料庫重跑；要修正就新增一支 migration。
- migration 要能在「ledger 被清空後重跑」時不炸：`CREATE ... IF NOT EXISTS`，`CREATE TRIGGER`／`CREATE POLICY`／`ADD CONSTRAINT` 用存在性檢查的 `DO` 區塊包住。
- 新表一律建在 `driftread` schema、開 RLS，並明確決定 policy（使用者資料 owner-only，系統表零 policy 僅 service_role）；function 預設 `SECURITY INVOKER`。
- `supabase/` 的三支分層原稿（`supabase/1_create_schema.sql`～`3_rls_policies`）與 `backend/migrations/010_schema_access.sql` 必須逐字一致（`tests/test_migrations.py` 守住），改一邊就要改另一邊。
- 動到 SQL 函式、RPC 或鎖語意時，在 `backend/tests/sql/` 補真 PostgreSQL 的 fixture——mock 的 client 驗不到 SQL 本身。
- 會動到 Supabase Dashboard 設定（Exposed Schemas、grant）的變更，照 [docs/RUNBOOK.md](docs/RUNBOOK.md) 的對應段落寫部署順序。

### Backend

- DB 一律透過 `database.get_client()`（固定 `driftread` schema 的 scoped client）；PostgREST filter 用 `.eq()`／`.in_()` 等結構化 API，第三方字串不進 `.or_()`，「可能查不到」用 `.maybe_single()`。
- 直連 PostgreSQL（psycopg2）時參數一律用 `%s` 佔位符傳入，禁止把任何外部輸入串接進 SQL。
- 對外抓取只能走 `fetch_with_cap()`／`fetch_and_parse()`，XML 只能用 `defusedxml`；其餘安全規則見 SECURITY.md 的「改動時要注意的事」。
- 日誌用模組層級的 `logger = logging.getLogger(__name__)`，不要用 `print()`；log 與錯誤回應不得帶出 token、key 或原始外連例外文字。
- 測試用的 db mock 避免裸 `MagicMock()` 讓錯誤的方法名靜默通過（SECURITY.md #20）。

### Frontend

- 不要重新引入 Angular Material；元件 SCSS 只用語意 token，不碰 `--nord-*`；原生元素的塗裝放全域 class（細節見 [frontend/README.md](frontend/README.md)）。
- 文章內容一律走 `[innerHTML]` 讓 Angular sanitizer 作用，**永遠不要用 `bypassSecurityTrustHtml`**。
- 樂觀更新一律搭配失敗回滾，並處理「在途寫入 vs 並發 GET」的 race（參考 `SubscriptionService`、`ReadingStreamService` 的作法）。
- 新增或改動的 service／元件行為要有對應的 `*.spec.ts`。

## Changelog 維護

- **每個 PR 都必須在 [docs/CHANGELOG.md](docs/CHANGELOG.md) 最下方新增一個「階段」條目**：`## 階段N：<一句話摘要>（PR #xx，YYYY-MM-DD）`。新功能、bug 修復、UI 調整、依賴升級、文件結構調整都要記。
- **一個 PR 只有一個條目**：同一個 PR 內的 review 修復、追加調整併入同一條目（例如加「review 過程中修掉的問題」小節），不另開新階段。
- 條目要寫**為什麼**與**沒做什麼**：根因、設計決策、被否決或延後的 review 建議（「已知未處理」「不在此 PR 範圍」），以及實際跑過哪些驗證。
- 階段編號接續上一個條目，不重複、不跳號。PR 號碼未知時先寫 `PR 待定`，開 PR 後補上。
- `docs/CHANGELOG.md` 只保留最近兩個十位段，更早的階段歸檔在 `docs/changelog-archive/`（搬移規則寫在 CHANGELOG.md 開頭）。
- 功能、API、資料表或限制有變時，同一個 PR 內一併更新 [docs/FEATURES.md](docs/FEATURES.md)；安全相關變更另在 [docs/SECURITY.md](docs/SECURITY.md) 補一筆並視需要更新「目前的防線總覽」「改動時要注意的事」；`TODO.md` 對應項目打勾並註明實作位置。

## 文件維護

- **本檔與 `docs/` 不重複**：同一件事只寫在一個地方，其他地方放連結。兩份文件各自維護同一份說明，就是它們落後於程式碼的原因。
- **現況文件 vs 歷史紀錄**：`FEATURES.md`、`CRAWLER_OPERATIONS.md`、`GLOBAL_SETTINGS.md`、`RUNBOOK.md` 與各 README 描述**現在**的樣子，改程式時一併改；`CHANGELOG.md` 與 `changelog-archive/` 是歷史紀錄，舊條目不回頭改寫。
- 程式碼註解引用設計理由時，指向 `docs/` 的文件與段落名稱（例如「見 docs/SECURITY.md 的『改動時要注意的事』」），不要寫章節編號——編號會隨文件重排失效。
- `scripts/check_docs.py` 檢查：`CLAUDE.md` 只能是 `@AGENTS.md`、本檔不超過 30 KiB、所有 Markdown 文件的相對連結都存在、本檔以反引號提到的路徑都存在。改名或刪除檔案時一併更新文件。

---

以下為 Pull Request 與 Code Review 的規範，Codex 與 Claude 一體適用。

## GitHub Pull Request 語言規範

所有 GitHub Pull Request 的 title、body、comment、review 回覆與 review 結果，
一律使用繁體中文。技術識別字、程式碼、命令與 API 名稱可保留原文。

---

## Code Review 原則

進行 Pull Request Review 時，優先找出「實際值得修、會影響產品」的問題。

不要為了理論完整性，持續追查極低機率、刻意構造、正常使用幾乎不可能出現的邊界情況。

### 什麼問題值得提出

只有至少符合以下一項時，才應提出 finding：

- 正常使用流程或合理可預期的操作可能觸發
- 可能造成資料錯誤、資料遺失、重複資料，或使用者看到不屬於自己的資料
- 可能造成安全或隱私問題（SSRF、注入、越權、未認證寫入、密鑰外洩、XSS）
- 可能造成 500、服務無法啟動，或核心功能（閱讀、訂閱、抓取、推薦）無法使用
- 是本 PR 新增或明顯暴露出的 concurrency、lifecycle、migration 或 compatibility 問題
- 問題雖低機率，但一旦發生會造成嚴重且持久的資料完整性問題

### 什麼問題通常不要提出

以下情況通常不應列為 Review finding：

- 必須輸入數百個搜尋詞、極端長文字或刻意構造巨大輸入才會發生，且已有 request body、rate limit 或長度上限擋住
- 純粹為了撞 PostgreSQL／PostgREST 參數數量、URL 長度、整數上限、collection size 等底層實作限制
- 需要極端不合理 timing 才可能發生，而且沒有明確產品影響的理論 race condition
- 必須手動破壞資料庫、繞過 migration、修改容器內部檔案或違反既有 invariant 才能觸發
- 現有行為在正常使用範圍內已經正確，只是還可以做更多 defensive hardening
- 主要收益只是架構更漂亮、更加通用、未來可能更容易擴充，而不是修正實際問題
- 與本 PR 無直接關係的既有問題，除非它會直接讓這次修改無法正確運作
- 本檔或 `docs/` 已明確記錄並接受的 trade-off（CHANGELOG 的「已知未處理」「不在此 PR 範圍」、SECURITY.md 記下的限制），且本 PR 沒有改變相關前提

**例外——外部可觸發的安全問題**：本專案有公開免認證端點，且伺服器會對第三方 URL 發出連線。
只要攻擊者能從外部（公開端點、可被收錄的 feed 內容、被抓取的網頁）觸發，就不算「刻意構造的極端輸入」，照常提出。

### Edge case 處理原則

遇到極端 edge case 時，優先考慮**簡單的產品限制**，而不是增加大量實作機制。

例如：搜尋輸入極端長時可能撞 PostgreSQL 或 PostgREST 的限制，應優先：

- 限制合理的搜尋文字長度與詞數
- 回傳清楚的 422 錯誤

而不是為了精準支援底層的每一個極限，加入更多 SQL 分支、狀態與 recovery 邏輯。

---

## Finding 提出門檻

提出 finding 前，先確認：

1. 這個問題是本 PR 引入或明顯暴露的
2. 有具體、可描述的實際觸發流程
3. 有明確的使用者、資料或安全影響
4. 問題的嚴重程度值得增加程式碼與長期維護成本
5. 沒有更簡單的產品限制能合理解決
6. 本檔與 `docs/` 沒有已經把這件事列為刻意接受的限制

每個 finding 應說明：

- **實際觸發方式**
- **具體影響**
- **為什麼值得在這個 PR 修**

不要只證明「理論上可能發生」。

---

## Review 優先級

優先檢查：

1. Security：SSRF、注入、越權（跨使用者資料）、未認證寫入、XSS、密鑰外洩
2. 靜默資料錯誤、資料遺失或重複寫入
3. Transaction / atomicity、advisory lock 與 worker 間的競跑
4. 正常操作可以觸發的 race condition（含前端樂觀更新與並發 GET）
5. 核心 workflow regression（閱讀、訂閱、抓取排程、推薦、發現）
6. Migration / schema / RLS / 新舊版本混跑的 compatibility
7. 明確且可重現的 UI 行為錯誤

低優先級：

- 純架構潔癖
- 理論 extensibility
- 極端輸入
- 微小 defensive hardening
- 幾乎不可能達到的 implementation limit

---

## 產品規模假設

這是一個**自架、單一部署的 RSS 推薦平台**（一組 `api` + `worker` + `frontend` 容器，搭配 Supabase Cloud），
不是要水平擴展到大型流量的搜尋引擎或企業資料平台。

Review 應以合理的使用者數、feed 數與文章量為前提；不要因為底層 library 理論上允許無限輸入，就要求支援刻意構造的極端資料。
但公開端點與對外抓取面對的是不受信任的網路，安全相關的輸入不適用這條放寬（見上方「例外」）。

如果合理的產品限制可以解決，就採用產品限制。

---

## Review Scope

不要把 Review 變成無限延伸的全專案 audit。

當一個 finding 被修正後，應檢查：

- 修正本身是否正確
- 是否造成直接 regression
- 是否破壞本 PR 涉及的既有 invariant

不要因為修正了一個問題，就沿著所有理論依賴一路擴展到與本 PR 幾乎無關的歷史問題。

Review 的目標是：

> 判斷這個 PR 是否值得安全地合併。

不是：

> 證明整個程式在所有理論輸入與所有可能執行順序下都完美。

**寧可少報一個 technically correct 但實際沒有產品價值的問題，也不要為了找到問題而找問題。**
