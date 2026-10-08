# 擷取、中文來源與正文保存

這一版先處理 #64 的資料生命週期，以及 #63 中可獨立落地的來源品質與監控。AI 分類／摘要佇列、語意推薦與跨 worker 的原子佇列認領仍待後續工作。

## 部署順序

本版新增 `backend/migrations/20261008031540_crawler_lifecycle.sql`。由現有 migration runner 在 API 啟動時套用，worker 必須等 API healthy。升級前先備份資料庫，停止舊 worker，再更新 API 與 worker；不要混跑舊版文章寫入程式。新的寫入 RPC 是必要依賴，缺少 migration 時不會退回不安全的普通 upsert。

```bash
docker compose stop worker api
docker compose pull api worker frontend
docker compose up -d api
docker compose up -d worker frontend
```

遷移只新增欄位、索引、RPC 與私有監控表，不在部署時縮減任何正文。`ARTICLE_RETENTION_ENABLED=false` 是預設值。第一次啟用前先觀察資料量、確認備份可還原，再預覽一個小批次。

## 文章與 discovery 分開完成

正文與摘要的 SHA256 是來源版本。相同版本與相同中繼資料不重寫文章；已縮減的版本再次出現在 RSS 中，也不會寫回完整正文。來源正文／摘要有變化時，會重新保存內容並重設抽取狀態；只修改標題／作者／日期則保留抽取成果。

啟用 discovery 時，寫入前先在本地解析外連；文章成功保存後才寫入候選與來源關聯。只有全部可處理外連及 referrer 寫入成功，才記錄該版本已抽取。零外連文件也可完成。失敗、佇列容量不足或新 host 預算不足會留下待處理狀態，舊資料以最早尚未抽取的版本優先補齊，不再只看最新文章。

單篇解析仍有 **512 KiB UTF-8 與 500 個 anchor** 上限。超限文件可保存已看到的線索，但不宣告完整抽取，也不允許縮減正文；延後 24 小時重試，讓其他文章繼續前進。這些文件需要後續更完整的分段抽取，並非已完成覆蓋。

frontier 預算在同一個共享索引內扣除。現有 Compose 使用單一 worker；多個並行 worker 或手動觸發使用不同索引時，總容量仍是軟上限。

## 中文內容

使用既有管理匯入功能：

- `backend/seeds/chinese_feeds.json` → `POST /api/admin/feeds`，收錄 TechNews、INSIDE、泛科學、報導者、Openbook，明確指定 `language=zh`、category 與 tags。
- `backend/seeds/chinese_targets.json` → `POST /api/admin/discovery/targets`，補入環境資訊中心、故事、經理人、愛料理的網站種子。網站種子仍需探測與審核，不代表已確認有效 RSS。

兩個端點皆使用 `X-API-Key`；檔案不會在啟動時自動匯入。來源匯入以 URL upsert，相同 URL 會更新提供的中繼資料，不另建 feed。

`FEED_DISCOVERY_CHINESE_SEED_QUOTA=4` 在每輪探測中保留最多 4 個「已到期、pending、來源為 seed、host 在設定清單內」的名額，上限是該輪預算的一半，其餘名額維持原排序；設 0 可停用保留。`FEED_DISCOVERY_CHINESE_SEED_HOSTS` 可調整 host 清單。這是中文種子優先權，不是文章語言強制過濾或分類流量配額。各類內容均衡仍應依管理介面的 category／language 與實際文章供給調整種子。

候選會排除明確的留言 RSS，並合併慣用 `/feed`、`/rss`、`/atom` 尾端斜線別名；不會把同一 host 的所有 feed 合成一個。hold／reject 也會約束別名，保留審核決策。

## 預覽與縮減正文

`GET /api/admin/retention/stats` 回傳目前文章、完整正文、僅摘要、待抽取、已縮減數量，以及文章表、索引、合計與整個資料庫的 bytes。它是快照，沒有歷史成長率。管理首頁顯示相同資料。

預覽預設為 `dry_run=true`：

```text
POST /api/admin/retention/run?retention_days=30&limit=200
X-API-Key: <admin key>
```

回傳 `dry_run`、`eligible`、`compacted`、`content_bytes`。只有明確使用 `dry_run=false` 才修改正文；手動執行不受排程開關限制。`content_bytes` 是原始正文 bytes，並非磁碟回收量；PostgreSQL 仍需正常 vacuum，檔案也不一定立刻縮小。

條件同時成立才縮減：抓取超過保存天數、完整抽取過目前版本、正文尚未縮減、沒有任何使用者收藏或已讀關聯、來源沒有任何使用者訂閱。資料庫鎖定後再檢查，保護已先提交的使用者互動。縮減僅將 `content` 設為 NULL，保留 article ID、URL、標題、摘要與其他欄位；不刪除文章或使用者關聯。搜尋 generated vector 隨正文同步更新，已移除的正文詞彙不再提供命中。閱讀頁顯示保存期限提示與原文連結。

套用後無法由這些欄位自行還原原始正文；需要備份或原站重新取回。未來新增的收藏不會還原正文。已有訂閱的來源全部受保護，因此本策略不能保證資料量停止增長。也不要回退到會無條件 upsert 正文的舊版 worker。

自動排程需要明確啟用：

| 變數 | 預設 | 範圍／用途 |
|---|---|---|
| `ARTICLE_RETENTION_ENABLED` | `false` | 明確 `true/1/yes/on` 才啟用 |
| `ARTICLE_RETENTION_DAYS` | `30` | 7–3650 天 |
| `ARTICLE_RETENTION_BATCH_SIZE` | `200` | 每輪 1–1000 篇 |
| `ARTICLE_RETENTION_INTERVAL_MINUTES` | `1440` | 最多 10080 分鐘；每次只做一批 |

## Worker 監控

`GET /api/admin/operations?limit=20`（需 `X-API-Key`）提供 worker heartbeat、最近迴圈與最近失敗數。refresh、discovery、retention 分別記錄，每 30 秒更新 heartbeat；超過 90 秒視為 stale。正在執行但 heartbeat 過期的 run 在讀取時呈現 interrupted；尚無紀錄呈現未知。這是近期執行視窗，不是每日累積報表。

監控寫入失敗會記錄 log，不停掉擷取。私有表不開放 anon／authenticated。run 與 heartbeat 保留 30 天，每小時最多清除 1000 筆過期紀錄。狀態查詢最多回傳 100 個 run、200 個 worker。主要文章寫入／抽取移到 thread，但其他短同步 DB 操作仍可能短暫延遲 event loop，heartbeat 並非獨立程序的存活探針。

## 驗證

後端使用 `pytest`，前端使用 `npm test -- --watch=false` 與 `npm run build`。`backend/tests/sql/test_crawler_lifecycle.sql` 是額外的 PostgreSQL 整合 fixture，必須在空的隔離資料庫套用遷移，並建立一個 `auth.users` 測試使用者後執行；不得在正式資料庫執行。它檢查正文縮減、搜尋同步、使用者保護、重跑、版本重設、legacy hash CAS、權限與監控清理上限。交易最後 rollback。
