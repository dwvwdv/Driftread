# 擷取、中文來源與正文保存

這一版先處理 #64 的資料生命週期，以及 #63 中可獨立落地的來源品質與監控。AI 分類／摘要佇列、語意推薦與跨 worker 的原子佇列認領仍待後續工作。

## 部署順序

本版新增 `backend/migrations/20261008031540_crawler_lifecycle.sql`、`20261008034852_global_settings.sql` 與 `20261008120957_blogroll_attempt_gate.sql`。由現有 migration runner 在 API 啟動時套用，worker 必須等 API healthy。升級前先備份資料庫，停止舊 worker，再更新 API 與 worker；不要混跑舊版文章寫入程式。新的寫入 RPC 是必要依賴，缺少 migration 時不會退回不安全的普通 upsert。

```bash
docker compose stop worker api
docker compose pull api worker frontend
docker compose up -d api
docker compose up -d worker frontend
```

遷移只新增欄位、索引、RPC 與私有監控表，不在部署時縮減任何正文。排程縮減在 `docker-compose.yml` 預設開啟（#117），程式碼層未設定時仍視為關閉；第一次啟用前先確認備份可還原、預覽一個小批次，見 [Compose 預設開啟與容量模型](#compose-預設開啟與容量模型117)。

## 文章與 discovery 分開完成

正文與摘要的 SHA256 是來源版本。相同版本與相同中繼資料不重寫文章；已縮減的版本再次出現在 RSS 中，也不會寫回完整正文。來源正文／摘要有變化時，會重新保存內容、重設抽取狀態與版本保存期；只修改標題／作者／日期則保留抽取成果。

部署主開關 `FEED_DISCOVERY_ENABLED` 與持久化暫停開關（[自動探索暫停與啟用](GLOBAL_SETTINGS.md#自動探索暫停與啟用)）都開啟時，寫入前先在本地解析外連；任一關閉則文章照常保存、只留在待抽取佇列。文章成功保存後才寫入候選與來源關聯。只有全部可處理外連及 referrer 寫入成功，才記錄該版本已抽取。零外連文件也可完成。失敗、佇列容量不足或新 host 預算不足會留下待處理狀態，舊資料以最早尚未抽取的版本優先補齊，不再只看最新文章。

單篇解析仍有 **512 KiB UTF-8 與 500 個 anchor** 上限。超限文件可保存已看到的線索，但不宣告完整抽取，也不允許縮減正文；延後 24 小時重試，讓其他文章繼續前進。這些文件需要後續更完整的分段抽取，並非已完成覆蓋。

frontier 預算在同一個共享索引內扣除。現有 Compose 使用單一 worker；多個並行 worker 或手動觸發使用不同索引時，總容量仍是軟上限。

單次 feed 採集的文章與首頁共用新增 host 名額，既有目標的 referrer 不占新增名額。文章 backlog 可以在下一個 tick 繼續處理；首頁另以 `last_blogroll_attempt_at` 與原有 `FEED_DISCOVERY_HARVEST_INTERVAL_HOURS` 限頻。資料庫原子認領在發出網路請求前記錄嘗試，失敗亦等待完整間隔，並避免並行採集重複抓首頁。首頁採集仍預設關閉。

## 中文內容

使用既有管理匯入功能：

- `backend/seeds/chinese_feeds.json` → `POST /api/admin/feeds`，收錄 TechNews、INSIDE、泛科學、報導者、Openbook，明確指定 `language=zh`、category 與 tags。
- `backend/seeds/chinese_targets.json` → `POST /api/admin/discovery/targets`，補入環境資訊中心、故事、經理人、愛料理的網站種子。網站種子仍需探測與審核，不代表已確認有效 RSS。

兩個端點皆使用 `X-API-Key`；檔案不會在啟動時自動匯入。來源匯入以 URL upsert，相同 URL 會更新提供的中繼資料，不另建 feed。

探測方向現由後台「全域設定」管理，支援多組語言、分類、網站與名額。原先兩個中文 seed 環境變數已移除；共用設定表、版本處理與名額規則見 [GLOBAL_SETTINGS.md](GLOBAL_SETTINGS.md)。

候選會排除明確的留言 RSS，並合併慣用 `/feed`、`/rss`、`/atom` 尾端斜線別名；不會把同一 host 的所有 feed 合成一個。hold／reject 也會約束別名，保留審核決策。

## 預覽與縮減正文

`GET /api/admin/retention/stats` 回傳目前文章、完整正文、僅摘要、待抽取、已縮減數量，以及文章表、索引、合計與整個資料庫的 bytes。它是即時快照。管理首頁同時顯示每日歷史與成長率（見下節）。

預覽預設為 `dry_run=true`：

```text
POST /api/admin/retention/run?retention_days=30&limit=200
X-API-Key: <admin key>
```

回傳 `dry_run`、`eligible`、`compacted`、`content_bytes`。只有明確使用 `dry_run=false` 才修改正文；手動執行不受排程開關限制。`content_bytes` 是原始正文 bytes，並非磁碟回收量；PostgreSQL 仍需正常 vacuum，檔案也不一定立刻縮小。

條件同時成立才縮減：目前正文版本超過保存天數（`content_revision_at`；未追蹤版本的舊資料退回 `fetched_at`）、完整抽取過目前版本（discovery 關閉或暫停期間入庫的版本尚未抽取，因此不會被縮減）、正文尚未縮減、沒有任何使用者收藏或已讀關聯、來源沒有任何使用者訂閱。資料庫鎖定後再檢查，保護已先提交的使用者互動。縮減僅將 `content` 設為 NULL，保留 article ID、URL、標題、摘要與其他欄位；不刪除文章或使用者關聯。搜尋 generated vector 隨正文同步更新，已移除的正文詞彙不再提供命中。閱讀頁顯示保存期限提示與原文連結。

套用後無法由這些欄位自行還原原始正文；需要備份或原站重新取回。未來新增的收藏不會還原正文。已有訂閱的來源全部受保護，因此本策略不能保證資料量停止增長。也不要回退到會無條件 upsert 正文的舊版 worker。

自動排程的開關：

| 變數 | 預設 | 範圍／用途 |
|---|---|---|
| `ARTICLE_RETENTION_ENABLED` | Compose `true`；程式碼 `false` | 程式碼只認明確 `true/1/yes/on`，拼錯或未設定都是關閉；`docker-compose.yml` 以 `${ARTICLE_RETENTION_ENABLED:-true}` 補上預設，`.env` 明確設 `false` 時仍停用 |
| `ARTICLE_RETENTION_DAYS` | `30` | 7–3650 天 |
| `ARTICLE_RETENTION_BATCH_SIZE` | `200` | 每輪 1–1000 篇 |
| `ARTICLE_RETENTION_INTERVAL_MINUTES` | `1440` | 最多 10080 分鐘；每次只做一批 |

### Compose 預設開啟與容量模型（#117）

**為什麼只改 Compose 預設**：issue 指定的是「Compose 範例部署」預設開啟。`retention_enabled()` 的 fail-closed 邊界（未設定、拼錯都關閉）保留不動，不經 Compose 直接執行 `worker.py` 的部署語意不變。`api` 與 `worker` 兩個區塊用同一個預設，避免兩個容器的設定互相矛盾；實際排程只在 worker，後台手動縮減不受此開關限制。`scripts/gen_env.py` 只回報、從不把這個變數寫進 `.env`，`.env.example` 也只放註解，所以新 `.env` 交給 Compose 預設，既有 `.env` 裡明確的 `false` 不會被升級或重跑腳本覆寫。開關與 refresh／discovery／來源評審彼此獨立（`tests/test_deploy_defaults.py`、`tests/test_operations.py`）。

**開啟旗標不等於問題解決**，原因有三：

1. **縮減必須等目前版本完成外連抽取**。`FEED_DISCOVERY_ENABLED=false`（Compose 預設）或探索暫停時，新入庫的正文不會被標記為已抽取，因此永遠不符合條件。這種部署開啟 retention 實際上不會縮減新文章；是否允許縮減未抽取版本（代價是那些正文的外連不會被探勘）仍待使用者決定。
2. **每天 200 篇追不上入庫量**。下表以 2026-10-22 約 07:20 UTC 對正式資料庫的唯讀查詢為準（時間點快照，非固定日產量）。
3. **縮減不刪文章列**。縮減後每篇仍留約 1.4 KB 的列（標題、摘要、由標題與摘要重算的 `search_vector`），加上索引、版本與來源證據；這部分會隨文章數持續成長，且 PostgreSQL 一般 VACUUM 後檔案不會立刻變小。

| 量測 | 值 |
|---|---|
| 資料庫／`articles` 合計／`articles` 索引 | 523.9 MB／337.4 MB／86.3 MB |
| 文章數；有正文；已縮減 | 38,766；14,680；3,311（2026-10-21 08:06–08:09 UTC 的手動縮減，正式環境沒有任何 retention 排程紀錄） |
| 新增：最近 24 小時／7 天 | 3,945／23,085；24 小時新文章中 55% 有正文 |
| 單列平均大小（`pg_column_size`，含 TOAST 壓縮後） | 有正文 11.9 KB（正文 4.1 KB、`search_vector` 5.9 KB）；無正文 1.4 KB |
| 每日新增列資料 | 2026-10-20 約 37.6 MiB、10-21 約 32.4 MiB（不含索引；索引平均約 2.2 KB／篇） |
| 目前超過 30 天且符合條件 | 74 篇（第一次排程只會縮減這些，遠低於 200） |
| 最大單一來源 | DEV Community 7 天 2,648 篇、52.5 MiB（約 20 KB／篇，7 天文章列資料約三分之一）；其他前 10 名皆 ≤ 4.1 MiB |

**穩態估算**（每日 N≈4,000 篇、55% 有正文、縮減可省約 10.5 KB／篇、約 95% 不受保護）：

- 每日新增約 33 MiB 列資料＋約 8.5 MiB 索引 ≈ **41 MiB／天**。保存天數 D 內的正文窗口約 D × 41 MiB：D=30 約 1.2 GiB，D=7 約 290 MiB。**500 MB 的 Free Plan 在目前入庫量下，30 天正文保存不可能成立**；即使最短 7 天，也只剩約 180 MiB 給其他資料與已縮減的舊列。
- 縮減後永久殘留約 1.4 KB 列＋索引＋版本與來源證據（`article_revisions` 約 54 MB、`article_discoveries` 約 30 MB），合計約 3.5–4.5 KB／篇，即 **約 13–17 MiB／天的無上限成長**。只靠正文縮減，DB 仍會在數週到一個多月內再次碰到 500 MB。
- 排程速率：每天需要縮減約 2,100 篇（4,000 × 55% × 95%）才跟得上；預設 200 篇／天只有約 10%。現在 backlog 很小（74 篇），但 2026-10-16 單日入庫 10,692 篇（5,739 篇有正文），約 2026-11-15 起會一次進入可縮減範圍，以 200 篇／天要將近一個月，同時每天又新增約 2,100 篇，永遠追不上。

**追趕設計（提案，未改預設）**：縮減 RPC 在交易內逐篇鎖住來源列直到 commit，同一來源的 refresh 入庫會等它；所以加速優先「縮短間隔、維持批次」，而不是放大批次。`ARTICLE_RETENTION_INTERVAL_MINUTES=60` 搭配批次 200，每天最多 4,800 篇，單次鎖定時間與現在相同，足以追上上述 backlog 與日增量。`30 days／200／1440` 預設值本次未改，需使用者決定。

**入庫治理選項（提案，未實作；不封存、不刪除既有文章）**

| 方案 | 預估效果（以上表為準） | 資料完整性／閱讀價值 | 實作成本 |
|---|---|---|---|
| 每來源每日入庫預算（例如 200 篇），超額記錄而非靜默丟棄 | DEV 919→200、SMH 310→200、The West 207→200，24 小時新增約 −21%（−836 篇）；DEV 的列資料約佔 7 天總量 1/3，影響最大 | 超額項目不入庫，需要記錄超額數與被略過的 URL，否則無法追溯；對高產量投稿平台是主動選擇少收 | 中：`ingest_article_batch` 前計數、超額 ledger、後台設定與告警 |
| 高位元組來源只存摘要（把既有 `fulltext_policy=summary_only` 從「只影響閱讀」延伸到「入庫不存正文」） | DEV 一個來源即約 −7 MiB／天（7 天文章列資料約 1/3） | 列與 URL 全保留，閱讀頁導向原文；外連探勘只能用摘要；已訂閱來源要另外決定 | 低–中：入庫時依來源政策清掉 content，與縮減共用語意 |
| `search_vector` 只索引標題＋摘要 | 有正文的列 `search_vector` 平均 5.9 KB，比正文本身（4.1 KB）還大；改成只索引標題摘要，有正文的列約省 45% | 正文仍可閱讀，但全文搜尋不再命中正文詞彙（縮減後的文章本來就不命中） | 中：generated column 改寫需重建欄位與 GIN 索引，大表要離峰 |
| 舊文章列到期（例如 90 天後刪除無訂閱／收藏／已讀的列） | 唯一能讓列數與索引有上限的方案 | 會失去歷史文章與其推薦訊號；issue 明確要求不得擅自刪除 | 中：需新 RPC、保護條件、cascade 與 VACUUM 規劃 |
| 保留完整 feed reader，改用付費方案 | Pro 方案 8 GB：不縮減約半年；30 天縮減下約 1.2 GiB 窗口＋每天 13–17 MiB，約一年以上 | 不流失任何 RSS 項目 | 低：成本由方案費用承擔 |

建議預設（待決定）：先對 DEV Community 這類高位元組來源啟用「入庫只存摘要」，同時把 retention 間隔改為 60 分鐘、保存天數評估 14 天；每來源預算先只做告警（現有 24 小時 500 篇門檻），觀察 7 天後再決定是否硬性限額。

觀測面：`GET /api/admin/operations/ingest?hours=24|168` 與後台「來源入庫量與版本更新」的 24 小時／7 天切換，列出每來源新增篇數與超門檻來源；每來源位元組目前沒有 API，需用 RUNBOOK 的逐來源 `pg_column_size` 查詢。全站容量警戒沿用「容量成長趨勢」與每表容量面板。

## 每日容量歷史

worker 隨常駐的來源評審設定監看另跑 `storage` 佇列工作，每小時檢查一次；資料庫只保存 UTC 每天第一筆成功的採樣。即使 refresh／discovery／retention 三種排程全部關閉，worker 仍監看獨立評審設定並需要 Supabase client。沒有新增開關或環境變數，也不會因此啟用正文縮減。

`record_article_storage_snapshot` 在交易 advisory lock 下去重，當天已有採樣就不再掃描文章。成功採樣與去重都是安全可重跑；資料庫故障沿用 queue 的三次重試／退避，失敗不停止採集。`article_storage_snapshots` 僅保存日期、採樣時間與既有即時快照的九項 aggregate，不留正文、URL 或使用者資料。保留最近 90 個 UTC 日，每次檢查刪除更早紀錄；停機不補造歷史。

`GET /api/admin/retention/history?days=30` 僅讀已保存紀錄，`days` 範圍 1–90；與其他管理端點共用管理 session／機器 API Key 驗證。回傳 `observed_at`、`retention_days`、按日期升序的 `points` 與 `growth`。不在 GET 中採樣、不因開管理頁增加掃描成本。

至少兩次採樣、跨滿 24 小時才有 `growth`；否則為 null。成長率是查詢視窗內最早與最新採樣的差，除以實際 elapsed days，包含文章數、table／index／total／database bytes。缺日使用實際間隔，負增量保留，不視為錯誤。它是期間平均淨變化，不是新增文章吞吐量、清理回收量或 quota 預測；database bytes 也包含其他 schema。即時快照與最近 30 天歷史各自載入，後台標示最近採樣時間與計算區間，無紀錄、資料不足與載入失敗分開呈現。

部署需新增容量歷史 migration，順序見 [RUNBOOK](RUNBOOK.md#每日容量歷史)。只有 service_role 可讀寫私有 RLS 表並呼叫 INVOKER RPC，PUBLIC／anon／authenticated 無權限。

## 每表容量與 MVCC 觀測

#114 E：資料庫逼近 500 MB 時，要分清「有效資料成長」與「VACUUM 已回收、可在表內重用但檔案沒縮小的空間」，不能把相鄰兩日快照的差額都歸因於 bloat，也不應把 `VACUUM FULL` 當例行任務（`ACCESS EXCLUSIVE` 鎖、重建暫存空間與逾時風險，手動步驟見 [RUNBOOK](RUNBOOK.md#資料庫接近-supabase-容量上限)）。

`20261022020000_storage_observability.sql` 在既有每日採樣的同一列新增 `tables` 快照（同一 advisory lock、UTC 每日去重與 90 天保存期）。`table_storage_stats()` 只讀 catalog 與 `pg_stat_user_tables`／`pg_stat_database`，不掃表內容；取 `driftread` schema 依總量前 25 大的表，並固定包含 `articles`、`article_revisions`、`article_discoveries`、`sync_changes`、`discovery_targets`、`background_jobs`。每表保存 heap（`pg_table_size` 扣掉 TOAST）、TOAST（含其索引）、index、合計 bytes，`n_live_tup`、`n_dead_tup`、累積 `n_tup_ins／upd／del／hot_upd`、`last_vacuum`、`last_autovacuum`、`vacuum_count`、`autovacuum_count`，以及資料庫層級的 `stats_reset`。部署當天已採樣過的日子會在下一次每小時檢查補一次表快照，不重掃文章、不改當天 aggregate。

`GET /api/admin/retention/tables?days=7`（`days` 1–90）回傳即時的 `current`（同一函式，GET 不寫入）與相鄰兩筆已保存表快照之間的 `intervals`（最新在前，缺日保留實際間隔）。區間增量是累積計數相減：任一計數變小（單表重設、crash recovery）或 `stats_reset` 改變時，該表的 INSERT／UPDATE／DELETE／HOT 為 null（後台顯示「無法計算」），不顯示負數；新出現的表也不相減。容量變化保留負值。`n_dead_tup` 與比例是 PostgreSQL 的估計值，代表可被 VACUUM 回收的舊列版本；確切的可用空間需要 `pgstattuple` 之類的擴充，本版不安裝。

## 來源入庫量與版本異常

#114 D 與 B 的最後一點：只做觀察與後台標示，**不**限制每來源每日入庫量、不節流、不封存、不啟用 retention；那些是待使用者決定的產品政策。

`GET /api/admin/operations/ingest?limit=10&hours=24`（`limit` 1–50；`hours` 只接受 24 或 168，#117）呼叫 `source_ingest_activity(hours, limit, …門檻)`，回傳最近 24 小時或 7 天：

- `top_ingest`：新增文章最多的來源，含其中 `backfill`（舊文回填）篇數，區分刊登時間與入庫時間。
- `top_revisions`：版本更新（`revision_number > 1`）最多的來源，含被更新的文章數、與前一版比較的正文變更數與僅中繼資料變更數（前一版已清除者兩者都不計）。
- `flagged`／`flagged_sources`：在截斷前對**全部**來源套用門檻的結果。只靠「每篇平均版本」超標的來源，總更新數未必排得進 `top_revisions`；所以超標總數與清單（最多 `limit` 筆）由 SQL 另外計算，不從兩份前 N 名推算。門檻值只在 Python 定義、以參數傳入；判斷式只在 SQL 寫一次，每筆回傳 `alerts`（超標原因），API 原樣顯示。

查詢只走新索引 `article_revisions(recorded_at)` 的時間範圍，再以主鍵關聯文章，不全表掃描 `article_revisions` 或 `articles`。每篇新文章入庫時都會在同一交易記錄版本，之後的版本更新，所以新增篇數即使第 1 版已被 10 版上限清掉仍是精確值；版本更新數在單篇一天超過 10 次時是下限。`article_revisions` 只新增不更新，額外索引不影響 HOT 更新。

標示門檻（`backend/services/ingest_activity.py`，也列在 [FEATURES](FEATURES.md#6-生效中的限制與門檻)）：24 小時新增 ≥ 500 篇；版本更新 ≥ 200 次；或至少 30 次更新且每篇平均 ≥ 3 次。7 天視窗只有新增篇數門檻乘以 7；版本更新的兩個門檻不變：每篇只保留 10 個版本，版本數在長視窗內有上限，放大門檻會讓同一個持續重送的來源在 7 天裡反而不被標示。因此 7 天視窗也會標示「每篇每天一版、連續一週」這類較慢但持續的重送。超過門檻只在後台列出原因與筆數，請人工判斷是真實新聞量還是來源反覆重送相同項目。

## 來源抓取診斷（#106）

時間欄位分別表示：`last_fetch_at` 是外連開始嘗試，`last_ok_at` 是 HTTP 抓取／解析成功（包含 304），`last_fetched_at` 是既有刷新完成時間（也包含准入排除）。`last_ok_at` 不代表文章已成功入庫；寫入失敗仍保留傳輸成功的證據，並另外記錄失敗、退避與下次嘗試。

`feeds.last_fetch_result` 保存最新一次的有界診斷，公開 API 僅提供安全結果碼、時間與計數，不保存原始外連／DB 例外或回應內容。舊來源維持 null，不能事後推測歷史原因。後台使用中及封存列表會區分等待首次抓取、歷史原因未知、准入排除、空內容、解析／傳輸／寫入失敗，並顯示未封存來源的 `next_fetch_at`。

| `outcome` | 語義 |
|---|---|
| `in_progress` | 已開始；尚無完成結果，可能仍執行或中斷，不能宣稱成功 |
| `fetch_timeout`／`http_error`／`fetch_failed`／`parse_failed` | 逾時、HTTP 錯誤（附狀態碼）、其他抓取錯誤、RSS／Atom 解析失敗 |
| `ingest_failed` | 抓取成功，但文章保存、計數或完成狀態寫入未完成；不保存本輪新 validator |
| `excluded` | 准入檢查排除並封存；管理端另外提供既有排除原因 |
| `empty_feed` | RSS／Atom 解析成功但零項目；傳輸成功與有文章可讀分開呈現 |
| `no_usable_entries` | 所有解析項目皆缺少網址，沒有可入庫項目 |
| `unchanged`／`updated`／`not_modified` | 文章總數未增加、增加、HTTP 304 |

有 body 時記錄 `entries_seen`、`missing_url`、`duplicate_entries`（同批完全相同網址）；成功完成另外記 `upserted`（含更新）、`new_articles`（前後精確計數差值）與 `total_articles`。這些不是跨來源去重數；鏡像准入排除仍由來源身分檢查負責，不能把「無新增」都說成被判重。

零文章來源或上次 `ingest_failed` 的排程會略過 validator、重新下載 body，避免 304 掩蓋未完成的入庫；其他排程維持 conditional GET。後台單筆「更新文章」一律重新下載。

`20261017135504_atomic_feed_article_counts.sql` 將計數維護移到每批最多 200 篇的 `ingest_article_batch` 交易內：先鎖來源，再保存文章與 provenance，最後精確重算 `article_count`。後續批次、語言判定或完成狀態更新失敗，不會讓已提交文章留下舊計數。Python 不再將先前讀到的 COUNT 寫回；刷新及 discover/import 共用 service-only、INVOKER 的 `reconcile_feed_article_count`，以相同來源鎖重算並回傳整數。304 也會重算，讓歷史落差可隨正常排程修復；空 feed 同樣可修復。這不把 DB 錯誤偽裝成抓取成功，原失敗狀態與退避仍保留，未知計數不當 0。

`last_fetch_result.stage` 使用固定階段碼：`record_fetch`、`count_before`、`admission`、`ingest`、`count_after`、`count_reconcile`、`language`、`completion_update`，成功為 `complete`。失敗日誌只記來源 ID 與階段，不記原始例外或憑證。舊版 `ingest_failed` 沒有階段，不能事後推斷其底層例外。全目錄唯讀核對與各時間點證據見 [#106 調查](ISSUE_106.md)。

## Worker 監控

`GET /api/admin/operations?limit=20`（需 `X-API-Key`）提供 worker heartbeat、最近迴圈與最近失敗數。refresh、discovery、retention、storage 分別記錄，每 30 秒更新 heartbeat；超過 90 秒視為 stale。正在執行但 heartbeat 過期的 run 在讀取時呈現 interrupted；尚無紀錄呈現未知。這是近期執行視窗，不是每日累積報表。

近期視窗常被頻繁的 refresh 填滿，所以另回傳 `latest_runs`：每種 kind 在保存期內最近一次 run，沒有則為 null；由 `latest_worker_runs(kinds)` RPC 一次取回（每個 kind 一次 `worker_runs(kind, started_at DESC)` 索引的 LIMIT 1 查詢），不為每個 kind 各發一次 PostgREST 請求。每個 worker loop 的 heartbeat 另帶 `config`：該 worker 程序自己解析的 `FEED_REFRESH_ENABLED`、`FEED_DISCOVERY_ENABLED`，以及 `ARTICLE_RETENTION_ENABLED`／保存天數／批次／間隔（只有布林與整數，無密鑰）。`worker_config` 取最新一筆帶設定的 heartbeat，標示回報時間與是否過期；舊版 worker 不帶設定時為 null，後台顯示「狀態未知」，不當成已停用。排程縮減啟用時一律直接套用（`mode=apply`），不做 dry-run；後台手動縮減仍預設 dry-run。API 容器的環境變數可能與 worker 不同，所以不從 API 讀取。

監控寫入失敗會記錄 log，不停掉擷取。私有表不開放 anon／authenticated。run 與 heartbeat 保留 30 天，每小時最多清除 1000 筆過期紀錄。狀態查詢最多回傳 100 個 run、200 個 worker。主要文章寫入／抽取移到 thread，但其他短同步 DB 操作仍可能短暫延遲 event loop，heartbeat 並非獨立程序的存活探針。

## 驗證

後端使用 `pytest`，前端使用 `npm test -- --watch=false` 與 `npm run build`。Backend CI 使用 PostgreSQL 17 service，設定 `DRIFTREAD_TEST_DATABASE_URL` 讓 `tests/test_postgres_lifecycle.py` 自動建立專用隔離資料庫、用實際 runner 執行完整 migration chain 與 SQL fixtures，並以獨立連線驗證未提交收藏／已讀／訂閱的 FK 鎖、新正文與舊抽取完成標記、正文縮減與相同版本 refresh 的競態。另驗證真正的 anon 拒絕與 owner RLS。只有 localhost 的 `*_test` 資料庫可作為建立測試資料庫的入口；不使用正式 `DATABASE_URL`。本地未提供測試 URL 時會 skip，CI 必須執行。
