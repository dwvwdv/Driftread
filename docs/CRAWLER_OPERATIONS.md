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

遷移只新增欄位、索引、RPC 與私有監控表，不在部署時縮減任何正文。`ARTICLE_RETENTION_ENABLED=false` 是預設值。第一次啟用前先觀察資料量、確認備份可還原，再預覽一個小批次。

## 文章與 discovery 分開完成

正文與摘要的 SHA256 是來源版本。相同版本與相同中繼資料不重寫文章；已縮減的版本再次出現在 RSS 中，也不會寫回完整正文。來源正文／摘要有變化時，會重新保存內容、重設抽取狀態與版本保存期；只修改標題／作者／日期則保留抽取成果。

啟用 discovery 時，寫入前先在本地解析外連；文章成功保存後才寫入候選與來源關聯。只有全部可處理外連及 referrer 寫入成功，才記錄該版本已抽取。零外連文件也可完成。失敗、佇列容量不足或新 host 預算不足會留下待處理狀態，舊資料以最早尚未抽取的版本優先補齊，不再只看最新文章。

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

條件同時成立才縮減：目前正文版本超過保存天數（`content_revision_at`；未追蹤版本的舊資料退回 `fetched_at`）、完整抽取過目前版本、正文尚未縮減、沒有任何使用者收藏或已讀關聯、來源沒有任何使用者訂閱。資料庫鎖定後再檢查，保護已先提交的使用者互動。縮減僅將 `content` 設為 NULL，保留 article ID、URL、標題、摘要與其他欄位；不刪除文章或使用者關聯。搜尋 generated vector 隨正文同步更新，已移除的正文詞彙不再提供命中。閱讀頁顯示保存期限提示與原文連結。

套用後無法由這些欄位自行還原原始正文；需要備份或原站重新取回。未來新增的收藏不會還原正文。已有訂閱的來源全部受保護，因此本策略不能保證資料量停止增長。也不要回退到會無條件 upsert 正文的舊版 worker。

自動排程需要明確啟用：

| 變數 | 預設 | 範圍／用途 |
|---|---|---|
| `ARTICLE_RETENTION_ENABLED` | `false` | 明確 `true/1/yes/on` 才啟用 |
| `ARTICLE_RETENTION_DAYS` | `30` | 7–3650 天 |
| `ARTICLE_RETENTION_BATCH_SIZE` | `200` | 每輪 1–1000 篇 |
| `ARTICLE_RETENTION_INTERVAL_MINUTES` | `1440` | 最多 10080 分鐘；每次只做一批 |

## 每日容量歷史

任一 refresh／discovery／retention 排程啟用時，worker 另跑 `storage` 佇列工作，每小時檢查一次；資料庫只保存 UTC 每天第一筆成功的採樣。三種排程全部關閉時仍維持 worker 直接退出，不另建 Supabase client。沒有新增開關或環境變數，也不會因此啟用正文縮減。

`record_article_storage_snapshot` 在交易 advisory lock 下去重，當天已有採樣就不再掃描文章。成功採樣與去重都是安全可重跑；資料庫故障沿用 queue 的三次重試／退避，失敗不停止採集。`article_storage_snapshots` 僅保存日期、採樣時間與既有即時快照的九項 aggregate，不留正文、URL 或使用者資料。保留最近 90 個 UTC 日，每次檢查刪除更早紀錄；停機不補造歷史。

`GET /api/admin/retention/history?days=30` 僅讀已保存紀錄，`days` 範圍 1–90；與其他管理端點共用管理 session／機器 API Key 驗證。回傳 `observed_at`、`retention_days`、按日期升序的 `points` 與 `growth`。不在 GET 中採樣、不因開管理頁增加掃描成本。

至少兩次採樣、跨滿 24 小時才有 `growth`；否則為 null。成長率是查詢視窗內最早與最新採樣的差，除以實際 elapsed days，包含文章數、table／index／total／database bytes。缺日使用實際間隔，負增量保留，不視為錯誤。它是期間平均淨變化，不是新增文章吞吐量、清理回收量或 quota 預測；database bytes 也包含其他 schema。即時快照與最近 30 天歷史各自載入，後台標示最近採樣時間與計算區間，無紀錄、資料不足與載入失敗分開呈現。

部署需新增容量歷史 migration，順序見 [RUNBOOK](RUNBOOK.md#每日容量歷史)。只有 service_role 可讀寫私有 RLS 表並呼叫 INVOKER RPC，PUBLIC／anon／authenticated 無權限。

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

監控寫入失敗會記錄 log，不停掉擷取。私有表不開放 anon／authenticated。run 與 heartbeat 保留 30 天，每小時最多清除 1000 筆過期紀錄。狀態查詢最多回傳 100 個 run、200 個 worker。主要文章寫入／抽取移到 thread，但其他短同步 DB 操作仍可能短暫延遲 event loop，heartbeat 並非獨立程序的存活探針。

## 驗證

後端使用 `pytest`，前端使用 `npm test -- --watch=false` 與 `npm run build`。Backend CI 使用 PostgreSQL 17 service，設定 `DRIFTREAD_TEST_DATABASE_URL` 讓 `tests/test_postgres_lifecycle.py` 自動建立專用隔離資料庫、用實際 runner 執行完整 migration chain 與 SQL fixtures，並以獨立連線驗證未提交收藏／已讀／訂閱的 FK 鎖、新正文與舊抽取完成標記、正文縮減與相同版本 refresh 的競態。另驗證真正的 anon 拒絕與 owner RLS。只有 localhost 的 `*_test` 資料庫可作為建立測試資料庫的入口；不使用正式 `DATABASE_URL`。本地未提供測試 URL 時會 skip，CI 必須執行。
