# PostgreSQL 工作佇列與 API watchdog

## 工作與排程

`backend/services/job_queue.py` 使用 scoped Supabase client 呼叫 PostgreSQL RPC。佇列失敗時不退回記憶體執行。`refresh`、`discovery`、`retention` 以完整 cycle 為工作單位，沿用各自 enable flag、tick、抓取限制與內部並發。discovery／retention 的預設關閉狀態不變。

`background_jobs` 保存 payload、到期時間、attempt、worker ID、lease token 與結果；`background_job_kinds` 保存每種工作的全域並發上限，三種 cycle 預設各一。部分唯一索引確保相同 kind／singleton key 最多一個 queued 或 running 工作。每輪以 `scheduler` singleton enqueue；重啟會沿用既有待辦與排程時間。成功或耗盡重試時，終態與下一輪入列在同一個交易提交。未捕捉例外重試最多三次，以 15、30 秒退避；通用 RPC 的最大重試次數為 20，退避上限一小時。已回傳 partial 的 cycle 沿用其 feed／target 退避，不再次重跑整輪增加失敗計數。

需要在業務寫入時建立工作，應在同一個 SQL transaction 內呼叫 `enqueue_background_job`。兩次 HTTP 呼叫不具有交易原子性。目前三種週期不修改業務資料後再入列，週期結果與下一輪則由單一 `finish_background_job` RPC 處理。

priority 範圍 −100～100，預設 0；到期工作先按 priority 由高到低，再按 available_at、created_at、id 排序。job timeout 存在每一筆 job，refresh／retention 為 900 秒，discovery 為 1800 秒；達到整輪期限時取消 action、停止續租，以 TimeoutError 記錄並沿用 bounded retry。下一輪保留 priority／timeout。

claim 在交易內序列化該 kind 的容量判斷，再以 `FOR UPDATE SKIP LOCKED` 取得到期工作。租約 120 秒，每 30 秒續租。renew／finish 都驗證 token 與租約到期時間；舊 worker 無法完成新 attempt。失去租約或續租失敗時取消 action，停止續租並留下原租約供回收。RPC 只記錄例外類型，不保存外連 URL、正文或 key。

這是 **at-least-once** 工作交付。完成業務寫入後、ack 提交前程序中止，會再次執行該 cycle。lease fencing 保護佇列狀態，不會撤銷已提交的業務寫入，也不會終止 `asyncio.to_thread` 中已開始的同步函式。文章身份／相同來源版本的冪等性仍由文章寫入 RPC 負責；手動管理端點仍直接執行既有動作，不宣稱與背景 cycle 共用排他鎖。

每次 worker polling 及 API watchdog 以 bounded reconciliation 回收過期租約；每次最多 100 筆。退回 queued 並持久化退避，達到 max attempts 則 dead。dead 保留供管理者檢查；重複排程仍建立下一輪，不會永久停止採集。poll 最多每五秒一次，不改變實際 cycle tick。停用的 kind 不再 claim；其既有 queued 工作保留，重新啟用時接續。重啟後明確提供的新 tick／timeout 會更新 active singleton 設定；已排定的 available_at 保留，下一輪使用新間隔。未提供的 recurrence／priority／timeout 不覆寫既有設定。

## 停機與部署

SIGTERM／SIGINT 立即停止新 enqueue／claim；若 signal 與已提交 claim 競跑，亦不啟動新 action。正在執行的 cycle 最多排空 20 秒，之後取消 coroutine；最終 heartbeat 最多等五秒。未完成工作不立即釋放，讓租約到期後回收，避免仍在執行的資料庫 thread 與新 attempt 過早重疊。Compose `stop_grace_period: 30s` 為容器程序提供停止期限；同步執行緒／無法即時取消的既有同步 I/O 仍可能需要容器最終終止。沒有新增環境變數。

新增 migration：

- `backend/migrations/20261009000000_background_queue.sql`
- `backend/migrations/20261009000100_worker_watchdog.sql`

API 啟動時由既有 runner 套用；worker 等 API healthy。升級時先停止舊 worker，再更新 API、等待 healthy，最後啟動新 worker。不要混跑使用記憶體排程的舊 worker 與新佇列 worker。migration 不抓取外部網站、不縮減正文、不啟用 discovery 或 retention。正式 Supabase migration／部署仍需運維實際驗證。

## API watchdog 與管理觀測

API lifespan 啟動獨立 coroutine，每 30 秒透過 RPC 檢查 worker heartbeat。首次檢查在 API 啟動 30 秒後；不讓監控故障阻止 API 啟動。它與 worker 位於不同程序，但不是外部主機的存活探針：API 或資料庫一起故障時不能作出判斷。API 不寄送 email、Slack 或其他外部通知。

超過 90 秒未 heartbeat 的 running worker 建立私有 `worker_alerts` 事件。每次最多新增 100 個事件，同一 worker 最多一個未解決事件；不同 API replicas 以 transaction advisory lock 去重。heartbeat 恢復或正常停止時標記事件 resolved。重新啟動的程序使用新的 worker ID，舊程序的未解決事件仍保留為失聯紀錄。最多 1000 個缺少健康 worker 的 running run 轉成 persisted interrupted，並記錄 `WorkerHeartbeatLost`。沒有 worker 紀錄時仍呈現未知，不推斷從未啟動的排程故障。

告警／恢復／interrupted／reconciliation 的非零數量以結構化計數記錄 API log。`GET /api/admin/operations` 沿用 `X-API-Key`，新增：

- `queue`：queued、running、dead、succeeded 數量及 `next_available_at`。
- `recent_jobs`：最近 `limit` 個工作，含 kind、attempt、到期與終態，不回傳 payload 或 lease token。
- `alerts`：最近 `limit` 個事件，含建立與解決時間。

原有 heartbeat、recent_runs 與近期失敗統計保留。所有新系統表開啟 RLS，零公開 policy，表與 RPC 明確撤銷 PUBLIC／anon／authenticated 權限，僅 service_role 存取。API watchdog 每 tick 最多刪除 1000 個超過 30 天的 succeeded 工作及 1000 個已 resolved 事件；queued、running、dead、未解決事件不刪除。原有 heartbeat/run 的 30 天有界清理繼續由 recorder 執行。

## 驗證

`tests/test_background_jobs.py` 共用隔離 PostgreSQL fixture，跑完整 migration chain、重放兩支 migration，驗證交易回滾、並行 singleton／capacity、SKIP LOCKED、lease fencing、重試／dead／週期接續、有界 sweep、告警去重／恢復／清理與真正的 anon／authenticated 權限拒絕。`tests/sql/test_background_jobs.sql` 提供資料庫 invariant fixture。

`tests/test_queue_worker.py` 驗證 RPC adapter、續租失敗取消 action、queue 故障不開始採集、signal 與 claim 競跑、排空期限、watchdog tick 及 API task lifecycle。既有 worker／operations 測試改用明確 queue protocol fake。
