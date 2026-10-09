# 文章版本、來源證據與歷史時間

此模型處理 #70、#71，沿用 [擷取與正文保存](CRAWLER_OPERATIONS.md) 的 hash、抽取 CAS 與正文縮減規則。migration `20261009000100_article_revisions.sql` 必須先由 API 套用，再啟動新版 worker；正式環境套用與部署仍待驗證。

## 身份與版本

`articles.id`、`UNIQUE(feed_id,url)` 與所有使用者關聯保持原樣。不同 feed 對同一 URL 的描述／HTML 仍有各自的文章列，互不覆寫。`canonical_url` 僅移除 URL fragment，以精確 URL 分組 provenance；不猜測同站其他路徑、追蹤 query、syndication 或語意相同文章，也不合併已收藏的舊 ID。

每次建立文章，或正文 hash、標題、摘要、作者、發布日期改變，資料庫 trigger 在同一 transaction 新增一筆私有 `article_revisions`，並更新 `revision_number` 與 `current_revision_id`。版本編號對單篇文章單調遞增。重抓相同內容、抽取進度更新、正文縮減均不建立假版本。即使 concurrent ingestion，唯一鍵和文章 row lock 仍保護版本序列。

歷史版本保存來源 hash 與當時中繼資料，**不另存永久 HTML**。歷史正文內容無法由 hash 還原，全文仍由 `articles.content` 與現有 retention 管理。metadata 更正不重設正文抽取／保存期；真正 content hash 改變才重設。已縮減的相同 hash 再被抓到也不會還原正文。Legacy migration 建立現存 snapshot 的第一版，不宣稱還原之前已遺失的歷史；來源 hash 之後完成初始化時會記錄新版本。

`article_revisions` 開 RLS，anon／authenticated 無權限，service_role 只有 SELECT／INSERT，且 UPDATE trigger 拒绝原地修改。刪除原文章／來源會透過 FK cascade 清除其歷史，因此 append-only 指存續文章的版本記錄，不是永久保存已刪除來源。

## Discovery provenance

私有 `article_discoveries` 保存 `article_id`、`feed_id`、`canonical_url`、入口 `origin`、來源 feed URL、首次／最近看見時間，以及最近觀測 hash。`(article_id,origin,source_url)` 冪等去重；相同入口重抓只更新最近時間與觀測 hash。兩個 feed 指向同一 URL 時，可依 canonical URL 找到兩個獨立來源；同一 feed 經 RSS 與登入使用者 discover/import 進入時，兩個入口都留下證據。文章寫入與證據保存同 transaction，失敗一起 rollback。

目前入口為 `rss`、`discover_import`、`historical_import`。沒有公開 provenance API，沒有新增任意 URL 抓取入口。`services.articles.upsert_articles()` 提供明確 ingestion context，未指定時沿用 RSS；不接受任意 origin 或超過 100 字元的 historical reason。

## 發布、發現與時間流

- `published_at`：來源聲稱的發布時間，保留原值供診斷。
- `discovered_at`：系統首次保存這筆 source-specific 文章的時間；往後 refresh 或 metadata 更新不改寫。
- `timeline_at`：存在且未晚於首次發現的發布時間；缺少或不合理的未來日期改用首次發現時間。日期更正時重算，同版本 refresh 不變。
- `backfill`／`backfill_reason`：首次保存時決定的歷史身份，後續 refresh 不會默默洗成 realtime。

新來源首次成功 fetch 的文章標記 `initial_fetch`；登入 discover/import 的同步文章標記 `discover_import`；明確歷史匯入可指定 reason。普通 refresh 首次看到發布超過七天的文章，標記 `old_publication`。既有資料採首次 `fetched_at` 回填 discovery，保守標記 `legacy_import`，不以部署時間製造新訊號。無日期的普通新文章以 discovery 排序。

歷史文章仍可閱讀、訂閱流顯示與搜尋。`backfill` 是[個人熱度](NON_AI_INTELLIGENCE.md)與[日報](CONSUMPTION_SURFACES.md)的排除依據，不把文章隱藏或刪除。目前沒有即時通知功能；來源 fetch／角色規則見[來源模型](SOURCE_MODEL.md)。

## 驗證

`backend/tests/test_article_revisions.py` 與 SQL fixture 使用實際 migration chain、隔離 PostgreSQL 17 資料庫，驗證版本、不同入口、不同 feed rendering、rollback、legacy baseline、migration 重跑、權限、相同版本縮減與並發 ingestion。Python ingestion 測試驗證 context 與輸入限制。測試只使用 localhost 的 `*_test` 入口，從不使用正式資料庫。
