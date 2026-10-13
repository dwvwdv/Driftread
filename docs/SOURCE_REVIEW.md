# 來源收錄評審（#96）

來源評審是 #63 AI roadmap 的第一個業務用途：從主動發現的候選來源抽取有限文章樣本，交給外部 agent 或 Jev 評審，再依後台政策收錄。來源收錄、文章分類與個人推薦是不同的決策；收錄分數不直接取代使用者偏好，也不把小眾來源視為低品質。

## 啟用與人工覆核

在 `/manage/settings` 的來源評審區塊設定 `source.review`，預設 `enabled=false`、`skip_human_review=false`、引擎 `external_agent`。採樣與背景評審仍受部署主開關 `FEED_DISCOVERY_ENABLED` 約束；只有兩個開關皆啟用才會對候選來源採樣。設定每輪重新載入，無需重啟 worker；部署主開關則需依部署方式重啟。

開啟「跳過人工覆核」後，新的有效通過結果直接收錄，不等待管理員。未通過者不收錄；資料不足、模型無法判斷、暫時錯誤或付費請求結果未知，保持暫緩或重試，不以失敗冒充通過。既有 `awaiting_human` 不因切換設定批次放行，需要明確處理。

人工核准、拒絕與保留永遠高於模型；關閉人工覆核不解除已有人工覆寫。背景工作不能重新收錄已被人工拒絕或保留的來源，不能封鎖整個 host，也不能改來源角色、正文政策或全域設定。啟用評審時，原 `FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS` 路徑停止直接入庫；引用數仍可以作為審核證據。

管理員與模型寫入都受候選版本與任務狀態保護。入庫只新增來源 metadata，首次文章抓取仍交 refresh worker；來源 URL 已存在時連結既有列，保留其人工整理的名稱、分類、標籤及權限。

## 評審資料與模型介面

資料包包括來源 metadata、採集政策、近期有限樣本及快照 hash。樣本只來自候選既有的 feed URL，沿用 SSRF、大小上限、robots 與禮貌限制；不建立任意 URL 抓取工具，也不在來源通過前寫入公開文章表。原文中的文字是待審資料，不能修改工具權限或執行政策。

每次評審記錄能力、引擎／模型、政策／提示版本、輸入快照與結構化結果。自然語言理由是可選欄位。來源任務快照有自己的版本，不把多篇文章的來源判斷冒稱為基於單一 article revision。

來源樣本每篇正文 snippet 最多 2,000 字、最多 10 篇，序列化採樣資料上限 90 KB；資料包有效 30 天。評審租約為 10 分鐘，租約重領最多三次；提交必須帶相同租約與快照 hash。服務端通過條件另包含至少一篇 100 字的有效樣本、相關性至少 0.5，以及設定的品質／信心／垃圾機率門檻；不能只相信模型提出的 `accept`。

外部 agent 的實際模型與系統提示不由本後台控制，紀錄使用 `external-agent` 標記未知模型，不能冒稱已驗證 Jev 模型或外部費用。Jev 紀錄另連結 receipt、實際截短後的 provider input hash、問題／政策的 prompt hash、adapter version 與 rubric hash；這些欄位由伺服器產生，外部 MCP 提交不能偽造。

Jev 使用官方固定 API endpoint，問題拆分為品質、主題相關性與垃圾內容機率。模型輸出提供判斷訊號，後台仍檢查樣本可用性、政策門檻、租約、快照及人工覆寫。Jev 不生成文字理由；後台從量化結果與規則產生的說明不宣稱為模型推理。不同引擎的信心與概率須以本地樣本各自驗證，不直接混用門檻。

## 管理 MCP 與權限

個人 `/api/mcp/` 保持原五個唯讀閱讀工具。來源評審使用獨立 `/api/admin/mcp/`，每個 HTTP request 都帶 `X-API-Key: <SOURCE_REVIEW_MCP_KEY>`；普通 `ADMIN_API_KEY`、使用者 Bearer token 與瀏覽器管理 session 不能替代這個專用 key。空白 key 拒絕呼叫；key 不進前端 runtime config。

| 工具 | 用途 |
|---|---|
| `review_policy` | 取得有效政策與背景執行可用狀態 |
| `request_source_review` | 申請候選評審；背景準備樣本，不在 MCP request 內抓取 |
| `claim_source_review` | 原子領取已備妥任務與有限租約 |
| `source_review_packet` | 讀取有界資料包 |
| `submit_source_review` | 提交租約、快照 hash 與結構化結果 |

評審者身分由伺服器指定，不相信模型或客戶端傳入的引擎名稱。專用 key 不能處理人工 resolve、解除人工覆寫或變更設定。Browser REST 走現有管理帳密 + Telegram session；原有腳本管理 key 的 API 契約保留，詳見 [部署手冊](RUNBOOK.md#後台-telegram-兩步驟驗證)。

## Jev 付費請求與離線評測

`JEV_API_KEY` 僅在 backend 環境；未啟用 Jev 不需要設定，也不會發生付費請求。每個邏輯請求的 key 包含能力、模型、輸入及政策／設定版本；先原子預留預算，收到回應先保存再解析。重試可以復用保存的回應。傳輸逾時等無法確定是否已計費的狀態記為 `unknown`，自動額外發送最多一次，之後暫停而非無限付費重試。這種營運暫停與內容人工覆核是獨立概念。

預算僅約束本後台直接發出的付費請求；外部 agent 自行支付的費用不在這個限制內。原始回應、樣本及診斷資料需要有界保存，不能另建永久全文庫；既有文章版本與正文仍沿用 [內容模型](CONTENT_MODEL.md) 與 [保存政策](CRAWLER_OPERATIONS.md)。

部署所有後台付費能力共用滾動預算：每日使用設定的 `daily_budget_usd`，每分鐘上限為其與 USD 0.1 的較小值，每小時為其與 USD 0.5 的較小值。0 阻止付費；未知結果保守保留預留成本。429／5xx 及未知傳輸結果最多再送一次；認證錯誤、無效回應或取消不自動重送。樣本抓取／可恢復 provider 工作錯誤以 5、10 分鐘退避，最多三次；預算不足每五分鐘重新檢查，不呼叫 provider、不消耗工作錯誤次數。

原始付費回應超過 30 天會被本地清理，結果快取為 90 天；保留不含正文的邏輯 identity，避免快取到期後靜默重新付費。API watchdog 在評審停用時仍做有界清理。`GET /api/admin/source-reviews/usage` 提供管理員讀取呼叫數、成本、未知／暫停與近期請求狀態，不返回原始回應。

離線評測區分 development／holdout，按決策統計誤收、漏收、暫緩及 precision／recall，供 agent／Jev 結果比較。隨附示例是合成測試資料，不是已校準的正式 gold set；正式門檻需要以實際來源及人工標註驗證。離線人工標註不代表線上逐筆人工覆核必須開啟。

```bash
python3 scripts/source_review_eval.py --split holdout
python3 scripts/source_review_eval.py --split dev --sweep-confidence 0.8 0.9 0.95
python3 scripts/source_review_eval.py --split holdout --fixtures /path/to/labelled.json --predictions /path/to/predictions.json
```

第三個命令使用同一 ID 集合的實際標註與模型結果；檔案格式參考 `scripts/fixtures/source_review/`。holdout 不允許 threshold sweep，避免在測試集調門檻。官方 Jev 主要針對英文；中文來源要另以代表性資料驗證，合成測試不能證明中文判斷品質。

## Roadmap 邊界

- #84 共用分析／人工覆寫語義：此批實作來源評審，文章／Story publication 的模型分析合成仍另行開發。
- #85 模型能力與版本：此批提供 `source_review`，不讓單一模型承擔摘要、embedding 等所有能力。
- #86 付費請求與預算：此批涵蓋後台 Jev；付費 crawler、通知與 webhook 的業務接入尚未實作。
- #87 評測：此批提供來源收錄的離線工具與示例；分類、事件與個人推薦評測集仍待建立。
- #88–#91 可共用上述介面，這次不啟用 embedding、事件自動分組或生成摘要。

程式／測試完成、PR 合併與正式環境驗收分開追蹤；正式部署沿用 #83。未以正式來源、Jev key 或正式 Supabase 執行的檢查不得宣稱已驗證。
