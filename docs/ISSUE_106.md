# #106 來源管道調查

## 已確認的正式資料

2026-10-16 12:22 UTC，以 Supabase 唯讀 SQL 查核；未抓取正式來源、領取任務、重新入列、提交評審、呼叫 Jev 或修改正式資料。

Issue 建立時的 100 個零文章來源是較早的快照。調查期間 worker 仍持續運作，不能事後重建那 100 個來源當時的結果，也不能把兩個時間點的數字混用。

| 項目 | 本次結果 |
|---|---:|
| 來源總數 | 214 |
| normal 且未封存 | 177 |
| 零文章來源 | 40 |
| 文章精確總數／來源快取合計 | 30,329／30,329 |
| 逐來源 `article_count` 與 `articles.feed_id` 精確聚合不同 | 0 |
| 零文章且准入排除封存 | 26 |
| 零文章且其他封存 | 10 |
| 零文章、未封存且歷史原因空白 | 3 |
| 零文章、未封存且抓取失敗 | 1 |
| 各候選最新任務仍為 Jev `provider_key_missing` | 197 |

至少十個具體來源的資料庫分類如下；這是欄位證據，沒有假稱已重抓第三方網站。

| 來源 | 分類與證據 |
|---|---|
| Recurity Labs | 未封存、0 篇、`last_ok_at` 有值、沒有失敗原因；歷史空 feed／解析／過濾原因無法還原 |
| Stop! Think Fraud | 同上 |
| Jean-Marc Morandini | 同上 |
| Интерфакс новости | 未封存、0 篇、曾嘗試、尚無成功、連續失敗 1 次，但錯誤文字為空 |
| sankalp's blog 的 RSS 別名 | 准入排除：鏡像既有來源，已封存 |
| karpathy 的 RSS 別名 | 准入排除：鏡像既有來源，已封存 |
| Google Developers Blog 的 RSS 與 Atom 別名 | 兩筆皆因鏡像既有來源而封存 |
| Fortune 的 `/rss` | 准入排除：鏡像既有來源，已封存 |
| Macworld Comments Feed | 准入排除：留言來源，已封存 |
| WDTN.com Comments Feed | 准入排除：留言來源，已封存 |
| Ars Technica 的 `/rss` 與 `/feed/` | 兩筆已在 10 月 13 日封存；不能從空白原因推斷是本次抓取故障 |
| TIME 的 `/rss` 與 `/feed` | 同為較早封存的別名 |
| Recently published packages on pub.dev | 已在 10 月 13 日封存；0 篇不能直接視為目前排程故障 |

公開列表程式已排除封存來源；詳情與前端數字沿用 API 的 `article_count`。本次沒有發現計數快取錯誤，也沒有直接驗收正式網站／API：執行環境未開放該站網域。正式瀏覽器快取與部署版本仍需上線驗收，不能宣稱前端有 bug 或已修好正式畫面。

## 評審任務的判讀

歷史狀態統計不能當成互斥來源數。另一個查核時間點，歷史 stale 包含 external_agent 政策 v1 的 2,974 筆及 v4 的 2,737 筆（另有帶採樣失敗碼及其他引擎的 stale）；各候選最新任務當時仍有 1,753 筆 v4 stale。worker 已持續產生 v5 ready／requested，證明已有部分恢復流程，不能說全部永久卡住。

SQL 確認 `prune_source_reviews`／claim／prepare／submit 都可因政策變更使未完成任務 stale，快照到期也會 stale；歷史資料沒有單獨保存轉移原因，故不把全部 5,777 筆歸為同一原因。`awaiting_human`、已完成結果與人工覆寫保留既有語義。

普通 `request_source_review` 原本會直接沿用任何已存在的終態；因此 MCP 對 stale／舊 Jev 缺金鑰任務申請成功，仍可能拿到原任務而沒有重新排入。本次新增的恢復與回報契約見 [SOURCE_REVIEW.md](SOURCE_REVIEW.md#恢復與佇列診斷106)。抓取端新增診斷、重算與時間欄位語義見 [CRAWLER_OPERATIONS.md](CRAWLER_OPERATIONS.md#來源抓取診斷106)。

## 重現與後續驗收

使用 backend 的 Python 依賴及 PostgreSQL client，可對目前資料重跑唯讀分類：

```bash
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f scripts/audit_source_pipeline.sql
```

腳本開啟 repeatable-read、read-only 交易，列出精確統計、全部零文章來源及每個候選的最新任務分布；不輸出樣本、token 或使用者資料。程式測試覆蓋並行重新申請、政策升版、跨引擎恢復、人工優先、來源採樣、預設示範內容、空 feed／缺網址／批內重複／新增文章及寫入失敗。另以本機 PostgreSQL 17 串接候選→評審→准入→刷新→文章入庫→公開詳情；資料庫使用測試轉接器執行真實 SQL，第三方 HTTP 使用替身，正式 PostgREST／瀏覽器另行驗收。

部署後先對三個歷史原因不明的啟用來源逐一按「更新文章」，檢查最近結果、解析項目數、文章數與下次抓取時間，再比較公開列表／詳情 API 與瀏覽器。這會真正抓取第三方來源，未在本次正式資料查核中執行。保留 #106 追蹤正式驗收；本 PR 不批次刪除歷史別名、不解除封存、不重新購買 Jev 請求，也不建立自動通知管道。
