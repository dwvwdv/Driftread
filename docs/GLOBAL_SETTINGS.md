# 全域設定管理

## 自動探索暫停與啟用

後台「總覽」及「待探索來源」頁有共用的自動探索開關，不需重啟 API／worker。`discovery.control` 存 `{ "enabled": true|false }`，預設 true 以保留既有部署意圖；真正執行仍須部署主開關 `FEED_DISCOVERY_ENABLED=true`，後台與 MCP 都不能繞過它。部署未開放時顯示原因，不能透過按鈕啟用爬取。

暫停保留候選、待探索 URL 與背景 queued 工作；worker 不再啟動 discovery 工作。執行中的探索 cycle 每秒重讀開關，關閉或讀取失敗即取消 coroutine，不把暫停計成來源抓取失敗。已送出的網路請求、已提交到 thread 的同步資料庫操作仍可能完成；暫停回應表示設定已儲存，不代表所有遠端 I/O 已停止。啟用後依既有排程接續，不清空佇列，也不強制立即跑一輪。

來源評審、採樣、外部 MCP 提交與 AI metadata 准入只受獨立 `source.review` 政策控制；探索開關及 `FEED_DISCOVERY_ENABLED` 不改寫評審決策，也不強制人工覆核。引用證據自動收錄（`referrer_auto`）仍屬探索管線，SQL 與暫停共用設定列鎖；AI 准入不取得該鎖。既有 RSS 更新及管理員操作照常。評審停用不啟動採樣或付費，skip 只控制有效決策是否需人工覆核，詳見 [來源評審](SOURCE_REVIEW.md#啟用與人工覆核)。

`GET /api/admin/discovery/control` 回傳 `enabled`、`deployment_enabled`、`effective_enabled`、`version`、`updated_at`；`PATCH` 接受 `{enabled,expected_version}`，版本衝突回 409。既有 settings PUT 也可儲存此已註冊 key。`POST /api/admin/discovery/run` 在暫停時回 503，在途手動探索遇暫停回 `paused=true`；後台同步停用手動執行按鈕。MCP `get_discovery_control`／`set_discovery_enabled` 提供相同版本與部署限制，要求 `catalog:manage`，只開放此控制項、不接受任意全域設定 key。

升級先停止舊 worker，由 API 套用 `20261018034910_discovery_control.sql`，再啟動新版 worker／frontend；舊 worker 沒有輪詢取消機制。migration 不會把已有暫停改回啟用。沒有新增部署環境變數。

後台的 **全域設定**（`/manage/settings`）提供共用設定入口。第一個區塊是「探測方向」，可以新增、編輯、暫停與移除多組語言／分類設定，按「儲存全域設定」後下一輪探測生效，不需重啟 worker。

每組方向包含名稱、目標語言（例如 `zh-tw`、`en`）、分類、啟用狀態、每輪保留名額與網站種子。預設有繁體中文的科技、商業、科學、社會、文化、環境、生活七組，涵蓋九個網站；可自行新增其他語言與領域。

語言與分類描述想找的內容，以選定網站引導探測；它們不是自動辨識結果，也不是對整個網路的分類搜尋。既有 feed 語言／分類不會被這些設定覆寫，候選仍需審核。名額是每輪保留的探測數，不是比例或文章數；設為 0 只取消保留名額，不停用整組。

啟用組的保留名額合計最多占該輪預算一半。各組依最早到期時間輪流取一個目標，再進下一輪分配；重疊網站的同一目標只取一次。剩餘預算維持原本的 referrer 證據排序，並排除所有正名額啟用組的 seed，避免額外種子溢入一般名額；一般佇列不足時不以這些種子補滿。保留名額只涵蓋 `pending` 且到期的 `source=seed`、完整符合設定 host 的目標。

停用組的專屬網站種子會暫停探測；若同一網站也在其他啟用組中，仍可探測。既有來源抓取與自然外連目標繼續運作。移除整組會移除它的優先／暫停規則，已存在的待探測紀錄仍保留，不等於封鎖網站；需要永久封鎖時使用原有探測目標管理。

儲存設定會將尚不存在的啟用網站種子加入佇列，和設定變更在同一筆交易完成。既有 host 的終態、拒絕、robots 封鎖與來源證據不會被重設。新增設定不代表已找到有效 RSS；已完成的目標不會因再次儲存而重新探測。種子 URL 會轉為網站 origin，深層路徑不限定探測頁面。

## 共用儲存與 API

設定存於 `driftread.app_settings`，以 `key` 對應型別化 JSON `value`，並有 `version` 與 `updated_at`。目前註冊 `discovery.control`（自動探索開關）、`discovery.profiles`（value 為 `{ "profiles": [...] }`）與 `source.review`（來源評審引擎、政策、門檻及可選人工覆核，見 [來源評審](SOURCE_REVIEW.md)）。兩張設定卡分別保存版本與草稿，衝突不丟棄尚未儲存的修改。加入新設定類型只需擴充 `SETTING_DEFINITIONS` 與對應表單，不必新增儲存表或 CRUD API。

| API | 用途 |
|---|---|
| `GET /api/admin/settings` | 讀取已註冊設定、預設值、版本與更新時間 |
| `PUT /api/admin/settings/{key}` | 送出 `{value, expected_version}` 儲存設定 |

兩者都需要 `X-API-Key`。表和 RPC 只授權 `service_role`，不提供瀏覽器直接存取。API 不列出任意未知 key，避免未來敏感設定意外曝光。Supabase key、連線字串與其他密鑰仍放部署環境變數，不放這張表。

更新使用版本比對；其他管理員先存檔時回 409，管理頁保留尚未儲存的修改，需重新載入最新版再編輯。直接 SQL 更新也會由 trigger 增加版本。設定損壞或資料庫失敗不會默默恢復預設並重新啟動已停用的探測。

最多 30 組、每組 30 個網址、合計 200 個網址；單一網址上限 2048 字元，名額 0–100。網址只允許 HTTP(S)、不含帳密；種子使用英數網域（國際化網域需先轉成 punycode）。停用組保留語法驗證，啟用組另做有並發與時間上限的 DNS／SSRF 檢查。探測時仍會重新套用 SSRF、robots 與原有禮貌限制。

## 升級

`20261008034852_global_settings.sql` 建立表與初始設定，不在 migration 中自動入列種子。第一次使用時可直接按「套用目前設定」，不必先修改預設方向，即可新增缺少的啟用網站；讀取頁面不會自行寫入。重複套用沿用版本檢查，保留既有目標狀態。既有啟用 discovery 的部署仍受主開關約束，設定不會覆蓋 `FEED_DISCOVERY_ENABLED`。

原先的 `FEED_DISCOVERY_CHINESE_SEED_QUOTA` 與 `FEED_DISCOVERY_CHINESE_SEED_HOSTS` 已移除；若有自訂清單，升級前記錄並在後台建立相應探測方向。其他部署環境變數維持既有管理方式。API migration 必須先完成，worker 才能啟動。
