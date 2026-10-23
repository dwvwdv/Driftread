# 批量清理與待探索來源

`/manage/cleanup` 提供來源／文章清理，`/manage/pending-sources` 提供待探索網址清單。REST 沿用管理員帳密＋Telegram session 或 `ADMIN_API_KEY`。REST 與 MCP 共用 `services/cleanup.py` 及 service_role-only SQL RPC；不呼叫外部模型或抓取網站。

## 查找與選取

`GET /api/admin/cleanup/items` 接受 `kind=feeds|articles`、`rule`、`state=active|staged|all`、`query`（最多 200 字）、`limit`（1–50）、`offset`。回傳 `{items,total,limit,offset}`，總數是符合條件的項目數，依群組大小、分組鍵、ID 排序。

| 對象 | rule | 整理線索 |
|---|---|---|
| 全部 | `all` | 所有項目，包含沒有相似項目的項目 |
| 來源／文章 | `category` | 完全相同的非空來源分類 |
| 來源 | `host` | website_url（缺省時 feed URL）的 host |
| 來源／文章 | `title` | 小寫並折疊空白後的相同標題；文章標題至少八字 |
| 文章 | `url` | 相同 canonical URL，目前僅去掉 fragment |
| 文章 | `content` | 正文至少 100 字且 content hash 相同 |

除 `all` 外只列群組大小大於一的項目。分組計數先套用 active／staged 狀態，再套搜尋。這些是整理線索，沒有語意模型、品質判定或自動刪除。可透過全部清單與搜尋自行選取同主題項目；不把同分類或同網域當成必須刪除的鏡像。

## 預覽、暫存與刪除

`POST /api/admin/cleanup/batch` 接受 `{kind,ids,action,dry_run,confirm_delete}`。`ids` 為 1–50 UUID、去重；`action=stage|restore|delete`，預設 `dry_run=true`。每個項目回 `ok` 及 articles／subscriptions／bookmarks／reads 影響計數；找不到回 `not_found`，刪除使用中項目回 `stage_first`。正常業務衝突逐筆回報，其餘項目繼續；非預期資料庫錯誤使整筆交易回滾。

正式永久刪除由 REST／MCP 共用 adapter 透過既有 `DATABASE_URL` 呼叫同一 RPC，連線後以 `SET LOCAL ROLE service_role` 維持原權限，避免數千篇文章的 cascade 超過 PostgREST 預設短逾時。連線上限 5 秒、整筆 SQL 上限 45 秒、等鎖上限 3 秒；失敗回傳可重試的 503 並回滾整筆交易，不輸出原始資料庫錯誤。缺少 DATABASE_URL 時明確拒絕；預覽、暫存、還原仍走原 Data API。45 秒上限低於內建 nginx 的 60 秒預設等待時間；外層代理若另有較短上限，需至少容納此操作。

- 來源暫存沿用 `feeds.archived_at`：暫停排程抓取及收割，既有 normal 文章與訂閱關係仍可閱讀。還原清除封存、設 `next_fetch_at=now()`，沿用人工解除收錄檢查封存的語義，不改角色與正文政策。已開始的抓取不保證即時停止。
- 文章暫存使用 `articles.staged_at`：統一 `article_publications` projection 排除它，來源預覽、全文、時間流、搜尋、收藏、日報、RSS、個人唯讀 MCP 與同步授權清單不再提供它。保留 ID、版本、正文、已讀及收藏，還原後使用原 ID；[每來源文章上限](CRAWLER_OPERATIONS.md#每來源文章上限)不會淘汰暫存文章，也不把它計入上限。article_count 仍代表實體文章數，包含暫存文章。
- 永久刪除前必須先暫存，明確傳 `dry_run=false` 與 `confirm_delete=true`。來源刪除依 FK 一併刪除其文章、版本、使用者訂閱／回饋／收藏／已讀等關聯；文章刪除只影響所選文章及其關聯，不合併或轉移使用者資料。前端先預覽影響筆數再確認；預覽是當時快照，正式寫入仍重查。
- 私有 `article_cleanup_tombstones(feed_id,url)` 避免文章下次 RSS 更新又被插回；刪除與標記共用交易及來源列鎖，與 ingestion 序列化。來源本身刪除後不再排程；不新增 host 封鎖或阻止管理員未來明確重新匯入的政策。

前端每次最多處理當頁 50 項。換頁／重新查詢清除選取；處理後保留仍在當頁的失敗項目，舊 GET 回應不能覆蓋異動後清單。

## 待探索清單與取消

`GET /api/admin/discovery/pending-targets` 列 `status=pending`（包括重試退避與已選入本輪的項目），依引用來源數、下次探索時間、ID 排序，`limit` 1–50、`offset>=0`。頁面顯示 URL／host、發現方式、引用數、嘗試次數、下次探索時間與最近失敗，可單筆或批次移除。

`POST /api/admin/discovery/targets/remove` 接受 `{ids}`（1–50 UUID），逐筆回 `ok` 或 `not_found|target_completed`。pending 改成 `removed`，重複移除冪等成功；已完成者，以及已有候選 INSERT 提交但目標尚未更新為 done 的項目，回衝突，不刪已建立候選或已收錄來源。保留 URL 取消標記，收割／種子／目錄重新看見也不復活。同 host 的其他 URL 不受影響，與既有永久封鎖 host 不同。

worker 執行前重查 pending；已送出的網路請求可能完成。trigger 用 target 列鎖保護候選 INSERT，取消先提交時後到結果不能新增候選；晚到的 worker 更新不能把 removed 改回 pending。歷史列可在 `/manage/frontier` 的 removed 篩選檢視，管理員仍可明確改為整個網域封鎖（rejected），封鎖後也不接受晚到候選。未新增解除取消操作。

## 管理 MCP 與權限

沿用 `/api/admin/mcp/`，新增六個工具：

| 工具 | 行為 |
|---|---|
| `list_cleanup_items` | 與 REST 相同的分組／搜尋及有界分頁 |
| `apply_cleanup_batch` | 預設 dry-run，明確傳選取 ID；刪除有相同暫存及確認條件 |
| `list_pending_discovery_targets` | 唯讀待探索網址及精確總數 |
| `remove_pending_discovery_targets` | 移除 1–50 個待探索網址 |
| `get_discovery_control` | 讀取探索開關、部署限制與版本 |
| `set_discovery_enabled` | 依 observed version 暫停／啟用探索，保留佇列 |

六個工具要求獨立 `catalog:manage`，異動工具標示 `destructiveHint=true`。機器 key 由可選 `CATALOG_MANAGEMENT_MCP_KEY` 設定，必須與管理／評審 key 不同，空白停用；此 key 只能使用六個新工具。只有 API 使用 MCP 機器認證，worker 不注入此 key。

初始 OAuth challenge 與未指定 scope 的 DCR 預設請求 `source:review catalog:manage offline_access`；明確的窄 scope 仍保留。ChatGPT OAuth 請求 `source:review catalog:manage offline_access`，管理員帳密＋OTP 同意頁列出批量暫存／還原／永久刪除、移除待探索來源與暫停／啟用探索。舊 grant 保留原 scope，不自動擴權；需要新增權限時重新授權。完整 scope 經 token／refresh 保留，每次工具呼叫分別驗證。來源評審專用 key 與只有 `source:review` 的 OAuth grant 不能呼叫新工具，回 `FORBIDDEN`；個人唯讀 `/api/mcp/` 不變。除自動探索開關外，不開放任意全域設定或人工評審覆寫。

## 部署與驗證

先停止舊 worker，由 API 套用 `20261018033131_admin_cleanup.sql` 與 `20261018034910_discovery_control.sql`，再更新 worker 與 frontend。探索控制的執行語義見 [全域設定](GLOBAL_SETTINGS.md#自動探索暫停與啟用)。新增欄位、取消狀態與 trigger 可重跑，沒有正式資料批次處置或 Supabase Dashboard 設定變更。保留 migration ledger，避免回放舊 publication view 覆蓋暫存過濾。

大量來源刪除逾時修復需更新 API，套用 `20261020132503_cleanup_cascade_indexes.sql`，補來源／文章關聯外鍵索引。沿用既有 DATABASE_URL，連線角色須可切換為 service_role；不改 Dashboard 或公開權限。回滾應用時保留索引與 ledger；已刪資料不會還原。

SQL fixture 與實際 PostgreSQL 測試覆蓋 migration 重跑、service_role 權限、匿名拒絕、預覽／還原、刪除 cascade、重新入庫防線、計數修復與取消對在途候選 INSERT 的鎖競跑。MCP 官方 SDK 驗證機器 key／OAuth scope 分隔與破壞性操作參數；前端測試涵蓋舊回應丟棄、確認取消、部分失敗與重試選取。
