# 部署與回滾 Runbook

給要實際操作 `docker-compose.yml` 這份部署的人看的操作手冊，記錄「照什麼順序做」。
背景與技術細節（RLS 決策、schema 設計理由）在 `docs/FEATURES.md` 第 5 節與
`docs/SECURITY.md`；這份文件只管操作順序。

## 前提

- 三個服務（`api`、`worker`、`frontend`）都是同一組 image 建構出來的兩個 repo
  （`driftread-api`、`driftread-frontend`），由 `.github/workflows/{backend,frontend}.yml`
  推到 GHCR。
- **每個 image 都同時打上 `:latest` 與 `:sha-<commit sha>` 兩個 tag**（見下方「回滾」）。
- `api` 在 `main.py` 的 lifespan 裡開機時自動跑 `migrate.py::run_migrations()` 與
  `backfill.py::run_backfills()`，兩者都用同一把 PostgreSQL advisory lock
  （`migrate.py::acquire_migration_lock`）序列化，並各自把「跑過了嗎」記在
  `driftread._migrations`——多個 `api` replica 同時開機、或 `worker`／`api` 前後開機，
  不會重複套用或互相競跑。migration／backfill 因此**不需要**手動照順序一個個跑，
  `docker compose up -d` 開 `api` 就會自動處理，`worker` 的 `depends_on: api: condition:
  service_healthy` 保證它一定晚於 migration 完成才啟動。
- `frontend` 的 Supabase 設定（`SUPABASE_URL` / `SUPABASE_ANON_KEY`）是容器啟動時由
  `frontend/docker-entrypoint.d` 寫進 `env.js`，不是 build time 編進 JS bundle——換帳號、
  換 project 只需要改 `.env` 再 `docker compose up -d frontend`，不必重新 build image。

## 一般部署（沒有動 DB schema／RLS／Supabase Dashboard 設定）

1. `git pull`（或改 `docker-compose.yml` 指到想要的 tag，見下方「回滾」）。
2. `docker compose pull`
3. `docker compose up -d`
   - `api` 開機時自動跑新 migration（若有）與健康檢查；`worker`／`frontend` 都
     `depends_on: api: condition: service_healthy`，會自動等 `api` 準備好才起。
4. `docker compose logs -f api` 確認 `Applying migration: ...` / `Applied: ...`
   （若這次沒有新 migration 則不會出現）且沒有例外往外拋、健康檢查轉綠。

日常沒有 schema 變動時，這四步就是全部流程。

## 會動到 Supabase Dashboard 設定的部署（schema exposure／grant／RLS）

`backend/migrations/*.sql` 涵蓋得到的部分（建表、RLS policy、function 權限）都會在
`api` 開機時自動套用。**Supabase Dashboard 的 Data API「Exposed Schemas」清單不是**——
沒有程式碼路徑能改這項設定，純粹是 Dashboard 手動步驟（Settings → API → Exposed schemas）。

目前的狀態：`driftread` schema 的 RLS policy／grant 已經由 migration 010 / 011 就緒
（見 `docs/FEATURES.md` 第 5 節），但「Exposed Schemas 是否已加入 `driftread`」是
`TODO.md` Phase 0 仍列為待確認的一項——這件事沒辦法從程式碼驗證，只能到 Dashboard 親自看。
**在把這份 compose 部署指到一個新的 Supabase project 之前，先確認這項設定**，否則
`backend/database.py::get_client()` 的 scoped client（固定 `schema="driftread"`）對
PostgREST 的每一次查詢都會是空的 exposed-schema 錯誤，而不是慢慢才發現的資料缺失。

正確順序（新增一個會擴大 exposed schema 或改變 grant 的 migration 時）：

1. **先**確認／調整 Supabase Dashboard 的 Exposed Schemas 與必要的 `anon`／`authenticated`
   grant——這步永遠手動、永遠先做，因為新 migration 套用後如果 API 還沒被允許看到
   新的 schema／表，任何依賴它的請求都會直接壞掉，而不是優雅降級。
2. **再** deploy 帶著新 migration 的 `api` image（走上面「一般部署」四步）。
3. 部署後跑一次 Supabase Dashboard 的 Advisors 頁（Database → Advisors）與一次
   「新舊 backend 部署順序」的手動走查——`TODO.md` Phase 0 最後一項——確認沒有非預期的
   公開存取或缺 grant。

**相容性 view 的收尾**：migration 010 把 `_migrations` 從 `public` 搬進 `driftread` schema
時，保留了一個 `public._migrations` 的 `security_invoker` view 當作相容橋樑，避免舊版
（改用 `driftread` schema 之前的）backend 重啟時把它當空 table 誤建一份新的 ledger。
**這個 view 目前仍然保留著**（`TODO.md` Phase 0 明確記著這項尚未執行）。要移除它，前提是
「所有仍在跑的 backend 版本都已經是讀 `driftread._migrations` 的版本」——確認方式是看
`docker compose ps` / 部署紀錄，確定沒有任何舊版 image 還在跑，才手動下
`DROP VIEW public._migrations;`。順序反了（view 移除在舊版本淘汰之前）會讓還沒升級完的
舊版 backend 在下次重啟時把 ledger 誤判成空的，重新跑一次它以為沒跑過的舊 migration。

## 每日容量歷史

#64 新增 `20261013115249_article_storage_history.sql`，由 API 啟動時的 runner 套用；沒有新 exposed schema、Dashboard 設定或環境變數。先備份並停舊 worker，更新 API 並確認 ledger／healthy，再啟動同版 worker／frontend。

確認管理首頁「容量成長趨勢」收到第一筆每日紀錄、`storage` 工作與 heartbeat 正常；任一既有 scheduler 啟用才會啟動容量採樣，三者全關仍退出。`GET /api/admin/retention/history?days=30` 必須由管理 session 或既有機器 API Key 授權，不登入不能讀。觀察至少 24 小時、兩筆不同日採樣後再驗證成長率；同日重啟不得覆寫採樣，不應手動清 ledger 或補造歷史。部署驗收仍由 #83 追蹤，正文縮減繼續維持 `ARTICLE_RETENTION_ENABLED=false`，直到備份／dry-run 驗證完成。

## 收錄前來源身分檢查

`20261015040607_source_mirror_gate.sql` 新增 `feeds.admitted_at`（已抓取過的既有來源回填為已收錄）與 service-only RPC `find_mirror_sources`，由 API 啟動時的 runner 套用；沒有 Dashboard 設定或環境變數。照一般順序先更新 API、確認 ledger 後再更新 worker。worker 先於 migration 啟動時，讀到的來源沒有 `admitted_at` 欄位，排程維持原本行為、不做檢查，也不會寫入不存在的欄位。行為見 [SOURCE_MODEL.md](SOURCE_MODEL.md#收錄前的來源身分檢查)。

## 資料庫接近 Supabase 容量上限

Free plan 的資料庫上限是 500 MB，超過會被切成唯讀。`DELETE` 和正文縮減都只把列標成可重用，**資料庫大小不會變小**；要把空間還回去，得對該表跑 `VACUUM (FULL, ANALYZE)`，執行期間整張表被鎖住（2026-10-13 對 472 MB 的 `articles` 約一分多鐘），API 讀寫該表會等待。

1. 用 `pg_total_relation_size` 找出最大的表，並逐來源加總 `pg_column_size(articles.*)`；先處理重複或非文章來源（別名 feed、留言 feed、套件發版通知等）。
2. 刪除前確認該來源沒有訂閱、已讀與收藏。保留 feed 列並設 `archived_at`，只刪文章：feed 列是之後同 URL 再匯入的墓碑；刪除 feed 會讓 `discovery_candidates.feed_id` 變成 NULL。文章刪除會 cascade 清掉 revisions、discoveries、已讀與收藏。
3. 在離峰時段依序對 `articles`、`article_revisions`、`article_discoveries` 跑 `VACUUM (FULL, ANALYZE)`。透過 Supabase MCP 執行時，客戶端 60 秒逾時不會取消伺服器上的 VACUUM，以 `pg_stat_activity`／`pg_stat_progress_cluster` 確認完成。
4. 已進入唯讀模式時，Supabase 會設定 `default_transaction_read_only`；清理用的連線先執行 `SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE`。

2026-10-13 的紀錄：資料庫 551 MB，刪除 10 個重複／留言／套件來源的 18,435 篇文章後跑 VACUUM FULL，降到 195 MB。實際刪除的資料約 60 MB，其餘約 290 MB 是更新造成的 bloat，所以空間仍會慢慢長回去；用管理首頁的容量歷史觀察，接近 400 MB 就重複步驟 3。

## issue #63 非 AI 功能升級

本批新增佇列、來源／正文政策、文章版本、人工事件、個人熱度與消費出口；不新增環境變數或 exposed schema。完整模型分別見 [BACKGROUND_JOBS.md](BACKGROUND_JOBS.md)、[PUBLICATION.md](PUBLICATION.md) 與 [CONSUMPTION_SURFACES.md](CONSUMPTION_SURFACES.md)。

本批最後一支 `20261009000700_incremental_sync.sql` 在所有 DDL／grants 之後送出 `NOTIFY pgrst, 'reload schema'`；通知在 migration 交易成功提交後才送達，讓 PostgREST 更新 publication／事件／熱度／sync 物件快取，不依賴額外的 DDL event trigger。部署驗收仍須實際呼叫新增 RPC／API，不能只以 `/api/health` 判定 Data API 已完成非同步 reload。

#82 的 `20261012000000_article_revision_retention.sql` 排在這批之後、同一次部署一起套用：它為 `article_revisions` 加上「每篇最多 10 版」的清理 trigger，結尾同樣送出 schema reload。正式環境若在套用 20261009 系列之後才套用它，migration 會一次刪掉既有的超量舊版本（只動版本歷史，不碰文章與使用者資料）。部署後 24 小時觀察 `article_revisions` 的列數，每篇應不超過 10 列。

1. 備份資料庫並保留 `driftread._migrations` ledger，記下目前三個 image SHA。
2. `docker compose stop worker`，等待正常停機；不要混跑舊記憶體排程 worker 與新佇列 worker。
3. 取得同一版本 api／worker 與 frontend image，先更新 `api`，等 migration／backfill 完成與 `/api/health` healthy。
4. 更新 `worker`、`frontend`；worker 的停止寬限為 30 秒，API 的 MCP session manager 與 watchdog 由同一 lifespan 啟停。
5. 在管理頁確認佇列、heartbeat、來源角色與健康。以兩個永久帳號走查訂閱、已讀、收藏、熱度、日報、RSS、MCP 與擴充 account switch；切換 `summary_only`／`private` 後確認搜尋與所有出口立即遵守政策。原始 articles 的匿名 Data API SELECT 現在應拒絕，前端經 backend 閱讀。
6. Dashboard Advisors 與正式環境的 publication／新增表 grants 仍需部署者實際確認。

本批 migration 已在隔離 PostgreSQL 17 驗證 baseline 升級及新增 migration 重跑。**不要清空歷史 ledger 作為升級方式**：完整清空後重播所有舊檔，會先遇到既有 006→010 public trigger dependency 問題；本批沒有修改已合併 migration，也不把新增檔可重跑宣稱為完整歷史 reset 已通過。正式 Supabase、Docker 與實際瀏覽器走查尚未驗證。

回滾應先停 worker，再依下節切回同版 image；新增資料不刪除。raw articles grants／正文與來源限制屬權限收緊，不應為恢復舊匿名 client 任意放寬。舊 RPC 保持原 return contract 並轉接 publication，回滾後仍需確認舊讀取端相容；離線擴充清除 cache 後才換回舊版。

## 後台 Telegram 兩步驟驗證

1. 在 Supabase `auth.users` 確認指定管理員為既有永久帳號且已有密碼，把其 UUID 填入部署 `.env` 的 `MANAGE_OWNER_USER_ID`。不以 Email／user_metadata 動態授權；空白時後台拒絕登入。
2. 確認 Vault 中既有 `telegram_bot_token` / `telegram_chat_id` 可由 `DATABASE_URL` 角色讀取，`telegram_chat_id` 為本人私人 chat（正整數）；確認 `pg_net` 已啟用、可對 Telegram 外連，`pgcrypto` 安裝於 `extensions`。本專案沿用 seamless_track 設定，不需要複製 Bot Token 到環境變數，不變更 reference schema。
3. 先部署 API，讓 `20261012020000_manage_telegram_auth.sql` 自動建表、撤銷 Data API 存取。再部署同版 frontend；worker 不使用管理員 UUID。`DATABASE_URL` 現在同時用於每次後台驗證，不能僅在 migration 時提供。
4. 瀏覽 `/manage` → `/manage/login`，輸入管理員帳密後確認本人 Telegram 收到 OTP，再輸入完成；確認一般帳號不能發送 OTP、錯碼／過期碼不能进入後台、重送舊碼失效、登出後台後舊管理 token 不可用。
5. 確認 `/login` 沒有註冊入口、舊 `/admin` 前端入口不再提供後台。共用 Supabase Auth 全域註冊未關閉，其他應用不受影響。
6. `/api/admin/*` 仍供既有腳本／擴充以 `X-API-Key` 存取，這是獨立機器授權，不宣稱它也經 Telegram OTP。需要撤銷舊瀏覽器曾使用的 key 時，在 `.env` 旋轉 `ADMIN_API_KEY` 並同步更新整合設定。

本機 PostgreSQL 測試用假的 Vault／pg_net 送信端；正式 Telegram 收件、實際瀏覽器與 Docker image 部署須在上述步驟驗收。
回滾 frontend／API 至舊版將恢復舊 API Key 後台入口；新增表可保留，管理 session 到期不會授權舊後台。

## 來源收錄評審升級（#96）

此批不變更 Supabase Dashboard 的 exposed schema；新增 service-only 表、RPC 與候選版本欄位。部署仍由 #83 追蹤，先備份並停舊 worker，再升級 API 套 migration，healthy 後啟動同版 worker／frontend。

1. 套用 `20261013040000_source_review.sql`、`20261013040010_source_review_queue.sql`、`20261013040020_ai_receipts.sql`，保留原 migration ledger；最後 DDL 後 reload PostgREST schema。
2. API 設定獨立的 `SOURCE_REVIEW_MCP_KEY`（不能與 `ADMIN_API_KEY` 相同）；Jev 引擎需要 `JEV_API_KEY`，compose 只傳給 backend api／worker。前端不接收這兩個 key。
3. 預設 `source.review.enabled=false`，升級不會自動付費或新啟動爬取。來源採樣另受 `FEED_DISCOVERY_ENABLED` 約束；確認主開關與 robots 政策後才從 `/manage/settings` 啟用評審。
4. 先在人工覆核模式驗證候選樣本、租約、結果與人工覆寫，再依實際評測結果開啟「跳過人工覆核」。既有待覆核任務仍需明確處理。
5. 用標準 MCP client 的專用 key 測試 request／claim／packet／submit；確認普通管理 key、個人 token 無法使用管理 MCP，原個人唯讀 MCP 仍正常。
6. 驗證 Jev receipt／用量、未知請求暫停與預算，確認 disabled 時不再發付費請求；正式模型回應、中文品質、代理連線及成本需實際部署另驗收。

回滾前先關閉 `source.review` 並停 worker；舊應用忽略新欄位，但不理解評審政策，可能重新使用原 referrer 自動入庫。因此回滾時保持 `FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS=0` 或停用 discovery，不能把舊版視為仍有模型把關。新增資料保留，不移除人工覆寫與分析歷史。

## 來源管道診斷與恢復（#106）

先停止舊 worker，再由 API 開機套用 `20261016122206_source_diagnostics_recovery.sql`，healthy 後啟動同版 worker／frontend，最後刷新 ChatGPT MCP 工具說明。新欄位是 API／worker 必要依賴，不能先啟動新版 worker；不需要修改 Dashboard、環境變數或金鑰。migration 不批次重排、重抓或修復正式資料。

驗收先讀 `review_policy.queue` 及一般候選清單，以少量 stale／舊 Jev 缺金鑰候選重新申請，確認 `request_action`、目前政策與引擎、舊紀錄保留及未建立 paid retry key；相同有效任務回 reused。只對原失敗做無付款的外部引擎轉交，不能用此步驟測試正式付費重送。後台逐一重抓歷史原因不明來源並比對診斷／精確文章數／公開列表、詳情與瀏覽器，參見 [調查紀錄](ISSUE_106.md)。

回滾應用時保留新增欄位與 RPC。舊 worker 不寫 `last_fetch_result`，因此需將診斷視為歷史結果；新 SQL 的恢復與預設示範樣本門檻仍生效。不刪除評審或 receipt 歷史，也不解除既有封存／人工覆寫。

## 來源評審批次與待複審

先更新 API，開機套用 `20261016033310_review_candidate_listing.sql`（最新評審索引、唯讀列表 RPC、人工覆核版本檢查），healthy 後更新同版 frontend／worker。沒有新增環境變數、資料表或 Dashboard 設定；既有候選／評審歷史保留。舊版 API 的 resolve 呼叫仍可用，但只能處理最新待人工覆核的結果。

ChatGPT 刷新 MCP 工具列表後應有九個工具。先呼叫唯讀 `list_source_review_candidates`，確認不建立租約，再以少量任務驗收批次領取／提交、部分失敗與已過期租約重領。候選頁確認「待複審」跨頁總數、全選與批次覆核。明確重試仍可能產生費用，不以正式任務任意做失敗測試。

回滾應用時保留 migration；新增 RPC／索引不影響舊列表，較嚴格的覆核檢查繼續生效。回滾到沒有新工具的 API 後，重新刷新 ChatGPT 工具列表。

管理連線期限另需 `20261016050000_review_oauth_expiry.sql`：API 先套 nullable grant／refresh 期限 migration，再升級 frontend，既有授權保持原期限；MCP 連線頁可改未來日期或永久。有效永久授權仍可立即撤銷，access token 仍短效。回滾到不理解 null 期限的舊 API 前，先用新版後台將有效永久授權改回有限日期。若資料庫仍有永久授權的 null 歷史列，回滾時依原 OAuth 回滾流程撤銷授權並清空 SOURCE_REVIEW_OAUTH_BASE_URL，保持 OAuth 停用直到再次升級；僅撤銷但讓舊版繼續提供列表仍可能序列化失敗。不要為回滾放寬 OAuth 私有表權限。

批次／單筆明確重試另需 `20261016050100_review_retry_fencing.sql`，新 API 依選定評審 ID 原子驗證最新終態，舊前端缺少 `expected_review_ids` 的批次重試會回 422，需一同升級前端。

## ChatGPT Web 來源評審 OAuth（#100）

1. 備份並停舊 worker，升級 API，開機套用 `20261014010000_source_review_oauth.sql`；保留 ledger，healthy 後啟動同版 worker／frontend。新表撤銷所有 Data API roles（含 service_role），沿用既有 privileged `DATABASE_URL`，不需要修改 Supabase Dashboard 的 OAuth server／Exposed Schemas。
   OAuth 啟用後，資料庫備份／還原必須包含私有 `driftread.review_oauth_registration_key`：匿名 DCR 的 client ID 由此持久金鑰驗證，不能在重啟或清理時重建。已同意 client 的 metadata 保留，過期 grant／token 清理不會要求刪除並重建 ChatGPT 連線。
2. `.env` 填 `SOURCE_REVIEW_OAUTH_BASE_URL=https://正式網域`，只填 HTTPS origin；重新建立 API 容器讓環境變數載入。此值只傳 API，worker 不用；空白停用 OAuth，原 `SOURCE_REVIEW_MCP_KEY` 保留。代理必須使用同版 nginx 的 `/.well-known/` API forwarding、no-referrer／禁止 frame headers，外層 TLS 與 public origin 一致。
3. 用未帶 token 的 MCP request 確認 401 `WWW-Authenticate` 指向 public origin 的 resource metadata；確認 `/.well-known/oauth-authorization-server` 與 `/.well-known/oauth-protected-resource/api/admin/mcp` 回傳 JSON，不能是 Angular HTML。metadata／工具 scopes 含 `source:review`、`offline_access`。
4. ChatGPT 自訂 MCP 填 `https://正式網域/api/admin/mcp/`，選 OAuth；Client ID／Client Secret 留白，採 DCR public client。完成管理員帳密＋Telegram OTP → 來源評審同意頁 → 回 ChatGPT 掃描九個工具。API key、普通 Supabase JWT 或「無驗證」都不能取代此流程。
5. 呼叫 `review_policy` 確認有效政策；如要採樣，還需 `FEED_DISCOVERY_ENABLED` 和 `source.review.enabled` 皆啟用，引擎 `external_agent`。人工覆核可依既有設定跳過，不因新增 OAuth 改變政策或批次放行舊結果。
6. 驗證 refresh、`/manage/mcp-connections` 撤銷與後台登出後舊 token 失效（包括管理 token 已過期／清除的登出），普通帳號不能授權；同意期間的 JWT 自動刷新仍能回跳，來源評審 token 無法改設定／人工覆寫／一般管理 REST。OAuth MCP 驗證每 IP 每分鐘 60 次、每 process 4 個並發，超額依 429 的 Retry-After 重試。驗收本人 ChatGPT 帳號、正式 Telegram 與 Supabase session，不以假的 JWT／隔離 PostgreSQL 測試冒稱已連上 Web。

回滾前撤銷已有 grants、清空 OAuth origin 並重建 API；回滾到只支援機器 key 的版本時，Web OAuth 無法使用。新增私有表保留，原個人唯讀 MCP 與管理機器 key 不變。程式、PR 合併與正式驗收分開由 #100／#83 記錄。

如果 discovery 已成功，但進入 `/api/review-oauth/authorize` 回 `invalid_request`／`Unsupported request parameters`，確認 API 已包含授權附加參數相容性修復：舊版固定白名單會拒絕客戶端額外參數，新版依 RFC 6749 忽略不識別的提示，繼續驗證既有安全欄位。可提供網址路徑與參數名稱協助診斷，勿記錄或貼出 state、授權碼、token 或完整授權 URL。

## 回滾

Image 只有兩種 tag：`latest`（永遠指向最後一次成功的 push）與
`sha-<40 字元 commit sha>`（每次 push 到 `main`/`master` 都會多一個，永久保留、不會被覆寫）。
`docker-compose.yml` 目前寫死 `:latest`，代表**沒有額外動作的情況下，回滾等於「把
壞掉的版本重新推一次」**——不是真的回到舊 image。要回滾到指定 commit：

1. 找出想回滾到的 commit sha（`git log` 或 GitHub Actions 該次 workflow run 的 commit）。
   `api`／`worker` 用同一個 image，只需要一個 sha；`frontend` 是另一個 repo 的 image，
   通常和 backend 同一個 sha 一起改，但兩邊各自最後一次改動的 commit 不一定相同，
   混用 sha 時留意這點。
2. 改 `docker-compose.yml` 裡 `api`、`worker`、`frontend` 三個 `image:` 欄位，把
   `:latest` 換成 `:sha-<sha>`，執行 `docker compose up -d`。
   回滾完之後要嘛把三個欄位改回 `:latest`，要嘛就放著——下一次正常部署會照 CI 推的
   `:latest` 覆寫回去。
3. **回滾只回滾應用程式碼，不會回滾資料庫 schema。** migration 是只進不退的——沒有
   `down` migration 機制（見 `backend/migrations/*.sql` 只有正向 SQL）。如果要回滾的目標
   commit 早於某個已經在生產環境套用過的 migration，**先確認**那個 migration 是否為
   純加法（新增 table／column／index，不刪東西）——純加法的話舊版程式碼通常能安全地
   忽略新欄位而正常運作；如果 migration 動過既有欄位的型別、刪過欄位，或改過 RLS
   policy，回滾應用程式碼前得先手動評估資料庫端要不要一起處理，這件事沒有自動化，
   純加法以外的 migration 回滾必須逐一個案判斷。
4. 回滾後同樣跑一次「一般部署」第 4 步的健康檢查與 log 確認。

## 環境變數檢查

新增／移除／修改環境變數的三處同步規則見 `AGENTS.md` 的「環境變數維護」
（`.env.example` / `docker-compose.yml` / `scripts/gen_env.py`）。`scripts/gen_env.py`
本身只會檢查「必要變數是否為空」並補上有預設值的變數，不會幫你判斷該填什麼——`SUPABASE_URL`
／`SUPABASE_KEY`／`SUPABASE_ANON_KEY`／`DATABASE_URL`／`SUPABASE_JWT_SECRET` 五個一定要
手動從 Supabase Dashboard 填。
