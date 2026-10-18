# 來源收錄評審（#96）

來源評審是 #63 AI roadmap 的第一個業務用途：從主動發現的候選來源抽取有限文章樣本，交給外部 agent 或 Jev 評審，再依後台政策收錄。來源收錄、文章分類與個人推薦是不同的決策；收錄分數不直接取代使用者偏好，也不把小眾來源視為低品質。

## 啟用與人工覆核

在 `/manage/settings` 的來源評審區塊設定 `source.review`，預設 `enabled=false`、`skip_human_review=false`、引擎 `external_agent`。採樣與背景評審仍受部署主開關 `FEED_DISCOVERY_ENABLED` 約束；還需通過 [自動探索開關](GLOBAL_SETTINGS.md#自動探索暫停與啟用) 才會採樣；暫停時回傳的有效 AI accept 保存為待人工覆核，不自動入庫。設定每輪重新載入，無需重啟 worker；部署主開關則需依部署方式重啟。

開啟「跳過人工覆核」後，新的有效通過結果直接收錄，不等待管理員。未通過者不收錄；資料不足、模型無法判斷、暫時錯誤或付費請求結果未知，保持暫緩或重試，不以失敗冒充通過。既有 `awaiting_human` 不因切換設定批次放行，需要明確處理。

人工核准、拒絕與保留永遠高於模型；關閉人工覆核不解除已有人工覆寫。背景工作不能重新收錄已被人工拒絕或保留的來源，不能封鎖整個 host，也不能改來源角色、正文政策或全域設定。啟用評審時，原 `FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS` 路徑停止直接入庫；引用數仍可以作為審核證據。

管理員與模型寫入都受候選版本與任務狀態保護。入庫只新增來源 metadata，首次文章抓取仍交 refresh worker；來源 URL 已存在時連結既有列，保留其人工整理的名稱、分類、標籤及權限。

候選頁以一次批次請求取得當頁每個候選的最新評審摘要，最多 50 個候選；以 `created_at DESC, id DESC` 決定最新一筆，沒有評審者顯示為尚未申請。不受全域最新評審筆數截斷，也不逐筆建立管理驗證連線。換頁或重載會取消舊批次，人工操作期間的舊回應不能覆蓋新狀態。相同內容及等級的通知在顯示期間只保留一則，關閉或到期後可再次提醒。

候選頁另有「待複審」篩選：候選仍為 pending、沒有人工覆寫，且**最新**評審已有可用 AI 結果（`awaiting_human`／`rejected`／`deferred`）、候選版本一致並尚未有人工作決定；這是評審工作狀態，不增加候選資料表的 status 值。伺服器先篩選，再計算總數與分頁，舊評審不能把新任務帶進待複審列表。覆核交易也驗證最新任務、候選版本與結果，過時的瀏覽器選取回 409。

當頁可全選並批次申請 AI 評審、明確重試，或對 AI 決策批次同意／拒絕。每批 1–50 筆，管理認證只執行一次，各筆沿用既有交易與政策；部分失敗不回滾其他成功結果，回傳每筆 ID、`ok`、結果或安全的 `status_code`／`detail`。前端顯示成功／失敗數，重載後保留仍在當頁的失敗項目選取。明確重試可能重新付費，仍遵守原預算與人工覆寫限制。 重試需攜帶畫面上選到的評審 ID（批次 `expected_review_ids` 以候選 ID 對應評審 ID）；SQL 在政策→候選→任務鎖內確認仍是最新終態後才取代，若 worker 已建立新任務回 409，不中止新任務或增加付費重試。

### 人工決策與 AI 結果

AI 已完成判定的候選只提供「同意決策／拒絕決策」，不再並排通用核准／拒絕。卡片將 MCP（外部 AI）或 Jev、AI 建議、信心／品質／垃圾機率、理由與評審時間作為獨立區塊；已失效結果標記為歷史，不把錯誤或排隊狀態當成判定。

| AI 原始判定 | 同意決策 | 拒絕決策 |
|---|---|---|
| 收錄 | 收錄來源 | 預設改為不收錄，確認後生效 |
| 不收錄 | 拒收來源 | 預設改為收錄，確認後生效 |
| 暫緩／無法判定 | 保留來源 | 明確選擇人工收錄或不收錄 |

「拒絕決策」表示人工不同意 AI，不等於拒收來源。批次同意／拒絕各依每份結果計算，不將混合的 AI 收錄／不收錄建議硬套同一個最終處置。AI 暫緩的不同意需逐筆明確選擇；AI 原始建議收錄但被服務端門檻暫緩時，不能以同意按鈕跳過門檻。

`POST /api/admin/source-reviews/{id}/judgment` 與 `/batch/judgment` 接受 `assessment=agree|disagree`。同意由伺服器推導最終處置，不接受客戶端指定 `decision`；不同意可明確指定與 AI 不同的 `accept|reject|defer`，二元建議未指定時取反，abstain 不能省略。批次最多 50 個 review ID，沿用逐筆交易與部分失敗回應。管理 MCP 憑證無人工決策權限。

原始 `result` 保持不變，新增 `human_review` 記錄同意／不同意、當時 AI 判定、最終處置與時間；一般管理摘要與最新評審查詢會帶出，前端能區分人工同意 AI 與人工不同意 AI。舊 `/resolve` API 保留並推導同一份人工紀錄。交易驗證最新任務、候選版本、有效完成狀態與人工覆寫；已有人工作決定或已收錄者不可重複處置。歷史評審不自動回填人工同意／不同意，避免推測舊操作意圖。

### 近期收錄與收錄方式

候選頁的「近期收錄」獨立列出最新 10 個目錄來源，顯示標題、feed URL、首次進入目錄的時間與收錄方式，可收合及重新整理；成功收錄後更新，不受候選篩選或當頁筆數影響。`GET /api/admin/discovery/recent-admissions?limit=10` 需管理驗證，`limit` 為 1–50，回傳 `{items, limit}`。時間依 `feeds.created_at` 排序，與首次抓取品質檢查用的 `feeds.admitted_at` 不同。

| method | 顯示與意義 |
|---|---|
| `ai_auto` | AI 自動收錄：有效結果依政策直接入庫 |
| `human_agree` | 人工同意 AI 後收錄 |
| `human_disagree` | 人工推翻 AI 後收錄 |
| `manual` | 人工直接收錄：從候選手動核准 |
| `referrer_auto` | 排程自動收錄：引用證據門檻路徑 |
| `unknown` | 歷史／其他管道（未記錄）：沒有可靠的收錄方式紀錄 |

`feed_admissions` 在候選真正新增 feed 的同一交易記錄方式、引擎與候選／評審 ID；已有來源的重連、重複核准或重新發現不產生新收錄，也不覆寫首次方式。紀錄不含文章、樣本或憑證，評審歷史清理不會刪除其方式標記。清單涵蓋所有目錄來源；既有來源及未經此候選入庫路徑的 JSON／URL／OPML 等匯入以 `unknown` 呈現，不從目前設定或最新評審猜測，也不回填歷史意圖。

尚無 AI 結果的候選保留明確的手動收錄／保留／拒收；評審進行中或結果已失效時不提供重複的直接處置按鈕。已保留來源的後續人工收錄仍可操作，顯示為調整人工決定。

來源發現會在抽取 alternate、直接 feed URL 與每次 redirect 時排除 `comments/feed/` 等明確留言端點，不抓取留言內容，也不占候選額度。候選寫入與申請評審的既有防線保留。URL 規則不以標題的 comments 字樣猜測，也不批次刪除歷史候選。

模型建議的分類與標籤只沿用既有詞彙：分類必須已被某個來源或探測方向使用，標籤必須已被某個來源使用，否則收錄時捨棄（分類留空、標籤略過），不讓模型自創的名稱進入公開目錄與推薦分類。自動收錄與人工採納／推翻 AI 後的收錄都套用這條規則；尚無 AI 結果時可在手動收錄設定指定新分類，已收錄者到來源管理修改。

候選保留／拒絕與模型收錄使用同一候選鎖。人工操作先完成時，模型不能再收錄；模型已先收錄時，候選操作回傳 409，後台重讀並提示前往來源管理。已收錄來源須在來源管理封存或刪除，候選操作不回報假的拒絕成功，也不撤銷既有來源。

## 恢復與佇列診斷（#106）

普通申請回傳 `request_action=enqueued/requeued/reused`。新候選為 enqueued；stale 或仍未完成但政策版本落後的任務以目前政策新建任務，舊任務與決策紀錄保留；同一候選的並行申請只建立一筆。既有有效進行中任務、人工覆寫、`awaiting_human` 與已完成判斷不自動重做。排程原有的每輪最多十筆 stale 恢復仍運作，MCP 可直接對已知候選重新申請，不必等待排程掃到它。

舊 Jev 任務若因 `provider_key_missing` 失敗，而目前引擎已改為 `external_agent`，普通申請可轉交外部引擎，重新準備樣本並保留舊失敗。相同引擎的設定錯誤、抓取錯誤與付費結果未知仍回 reused；不能把這些狀態當成來源不合格。候選頁提供「依目前政策重新申請」，沿用相同安全恢復規則；需要重新購買時仍使用原本受選定任務 ID 保護的明確重試。

普通恢復沒有 `paid_retry_key`，也不直接呼叫 provider；不解除未知請求的重送上限。政策版本仍是既有 receipt identity 的一部分，不同政策的 Jev 分析可能建立新的付費邏輯請求。現在的外部引擎不透過 backend 呼叫 Jev。

`review_policy.queue` 另回各 pending、無人工覆寫候選**最新任務**的狀態總數、`outdated_active` 與 `provider_key_missing`。它是可觀測的佇列積壓／設定告警訊號，不是歷史任務總數，也沒有新增通知外送。快照過期與政策升版都可產生 stale；不從舊資料的空白錯誤碼猜測唯一根因。

採樣時只有標題為 Hello world 且全文完整符合已知 WordPress 預設起始文章，才標記 `default_demo`。SQL 的有效樣本門檻排除這種樣本；只有預設內容時，模型 accept 仍暫緩並記 `default_demo_content`，不封鎖 host，管理員可照原流程覆寫。相同標題的真實文章、含其他原創文字的文章不套此規則。只有標題或過短摘要仍沿用至少一篇 100 字有效樣本門檻，不一概當垃圾來源。

## 評審資料與模型介面

資料包包括來源 metadata、採集政策、近期有限樣本及快照 hash。樣本只來自候選既有的 feed URL，沿用 SSRF、大小上限、robots 與禮貌限制；不建立任意 URL 抓取工具，也不在來源通過前寫入公開文章表。原文中的文字是待審資料，不能修改工具權限或執行政策。

每次評審記錄能力、引擎／模型、政策／提示版本、輸入快照與結構化結果。自然語言理由是可選欄位。來源任務快照有自己的版本，不把多篇文章的來源判斷冒稱為基於單一 article revision。

來源樣本每篇正文 snippet 最多 2,000 字、最多 10 篇，序列化採樣資料上限 90 KB；資料包有效 30 天。評審租約為 10 分鐘，租約重領最多三次；提交必須帶相同租約與快照 hash。服務端通過條件另包含至少一篇 100 字的有效樣本、相關性至少 0.5，以及設定的品質／信心／垃圾機率門檻；不能只相信模型提出的 `accept`。

外部 agent 的實際模型與系統提示不由本後台控制，紀錄使用 `external-agent` 標記未知模型，不能冒稱已驗證 Jev 模型或外部費用。Jev 紀錄另連結 receipt、實際截短後的 provider input hash、問題／政策的 prompt hash、adapter version 與 rubric hash；這些欄位由伺服器產生，外部 MCP 提交不能偽造。

Jev 使用官方固定 API endpoint，問題拆分為品質、主題相關性與垃圾內容機率。模型輸出提供判斷訊號，後台仍檢查樣本可用性、政策門檻、租約、快照及人工覆寫。Jev 不生成文字理由；後台從量化結果與規則產生的說明不宣稱為模型推理。不同引擎的信心與概率須以本地樣本各自驗證，不直接混用門檻。

## 管理 MCP 與權限

來源評審以外另有六個清理／待探索與探索控制工具，要求獨立 `catalog:manage` 或新的專用機器 key；既有評審憑證不擴權。工具與部署見 [後台清理](ADMIN_CLEANUP.md#管理-mcp-與權限)。下文九個評審工具仍要求 `source:review`。

個人 `/api/mcp/` 保持原五個唯讀閱讀工具。來源評審使用獨立 `/api/admin/mcp/`，支援專用 `X-API-Key: <SOURCE_REVIEW_MCP_KEY>` 或由本服務發出的窄權限 OAuth `Authorization: Bearer <access_token>`；每次請求只能選一種。普通 `ADMIN_API_KEY`、Supabase JWT 與瀏覽器管理 session 都不能替代它們。空白 key 停用機器認證，空白 `SOURCE_REVIEW_OAUTH_BASE_URL` 停用 OAuth；兩個開關互相獨立，key 不進前端 runtime config。

| 工具 | 用途 |
|---|---|
| `review_policy` | 取得有效政策與背景執行可用狀態 |
| `list_source_review_candidates` | 唯讀候選及最新評審摘要，支援 pending／ready／awaiting_human，最多 50 筆，含精確總數；不領租約、不回傳樣本或 token |
| `request_source_review` | 申請候選評審；背景準備樣本，不在 MCP request 內抓取 |
| `request_source_reviews` | 批次申請 1–50 個候選，去重並逐筆回報；不開放強制付費重試 |
| `claim_source_review` | 原子領取已備妥任務與有限租約 |
| `claim_source_reviews` | 批次領取預設 3、最多 5 個任務；各有自己的 10 分鐘租約 |
| `source_review_packet` | 讀取有界資料包 |
| `submit_source_review` | 提交租約、快照 hash 與結構化結果 |
| `submit_source_reviews` | 批次提交 1–50 筆獨立判斷，逐筆回報，保留相同結果冪等性 |

建議先讀政策與唯讀 ready 清單，再批次領取、依各份證據獨立評審、批次提交。沒有樣本的 pending 候選先申請，再等待背景準備。領取不是冪等操作：批次數量不足可能是佇列暫空；中途遇錯會保留已取得的 packet 並帶 `error`，先處理已回傳租約。領取後不再額外查候選，避免第二次資料庫連線失敗而遺失 token。每包仍受 90 KB 採樣上限約束，工具描述公開期限及服務端額外門檻。

清單 offset 會隨評審完成而移動；每批處理後從 offset=0 重讀，避免跳過候選。ready 清單也包含租約過期、仍可重領的任務。一般業務錯誤回 MCP `isError=true`，結構化碼包含 `NOT_FOUND`、`FORBIDDEN`、`REVIEW_CONFLICT`、`UNAVAILABLE`，不必解析自然語言；格式錯誤仍由 SDK 驗證。批次以逐筆 `ok`／狀態碼區分業務結果，重試失敗項目即可。

評審者身分由伺服器指定，不相信模型或客戶端傳入的引擎名稱。專用 key 不能處理人工 resolve、解除人工覆寫或變更設定。Browser REST 走現有管理帳密 + Telegram session；原有腳本管理 key 的 API 契約保留，詳見 [部署手冊](RUNBOOK.md#後台-telegram-兩步驟驗證)。

### ChatGPT Web OAuth（#100）

ChatGPT Web 的「無驗證」不能附上自訂 `X-API-Key`。部署本版後，在 `.env` 填 `SOURCE_REVIEW_OAUTH_BASE_URL=https://你的網域`（正式 HTTPS origin，不含路徑或 query），重新建立 API 容器讓環境變數載入；同版 frontend 的 nginx 必須把 `/.well-known/` 轉給 API。部署順序與 migration 見 [RUNBOOK](RUNBOOK.md#chatgpt-web-來源評審-oauth100)。

在 ChatGPT 的自訂 MCP 新增頁填 `https://你的網域/api/admin/mcp/`，選 **OAuth**。Client ID／Client Secret 留白，由 ChatGPT 經 DCR 註冊 public client；若介面要求選註冊模式，選 DCR。第一次連線跳到 Driftread 的 `/manage/mcp-authorize?request=...`：先登入唯一管理員帳密、輸入 Telegram OTP，再明確允許或拒絕來源評審權限。登入回跳保留原 request。一般帳號不能同意，既有管理 key 不能簽發 OAuth grant；客戶端可提供的名稱不是已驗證品牌。

授權使用 S256 PKCE，一次性 code 綁定 client、已註冊 callback 與 MCP resource。`source:review` 只授權表中的九個工具；另以 `offline_access` 支援持續連線。請求 10 分鐘、code 2 分鐘、access token 15 分鐘、新 grant 預設 30 天（管理員可調整或設為永久）；refresh 每次旋轉，已使用 refresh 重放會撤銷整個 grant。伺服器只保存授權碼與 bearer 憑證的 SHA-256 hash，原文只回傳給 client。每次 MCP／refresh 都檢查目前管理員 UUID、Supabase session、帳號停用、resource 與到期／撤銷；不提供 client_credentials 或接受其他 JWT。

`/manage/mcp-connections` 可查看授權、修改有效期限或設為「永久有效」，並隨時撤銷。每條連線獨立設定；自訂日期使用瀏覽器本地時區輸入，轉成含時區的時間送出。新授權仍預設 30 天，既有期限不自動延長。後台明確登出會依已驗證的 owner／Supabase 登入 session 撤銷該登入的管理 sessions 與 OAuth grants，即使管理 token 已過期、清除或未傳入也生效；不影響其他登入。管理 OTP session 自然到期不等於 OAuth grant 到期，Supabase session 失效仍會拒絕工具呼叫。前端以 user ID／auth session ID 與管理 token 隔離在途回應，正常 JWT 自動刷新仍可完成同意回跳；舊 JWT 的延遲 401 不清除新憑證。OAuth 同意不是逐次工具批准，ChatGPT 的工具執行權限另由客戶端管理。MCP 連線也不會自行啟動常駐 agent。

修改期限使用 `PATCH /api/review-oauth/grants/{id}`，只接受管理員帳密＋Telegram session；body 必須明確帶 `expires_at`（含時區的未來 ISO 日期，或 `null` 表示永久）。只能修改本人仍有效且未撤銷的 grant；過期／撤銷後需重新授權，不藉編輯復活。交易沿用 grant row lock，同時調整尚未使用的 refresh token 期限，既有連線不需換 token 即可延長；縮短後每次 MCP／refresh 都依目前 grant 拒絕到期權限。access token 仍最多 15 分鐘，永久不改變其短效性。

永久僅表示沒有固定到期日，撤銷、同登入的後台登出、owner／原 Supabase session 失效與 refresh replay 仍會中止授權。有效永久 grant 不被清理，已撤銷者 30 天後可清除；其 refresh replay digest 與其他有效 grant 一樣保留到 grant 清理。回應只含連線摘要，不提供任何 bearer 原文。

公開 discovery：`/.well-known/oauth-authorization-server`、`/.well-known/oauth-protected-resource/api/admin/mcp`（另提供 root 與尾斜線 alias）。HTTP 401 的 `WWW-Authenticate` 指向 resource metadata，所有工具的 `securitySchemes` 同時宣告 `source:review`／`offline_access`。授權端點為 `/api/review-oauth/{register,authorize,token,revoke}`；token／revoke 只收有界 form，不使用 query token。DCR callback 只允許官方 `https://chatgpt.com/connector_platform_oauth_redirect` 或 `https://chatgpt.com/connector/oauth/{callback_id}`，須逐字與註冊值相同，不抓取客戶端 URL。

授權 query 依 [RFC 6749 §3.1](https://www.rfc-editor.org/rfc/rfc6749.html#section-3.1) 忽略不識別的附加參數，例如 `audience`、`access_type` 或 `id_token_hint`；這些提示不改變權限、不當成登入證明，也不傳到 callback。已知的 client／callback／resource／scope／S256 PKCE 仍須通過原驗證。原始 query 上限 8,192 bytes、最多 20 個欄位，任何重複參數均拒絕；其他端點的參數規則不變。

本實作是既有單管理員應用的窄用途授權服務；OAuth 表 RLS 零 policy、撤銷所有 Data API roles（含 service_role），沿用後台 privileged `DATABASE_URL`，不改共用 Supabase 的 OAuth provider／註冊設定。匿名 DCR 回傳包含公開 metadata 的 HMAC 簽名 client ID，不新增 client 記錄，也不佔用持久 client 配額；只有管理員明確同意才保存 client，之後不因閒置或 grant 到期刪除，讓 ChatGPT 可重用同一 client ID 重新授權。簽名不加密 metadata；私有 `review_oauth_registration_key` 保存唯一持久簽名金鑰，必須隨資料庫備份保留，不可在重啟／清理時重建。

待授權 requests 最多 1,000、10 分鐘到期，公開端點有每 IP 限流。授權請求過期有界清理，grant 到期或撤銷 30 天後清除其 token／code；refresh replay 的 digest 保留到 grant 清理。實際 ChatGPT Web／正式 Telegram／Supabase 與代理部署必須另驗收。

管理 MCP 的 OAuth bearer 驗證在進資料庫前，按來源 IP 每 60 秒最多 60 次，另限每 process 同時 4 次驗證，使用獨立執行緒池保留其他後台工作的共用容量。超出任一上限回 429 與 `Retry-After`；不排隊，請依回應稍後重試。取消 HTTP 請求不會提前釋放仍在執行的資料庫驗證名額；專用機器 key 不使用此驗證池。

官方契約：[OpenAI authentication](https://developers.openai.com/plugins/build/auth)、[ChatGPT MCP setup](https://developers.openai.com/api/docs/guides/custom-mcp-server)。

## Jev 付費請求與離線評測

`JEV_API_KEY` 僅在 backend 環境；未啟用 Jev 不需要設定，也不會發生付費請求。每個邏輯請求的 key 包含能力、模型、輸入及政策／設定版本；先原子預留預算，收到回應先保存再解析。重試可以復用保存的回應。傳輸逾時等無法確定是否已計費的狀態記為 `unknown`，自動額外發送最多一次，之後暫停而非無限付費重試。這種營運暫停與內容人工覆核是獨立概念。

預算僅約束本後台直接發出的付費請求；外部 agent 自行支付的費用不在這個限制內。原始回應、樣本及診斷資料需要有界保存，不能另建永久全文庫；既有文章版本與正文仍沿用 [內容模型](CONTENT_MODEL.md) 與 [保存政策](CRAWLER_OPERATIONS.md)。

部署所有後台付費能力共用滾動預算：每日使用設定的 `daily_budget_usd`，每分鐘上限為其與 USD 0.1 的較小值，每小時為其與 USD 0.5 的較小值。0 阻止付費；未知結果保守保留預留成本。429／5xx 及未知傳輸結果最多再送一次；認證錯誤、無效回應或取消不自動重送。樣本抓取／可恢復 provider 工作錯誤以 5、10 分鐘退避，最多三次；預算不足每五分鐘重新檢查，不呼叫 provider、不消耗工作錯誤次數。

原始付費回應超過 30 天會被本地清理，結果快取為 90 天；保留不含正文的邏輯 identity，避免快取到期後背景工作靜默重新付費。快取到期的結果只有在管理員明確「重試 AI 評審」時才重新購買。API watchdog 在評審停用時仍做有界清理。`GET /api/admin/source-reviews/usage` 提供管理員讀取呼叫數、成本、未知／暫停與近期請求狀態，不返回原始回應。

修正 Jev key 或無效回應問題後，可在候選頁明確「重試 AI 評審」。只有明確重試會取得 paid retry key；排程對新候選或 stale 任務的重新排入不帶 key，只復用既有 receipt。只有已知的終態失敗或快取已清除的完成結果會建立新付費請求 generation；同一重試任務持久綁定 receipt，背景重跑不會再建立 generation。舊費用與回應紀錄保留，重新發送仍受預算限制；已完成回應直接復用，unknown 不因手動重試解除最多兩次發送的上限。

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

## MCP 獨立評估（2026-10-16）

獨立 agent 實際讀取已連接服務的 `review_policy`，並以不存在、格式錯誤及空 ID 測試 packet；沒有領取正式任務、提交決策或觸發付費請求。確認舊版缺少唯讀清單與批次工具、業務錯誤無穩定碼。程式審查另找出領取後額外查詢可能遺失 token、ready 清單需要對齊政策與過期租約重領，以及人工覆核需要最新任務檢查，本輪一併修復並納入回歸測試。

後續保留一項 P3：提供有界的既有分類／標籤詞彙表。現階段不知道有效詞彙時省略建議，服務端繼續捨棄未知詞彙。本輪 SDK／隔離 PostgreSQL 驗證不能代替更新正式 API 後的 ChatGPT 工具刷新、正式 PostgREST、Telegram 登入與 UI 驗收。
