# 人工組織與個人熱度

這些功能不使用 embedding、LLM、付費模型或 AI 事件判斷。推薦與文章閱讀的其餘契約見 [FEATURES.md](FEATURES.md)。

## 人工 Fact／Story API

Fact 是操作者明確挑選的文章集合；Story 是操作者明確挑選的 Fact 集合。文章與原來的來源、已讀和收藏關係保留，各篇文章仍可單独閱讀。關係不會自動擴散，也不會根據分類、同文 URL 或人工 relation 自動加入 membership。

後臺端點沿用 `X-API-Key`。`event_objects`、`fact_articles`、`story_facts`、`event_relations` 開 RLS 且無公開 policy；只有 backend 的 `service_role` 能讀寫。RPC 皆為 `SECURITY INVOKER` 並只授權 `service_role`。公開 `/events/{id}` 固定使用可閱讀 projection，保留 archived normal 文章與集合 URL 的閱讀能力；私人來源、signal-only 文章及排除項目不會出現在結果，整個集合無可閱讀文章時回 404。

| 方法 | 端點 | 操作 |
|---|---|---|
| POST | `/api/admin/events` | `kind: fact\|story`、`title`，建立人工集合，回傳 id 與 version |
| GET | `/api/admin/events` | 可選 kind、limit 1–100，列出最近集合 |
| GET | `/api/admin/events/{id}` | 集合、直接 members、exclusions、人工 relations、文章及 version |
| PATCH | `/api/admin/events/{id}` | 必填 expected_version，可更新 title、members、relation_target+relation |
| POST | `/api/admin/stories/{id}/merge` | target_id、source_version、target_version，原子合併 Story |
| GET | `/api/events/{id}` | 可閱讀文章與原始連結；合併後 requested_id 保留、id 指向新 Story |

`members` 為至多 200 個 `{id, excluded}` 決定，Fact 的 id 是 article id，Story 的 id 是 fact id。PATCH 只覆寫明確提供的決定，省略的永久排除保留；要撤销排除，操作者必須明確傳 `excluded: false`。所有 membership 與 title／relation 更新在同一交易中，成功一次 version 加一；版本過期回 409，需重新 GET 後再决定，不能盲目重試。無效 FK／類型或版本衝突不會消耗 version。

relation taxonomy：`SAME_OCCURRENCE`、`SAME_STORY`、`UNRELATED`、`ROUNDUP`，以及保留的擴展 `SAME_EVENT`、`FOLLOW_UP`、`REACTION`、`CONTEXT`、`SAME_TOPIC`。這只是操作者聲明，不會觸發自動判斷或傳遞閉包。合併只允許未合併的 Story，依 id 順序鎖住兩列；遇到相同 Fact，任一方的排除保留。舊別名直接壓平到新 Story，拒絕自合併、再合併已被合併的 id 及循環。

合併會在同一交易把來源、目標與兩者既有別名的人工 relation 重掛到目標，並重新排列 endpoint id。同一對 endpoint 的相同聲明只保留一筆，合併後變成自關係的聲明移除；若同一對 endpoint 出現不同 relation，整筆合併回 400 `Invalid event change`，membership、排除、關係、別名與版本皆不改變。操作者須先用 PATCH 統一衝突的關係，再依重新取得的版本合併，不會默默選擇或覆蓋其中一個聲明。

帶 relation 的 PATCH 與 merge 在鎖住 event rows 前共用交易 advisory lock，避免並發寫入留下指向被合併來源的關係。PATCH 的 relation_target 會解析既有別名；解析後等同自己也拒絕。純 membership PATCH 不取得此共用鎖。這些交易只修改人工事件表，不會取得同步 ledger 的 `sync_clock` 鎖。

範例（id 由建立回應取得）：

```json
{"kind": "fact", "title": "人工確認的發佈事實"}
```

```json
{"expected_version": 1, "members": [{"id": "22222222-2222-2222-2222-222222222222", "excluded": false}]}
```

## 個人熱度 snapshot

`GET /api/me/personal-heat` 需要永久使用者登入。支援 `feed_id`、`unread_only`、`limit`（1–100，預設 100）。回傳與閱讀流相容的 `items`、`next_cursor: null`，並帶 `snapshot_at`、`complete`、`behind_participant_count`、`candidate_limit: 500`；每篇附 `why`、`heat`、`participant_count`、`group_key`。它是最近七天、至多 500 篇候選的有界 snapshot；不假裝能對動態熱度提供穩定的全歷史 cursor。

顯示集合只包含本人未靜音的 normal 訂閱文章。signal-only 設定源可對相同 URL 或人工 Story 提供數量／熱度證據，不公開其文章內容；已訂閱且明確靜音的 signal-only 源也不參與。private 與封存來源完全不參與。使用者的已讀狀態、dislike 與最近 14 天 skip 皆由 RPC 的顯式 user_id 條件處理，熱度不繞過這些排除。

人工 Story 的 membership 決定 group；一篇文章被多個未合併 Story 收錄時，以最小 Story id 作穩定選擇。沒有人工 Story 時使用文章 canonical URL（目前只移除 fragment）。URL 同文不是語意事件判斷；相同分類、標籤或 relation 本身不能聚合文章。結果中的同 group 只顯示排名最高的一篇，原文的閱讀／收藏 API 仍使用各自 article id。

獨立參與者以 source `signal_group`（鏡像群）為優先，否則 feed id。每個 group／participant 只取最新一次有效發佈時間，重複文章不能灌高熱度：

`heat = Σ 2 ^ (− max(0, snapshot_at − published_at) / 24 小時)`

七天之外、未提供 published_at、未來發佈時間與 backfill 都不形成 current heat。抓取／發現時間不提供熱度，因而晚抓到文章不會被當成剛發生。個人排名使用既有 explicit 偏好、訂閱、喜歡、不喜歡、跳過與收藏訊號，加上 `0.25 × heat / (1 + heat)` 的飽和項；再大的熱度仍不能蓋過有分數差距的明確個人訊號。

完整度對整個相關來源集合計算，包含沒有近期文章的來源與全域 configured signal-only sources。任何来源無 last_ok_at 或已超過其抓取間隔，就標 incomplete／behind；理由只表示資訊未完整，不解釋為熱度下降。當下查詢從現存明確證據重算，catch-up 後新的 snapshot 可恢復完整。

### 持久歷史與有界修復

現有 heat GET 透過 `capture_personal_heat` 原子保存 `user_heat_snapshots`，回傳可供查詢的 `snapshot_id`，每人最多保存 100 筆，查詢／修復窗口為 30 日；下次 capture 會清除本人的過期紀錄，沒有背景定時刪除。只存當時的候選 article id／group、完整來源 cohort 與健康觀測、每來源的 group／participant／source_time；不複製 title、summary、正文或使用者偏好。此表 RLS 開啟且沒有公開 policy，raw table／RPC 僅授權 service_role；永久登入者只能透過帶顯式 user_id 的 backend 端點存取自己的紀錄。

| 方法 | 端點 | 操作 |
|---|---|---|
| GET | `/api/me/personal-heat/history` | 本人紀錄時間與 id；limit 1–100，預設 20 |
| GET | `/api/me/personal-heat/history/{snapshot_id}` | 本人單筆 snapshot，limit 1–100；不存在、過期或非本人回 404 |
| POST | `/api/me/personal-heat/history/repair` | 重算本人最近至多 20 筆保存紀錄；limit 1–20，回 repaired_count |

repair 對原來 cohort、鏡像 participant 身份與候選 group 補進晚到的 source publication time 證據，按來源取最新有效 published_at，再以原 snapshot_at 衰減；抓取時間、未知／未來日期、backfill 不創造歷史熱度。修復結果寫回同一筆 snapshot 並記錄 repaired_at，重跑不重複計數。每人 capture／retention／repair 使用同一交易 advisory lock。來源健康觀測維持原值：例如原先 incomplete、後來抓取成功的來源，舊 snapshot 修復後仍標原來 incomplete，新的 snapshot 才反映現在健康。

歷史 detail 每次重新套用目前 `article_publications` projection、來源角色／封存、訂閱／靜音、已讀與 dislike／skip；目前變 private 或不再可參與的來源，其候選與熱度證據都會移除。signal-only 的 article id、來源身份／metadata 或內容不出現在 API，僅提供聚合熱度。rights 收緊會使用目前的 bounded summary，不會從歷史還原舊全文。原始 completeness 仍表示當時整個 cohort 的抓取完整度，並非目前投影後的 cohort 完整度。

此版本是有界證據修復，不提供歷史 trend UI、穩定分頁 cursor、背景排程、任意時間回填或偏好歷史。排名與排除使用目前個人設定，回應 `preference_basis: current` 明示此點；不把目前偏好宣稱為當時偏好。原候選與 cohort 不增加，未捕獲的舊 snapshot 不能憑空還原健康。晚到文章的人工 Story group 按修復時的明確 membership 解析，不能還原未保存的 membership 版本；已保存 source-time 證據不因文章清理而遺失，repair 只補入有效晚到證據，不是歷史撤稿修訂系統。

## 個人來源推薦

既有 category 70% 偏好／30% 探索配額和 language 加分保留。探索池在不同 category 或未分類來源中，優先挑 shared tag／language 的鄰近邊界；不足時仍使用既有回填規則。偏好理由分開 explicit preferences 與 subscriptions，不再將設定偏好描述為已訂閱。

最近 30 天至多 200 筆已讀紀錄提供弱正向：每個 category +0.25、每個 tag +0.1，去重後同篇候選的總 history 加分最多 0.5。大量自動已讀不能單靠數量蓋過喜歡／收藏。history 查詢顯式帶 user_id，且深層 feed join 只取未封存 normal 來源；靜音訂閱不作強正向訊號。沒有命中訊號的探索卡片明確顯示探索原因。

## 驗證

`test_heat_history_postgres.py`／`sql/test_heat_history.sql` 驗證持久 producer／consumer、晚到證據、凍結健康、owner 隔離、private／rights 撤權、retention／repair 上限與 expected relation names。`test_personal_heat.py` 提供固定時間的參與者、半衰期、個人偏好主導、lag/catch-up、同 Story 去重、future/unknown/old evidence fixture，並檢查 API scope／limits、歷史 API 認證、顯式 owner 參數與固定過去時間。`test_manual_events.py` 守住管理認證與 safe 400／409／public projection。`test_manual_events_postgres.py` 在臨時 PostgreSQL 資料庫驗證原子 membership、永久排除、合併／別名、角色隔離、無訂閱的 signal-only 貢獻、backfill 排除與兩個連線同時改版的一成功一衝突。`test_story_merge_relations_postgres.py`／`sql/test_story_merge_relations.sql` 驗證關係與別名重掛、相同聲明去重、自關係移除、衝突完整 rollback、migration 重跑，以及兩個交易依不同先後順序並發 PATCH／merge。
