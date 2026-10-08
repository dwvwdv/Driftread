# 來源角色與抓取完整性

來源角色是客觀 metadata，不是全站推薦 tier。所有舊來源預設維持可閱讀的 `normal`，沒有新增任何 AI 功能。

| 欄位 | 語義 |
|------|------|
| `first_party` | 管理者標記的一手資訊來源，預設 false；不直接改推薦評分 |
| `participation_mode` | `normal` 提供閱讀內容與訊號；`signal_only` 僅提供系統訊號；`private` 僅管理者可見 |
| `signal_group` | 同集團／mirror／轉載來源的共同參與者 key；去頭尾空白、小寫、空字串存 NULL；最多 100 字元 |
| `fulltext_policy` | `rss` 或 `summary_only`，預設 rss；具體文章 publication 行為見統一讀取層 |
| `last_fetch_at` | 最近一次已開始的抓取嘗試，外連前 durable 記錄；失敗不會冒稱成功 |
| `last_ok_at` | 最近一次成功取得且解析的回應或 HTTP 304，文章寫入失敗仍保留來源成功證據 |

舊 `last_fetched_at` 仍保留既有 API 相容性。migration 只從既有成功時間回填 `last_ok_at`，從成功／失敗最大時間回填 `last_fetch_at`，不以 migration 執行時間冒充成功。`record_source_fetch` 使用 `GREATEST`，較晚完成的舊嘗試不能讓成功時間倒退。

## 管理 API

管理 API 全部需要 `X-API-Key`：

- `GET /api/admin/feeds`：原有分頁管理目錄，包含所有角色與封存來源；公開 `/feeds` 不能代替此入口。
- `PATCH /api/admin/feeds/{id}/source`：局部更新 `first_party`、`participation_mode`、`signal_group`、`fulltext_policy`。未帶欄位保留原值，`signal_group: null` 明確清除群組；其他欄位拒絕 null。
- `GET /api/admin/feeds/source-health`：逐頁載入非封存來源，回傳 deterministic source completeness 與 lag。

既有管理 JSON 匯入亦可帶角色 metadata；公開 discover／OPML 匯入不能指定角色。公開匯入透過私有 RPC 插入新來源；URL 已存在時保留人工 metadata，隱藏來源回傳「不可用」，不訂閱、不曝光其 id。

## 可見性

公開來源目錄、來源詳情、分類／語言清單、來源搜尋、推薦候選與原因、個人訂閱／回饋列表、OPML 匯出都只讀取 `normal`。已訂閱的來源改成隱藏角色後不再曝光；使用者仍可取消訂閱／清除自己的回饋。新訂閱、更新訂閱與新增回饋須先檢查來源可閱讀性。

`feeds_public_read` RLS 同樣限制 normal，避免 anon／authenticated 直接 Data API 繞過後端 guard。後端仍使用 service_role，因此來源 RPC 與應用查詢均自己加角色條件。文章 consumption 的 role guard 由統一 publication 層處理。

目前沒有 private owner 模型；`private` 不代表「某個一般使用者的私人訂閱」。它是管理者保留的系統來源，不參與個人熱度。

## 完整性與參與者

`services/source_health.py` 提供三個介面：

- `participant_key(source)`：有 signal_group 時為 `group:<group>`，否則 `feed:<id>`。
- `source_health(source, now)`：回傳成功距今秒數、`lag_seconds = max(0, age - fetch_interval)` 與 behind 狀態。沒有成功證據視為 behind。
- `summarize_source_health(sources, now)`：排除 private／封存来源，回傳 complete、來源／獨立參與者數與 behind 數；空 cohort 不宣稱完整。

呼叫者必須傳入所選 cohort 的**全部**來源，包括沒有近期文章的來源。若只從有文章的來源計算，故障來源會消失，完整性就會被錯誤高估。同群組來源雖合為一位參與者，任一来源延遲仍使 cohort incomplete。`signal_only` 可提供訊號但不能作為閱讀代表來源。

## 驗證與部署

新增 migration `20261009000200_source_roles_health.sql`；先套 migration，再部署需要新增欄位／RPC 的 API、worker。沒有 Supabase Dashboard 設定變更。

unit tests 驗證 metadata validation、局部更新、來源可見性、參與者去重、完整性、失敗／304 與文章寫入失敗後保留抓取成功紀錄。獨立本地 PostgreSQL fixture 驗證 RLS、來源搜尋／推薦／分類／語言 RPC、私有 URL 匯入、時間戳單調性、migration 重跑。正式 Supabase／正式部署未驗證。
