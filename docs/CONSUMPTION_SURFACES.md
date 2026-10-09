# 個人閱讀出口（#78–#80）

所有出口需要永久使用者 Bearer token；backend 的 service-role 查詢明確使用認證得到的 `user_id`，不接受 caller 指定其他使用者。內容沿用 `article_publications`：只讀 normal 來源，排除 private、signal_only，正文遵循來源 fulltext policy。這些功能不呼叫 AI、產生摘要或發送郵件。

## 每日閱讀與個人 RSS

- `/me/digest` 顯示指定日期、IANA 時區的來源摘要；`GET /api/me/digest?date=YYYY-MM-DD&timezone=Asia/Taipei&limit=100` 使用當地午夜到次日午夜，包含 DST 的 23／25 小時日期。最多 100 筆，`truncated` 表示仍有資料。
- `GET /api/me/rss?limit=50` 匯出個人活躍訂閱的最近文章，最多 100 筆；只含來源摘要及原文連結。daily/RSS 排除 backfill，使用 publication timeline。
- RSS 需要 Authorization header；目前提供登入後下載檔案，未實作可放進一般 RSS reader 的永久 token URL、排程寄送或公開分享。
- 前端切換帳號、登出與元件銷毀會取消在途請求，防止舊帳號回應顯示或下載。

## 正式唯讀 MCP

使用官方 Python MCP SDK 的 Streamable HTTP：`/api/mcp/`，每個 request 都帶 `Authorization: Bearer <access_token>`。支援 initialize、notifications、tools/list、tools/call；stateless transport 不共用帳號 session。

| Tool | 行為 |
|------|------|
| `subscriptions` | 個人未靜音且 normal 的訂閱、個人名稱；limit 1–100 |
| `reading_stream` | 個人活躍訂閱最近文章摘要；limit 1–100 |
| `search` | 個人活躍訂閱的 publication 全文索引搜尋；query 1–200 字元、limit 1–100 |
| `article` | 個人活躍訂閱的單篇 publication；只在 rights 允許時附正文 |
| `digest` | 指定日與時區的來源摘要；limit 1–100 |

全部工具標註 readOnlyHint；沒有寫入、feed 抓取或 AI 工具。CLI SDK client 可無 Origin；瀏覽器 Origin 必須與 Host 相同。部署時 reverse proxy 必須限制可信 Host。

## 同步與離線快取的限制

`GET /api/me/sync?cursor=...&pending_ids=<uuid>` 使用帳號綁定的版本化游標；另一個使用者的游標與畸形游標回 400。游標不是權限憑證，每次仍須認證。最多 100 個 pending article IDs（extension 超過上限會要求先同步），回應會再次確認其訂閱／publication 權限。

這是「增量判斷是否有變更 + 有界快取快照」，不是任意長度閱讀歷史的 incremental delta。相關變更會回傳最近 100 篇的完整 replacement snapshot；client 整批替換快取。DELETE、取消／靜音訂閱、來源 private／signal_only 和 rights 更新都會失效舊快取；無相關變更時只推進 cursor，不重送 items。回應不包含正文或 search_vector。

`sync_clock` 全域單列鎖在交易內指派 sequence，snapshot 取得同一把鎖，避免跳過尚未提交的早期寫入；rollback 同時回滾 sequence 與 ledger。這會序列化相關寫入與 snapshot，是目前單一自架部署接受的成本。ledger 只記 table、operation、user_id 與時間，不留文章／使用者 payload；約保留 50,000 次 invalidation（每 1,000 次修剪，最多短暫多 999 筆），過期或超前 cursor 會 reset 至最近 100 篇。既有已授權 owner RLS 寫入由限定用途的 SECURITY DEFINER trigger 寫私有 ledger；固定 pg_catalog search_path，撤銷 PUBLIC／anon／authenticated EXECUTE。snapshot／search RPC 僅 service_role 可執行。

Extension 同步先拉權限 snapshot，剔除失權 pending operation，再用原帳號 connectionId 與 token 呼叫 `POST /api/me/sync/operations`（article_id、kind、enabled），最後拉回 authoritative snapshot。專用 service-only INVOKER RPC 同交易鎖定 normal 來源、本人未靜音訂閱與 publication 文章，再冪等寫入／刪除 read、favorite 或 read_later；user_id 只由認證取得。preflight 後撤權回 404，丟棄該操作並繼續更新。公開 reader 的原已讀／收藏 API 語義保留，不作為離線重放入口。

批次文章寫入與 sync ledger 的既有鎖順序可能與 replay 競跑；RPC 設 250ms lock timeout，捕捉鎖逾時／死鎖時由 exception subtransaction 回滾狀態與 ledger，回 503／Retry-After: 1。Client 保留 pending，下一次同步再送，不以 409 丟掉暫時繁忙的操作。網路失敗同樣保留 queue。已老化出最近 100 篇、仍有權限的 pending operation 可繼續重放。

本機快取只有摘要和狀態；有效期 24 小時，斷線、401／403、帳號切換時清除，token 只存 Chrome session storage，不跨 Chrome 帳號同步。沒有 server push；離線時無法即時察覺撤權，須重新連線同步或等待快取過期。沒有完整歷史下載、正文離線閱讀或無限期離線支援。

## 驗證

`backend/tests/test_sync_postgres.py` 使用獨立 PostgreSQL 測試資料庫驗證角色權限、owner RLS 寫入、交易 race／rollback、刪除與撤權、使用者搜尋隔離。`test_mcp.py` 使用官方 ClientSession 做 protocol interoperability；extension Node tests 覆蓋 account／options lifecycle、權限預檢、404 後重試及網路失敗。daily frontend tests 覆蓋登入、時區、空畫面、錯誤、帳號切換與 destroy。

`.github/workflows/extension.yml` 在 extension 變更的 PR／push 執行 Node 測試，不取代安裝 Chrome 的實際 UI 走查。
