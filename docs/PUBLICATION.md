# 統一文章閱讀層

文章讀取經 `driftread.article_publications` projection；抓取與保存維護原始 `articles`，既有文章閱讀頁、feed 詳情預覽／文章分頁、閱讀流／未讀數／範圍標已讀、收藏及文章搜尋使用同一層。新消費出口透過 `backend/services/publications.py` 取得相同語義。此層不含 AI analysis 或模型覆寫。

## 來源可見性與正文政策

只有 `feeds.participation_mode = normal` 的文章可閱讀。`private` 與 `signal_only` 仍可被后台維護及抓取，但不出現在任何上述文章出口，已有訂閱或收藏不會繞過這項限制。Archived 的正常來源保留已知 ID、訂閱閱讀流與收藏的閱讀能力；公開搜尋排除 archived，沿用既有行為。

`feeds.fulltext_policy` 的 `rss` 沿用原有 RSS 快取正文；`summary_only` 立即令讀取層的 `content` 為 null、`fulltext_allowed` 為 false。原始正文繼續供抓取與保存流程使用。兩種政策均保留 feed 提供的 title／author；`summary_only` 的 summary 先移除 HTML 再限制最多 500 字元，避免完整 RSS description 繞過正文限制。摘要不從被禁止的 content 衍生。`rss` 只表示部署設定允許目前閱讀方式，不代表系統判定出版者授予任意再散布授權；其他出口仍應按自身產品政策限制內容，例如 RSS remix 使用摘要及原文連結。

搜尋相關度、命中條件及片段同樣依閱讀層許可的 title／summary（含摘要模式的裁切）／author／content 計算。原始 `articles.search_vector` 的 GIN 索引僅用於篩選候選，接著以 publication 向量重新檢查；禁止的正文既不能造成命中，也不能影響 rank 或片段。政策更新即時生效，無需重建索引。

View 使用 `security_invoker=true`，僅 `service_role` 具 SELECT；anon／authenticated 的原始 `articles` SELECT 已撤銷，防止 Data API 繞過正文或來源限制。內部 RPC 僅 backend 可執行；所有使用者資料 join 仍自行帶 `user_id`，因 backend 的 service_role 繞過 RLS。

## 共用服務契約

- `get_publication(db, article_id)` 回傳可閱讀文章字典或 None；無使用者訂閱限制，與公開閱讀頁一致。需限制為自己的訂閱時，出口須再檢查 authenticated user 的 `user_feeds`，包含 `user_id` 與靜音狀態。
- `list_personal_publications(db, user_id, start=None, end=None, exclude_backfill=False, limit=100)` 透過 RPC 只讀該使用者的正常、未靜音訂閱；時間範圍為 UTC `[start,end)`、可排除 backfill，`timeline_at,id` 遞減，最多 500 筆，來源名稱帶此使用者自己的 custom_title。
- 回傳文章欄位包含 `timeline_at`、`discovered_at`、`backfill`、`backfill_reason`、`current_revision_id`、`content_compacted_at`，另含 `feed_title`、`feed_language`、`feed_archived_at`、`fulltext_allowed`。Service 移除内部 `search_vector`。

## Cursor 契約

Feed 文章、閱讀流、已讀清單及兩種搜尋使用 v1 query-bound cursor，包含不透明 position 及結果集合 identity 的 SHA-256 fingerprint。Identity 包含 endpoint、authenticated user（有使用者狀態的出口）、feed、unread_only、搜尋原文與 language；頁面大小不改變集合 identity，可在下一頁調整。

跨 endpoint、帳號或篩選重用 cursor、舊版無 scope cursor、未知版本及超過 2048 字元的輸入一律回 400 `Invalid cursor`。呼叫端應從第一頁重新載入。Position 仍驗證 timestamp、UUID 與有限 rank，文章排序統一 `timeline_at,id`；已讀記錄按 `read_at,article_id`，feed 搜尋按 `rank,created_at,id`。

Fingerprint 用於結果集合相容性檢查，不是授權簽章。使用者隔離始終依請求認證及 RPC 的 user_id 執行，不能從 cursor 決定讀取哪個帳號。此變更不提供整個翻頁過程的資料庫 snapshot；既有 keyset 仍允許資料更新影響後續頁。

## 驗證

`backend/tests/sql/test_publication_read_layer.sql` 在隔離的 PostgreSQL 測試資料庫驗證來源限制、rights 即時切換、搜尋命中／片段、兩使用者狀態與收藏隔離、日期／backfill／custom_title、timeline keyset、mark-all 範圍及 grants。`test_publication_postgres.py` 另驗匿名角色不能讀 raw 或 internal view。`test_publication_cursors.py` 驗證跨 endpoint／user／filter 拒絕、舊 cursor 失效與 page size 可調整。

新 RPC 使用 `list_feed_publications`／`list_reading_publications`／`search_publications`／`list_bookmark_publications`，舊 RPC 保持原 RETURN contract 並轉接同一層，避免歷史 migration 的 CREATE OR REPLACE 與新增欄位衝突。新基礎 migrations 與 publication migration 可重跑；完整清空歷史 ledger 的既有 schema 移轉限制仍需保留 ledger，見部署文件。
