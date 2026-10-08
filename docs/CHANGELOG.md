# 變更紀錄（PR 逐筆）

本檔以 PR 為單位記錄 Driftread 的變更，**依合併時間由舊到新排序，新條目一律加在檔案最下方**。
「當前狀態」請看 [FEATURES.md](FEATURES.md)；安全加固細節看 [SECURITY.md](SECURITY.md)。

> 註：`master` 是本專案的預設分支（CI 同時接受 `main` / `master`）。

> **本檔只保留最近兩個十位段的階段（目前為階段二十一起）**；更早的階段依十位段原文歸檔在 [changelog-archive/](changelog-archive/)。
> 跨入新的十位段時（例如寫下第一個「階段四十一」），把最舊的那一段（屆時為二十一～三十）整段搬到
> `changelog-archive/stages-NN-NN.md` 並更新下方索引——**只搬不改**，歸檔內容就是當時的紀錄（相對連結補上 `../`）。
> 彙整某段期間的變更時若跨過本檔最舊的階段，要連同對應的歸檔檔一起讀。
> 撰寫規則（一個 PR 一個條目、review 修復併入同一條目）見 [AGENTS.md](../AGENTS.md) 的「Changelog 維護」。

## 歸檔索引

- [階段十一～二十（PR #25–#34 與 schema 隔離、runtime config，2026-07-30 ~ 08-17）](changelog-archive/stages-11-20.md)
- [階段一～十（PR #1–#24，2026-05-07 ~ 07-30）](changelog-archive/stages-01-10.md)

---

## 階段二十一：訂閱 CTA、單一訂閱狀態與 frontend CI 補跑單元測試（2026-08-18）

TODO.md 建議開發批次第 3 批。此前訂閱只能在「我的訂閱」頁面單向取消，feed 詳情、目錄卡片、
Discover 與「猜你喜歡」都沒有訂閱入口，也沒有任何地方能知道「這個 feed 我訂閱了沒」——每個頁面
各自推導，彼此不同步。

- **`frontend/src/app/services/subscription.ts`（新）**：`SubscriptionService`，單一訂閱狀態
  快取，登入後（依 user id keyed，同 `MyFeeds` 既有的 `loadedFor` pattern）載入一次，`isSubscribed()`
  供任何頁面查詢。`subscribe()` / `unsubscribe()` 樂觀更新本地狀態、失敗回滾、pending 期間忽略
  重複呼叫；`sync()` 讓已經自己抓過 `Feed[]` 的頁面（`MyFeeds`）直接回填快取，不必再多打一次
  `GET /me/feeds`；`markSubscribed()` / `markUnsubscribed()` 給後端已經順帶完成訂閱異動的情況
  （見下方 Discover 匯入）記錄狀態，不必再補一次多餘的 API 呼叫。
  - 有個真實的 race：重新登入的同一個 tick 裡，session 變化同時觸發這個 service 自己的
    reload effect，也可能觸發呼叫端自己的 `subscribe()`（例如登入後代下的訂閱）。若 reload 的
    伺服器快照剛好是那筆寫入 commit 之前抓到的，`_ids.set(new Set(serverIds))` 直接整組覆蓋
    就會把還在 in-flight 的樂觀新增蓋掉。`load()`/`sync()` 現在對 `_pending` 中的 id 保留本地
    樂觀值，其餘才信任伺服器快照。`subscription.spec.ts` 有這個情境的回歸測試。
- **訂閱入口**：
  - `components/feed-detail`：header 加「訂閱／已訂閱」按鈕。
  - `components/feed-list`：每張目錄卡片加快速訂閱；按鈕/已訂閱 chip 疊在卡片整體可點擊區
    （標題 `::after` 撐開的 hit area）之上（`position: relative; z-index: 1`），否則會被蓋住點不到。
  - `components/discover`：已收錄（`already_exists`）的候選除了「前往查看」，登入使用者可直接訂閱；
    新匯入（`POST /discover/import`）本來就會讓後端順便訂閱登入中的使用者
    （`backend/routers/discover.py`），前端呼叫 `markSubscribed()` 同步快取，不重複打
    `POST /me/feeds/{id}`。
  - `components/recommendations`（猜你喜歡）：卡片動作由「喜歡／跳過」兩個，拆成「喜歡／跳過／
    訂閱」三個獨立語意。訂閱同時仍記一筆本地「喜歡」信號——`user_feed_feedback` 之類的獨立
    `subscribed` 訊號與持久化是 TODO.md 之後「回饋持久化」批次的範圍，這批只先把 UI 動作分開。
  - `components/my-feeds`：取消訂閱／重新整理清單時透過 `subs.markUnsubscribed()` /
    `subs.sync()` 回寫共用快取，讓其他頁面不必整頁重新整理就會同步。
- **未登入時的訂閱**：以上入口在未登入時都導去 `/login?redirect=<原路徑>&subscribeFeed=<feed id>`，
  而不是把點擊吃掉或丟回首頁。`components/login` 的 `Login.submit()` 登入成功後讀這兩個 query
  param，代呼叫一次 `subscribe()` 再導回 `redirect`（沒有則回首頁），簽出時不會誤觸發。
- **frontend CI**：`.github/workflows/frontend.yml` 的 Build job 在 `npm run build` 前加
  `npm test`。此前 CI 只跑 production build，這個 repo 既有／新增的所有 `*.spec.ts` 從未被 CI
  執行過（`ng build` 用的 `tsconfig.app.json` 也刻意排除 `*.spec.ts`，型別錯誤都抓不到）。
  `@angular/build:unit-test`（Vitest + jsdom，非瀏覽器）在 GitHub Actions 會自動偵測
  `CI=true` 以 non-watch 模式單次執行，不需要額外安裝瀏覽器或加 `--no-watch`。
- **測試**：新增 `subscription.spec.ts`、`feed-detail.spec.ts`、`feed-list.spec.ts`、
  `discover.spec.ts`、`recommendations.spec.ts`、`login.spec.ts`。全部沿用既有測試慣例——純
  物件 fake + 手動記錄呼叫（本專案的 Vitest 設定裡沒有任何 spec 用 `jasmine.*`／`vi.*` mock
  API，一律手寫 fake），`Router.navigate`/`navigateByUrl` 用真的 `provideRouter([])` 換掉方法
  本體記錄呼叫參數，`SubscriptionService` 自己的 effect 測試用 `TestBed.flushEffects()`
  （Angular 17 起的正式 API，用在 `TestBed.inject()` 直接建立、不經過 `ComponentFixture` 的場合）。
  本 sandbox 的 npm registry allowlist 依然卡在同一批依賴鏈（`zod-to-json-schema` 等）上，
  `npm ci` 全部失敗，無法在本機跑 `ng build` / `ng test` 驗證——與階段二十的已知限制相同，
  交給 CI 實際跑過；已用人工重讀全部改動檔案一遍。
- 對應文件更新：`docs/FEATURES.md` 第 1 節、第 7 節（新增 Frontend 測試列）、`TODO.md`
  （批次 3 打勾，勾掉 frontend CI 補跑單元測試那條）。

## 階段二十二：手動 refresh response contract、bookmarks 複合 index（2026-08-19）

- **`POST /admin/feeds/{feed_id}/refresh` 補上型別化 response model**：原本
  `response_model=dict`，回傳的 dict 完全沒有欄位驗證與 OpenAPI schema。新增
  `models.py::FeedRefreshResult`（`inserted` / `feed_id` / `status` / `new_articles` /
  `total_articles`），沿用既有欄位名稱與語意——`inserted` 這個名字本身就是既有外部合約（瀏覽器
  擴充與外部腳本會讀），沒有改名。`status` 收斂成 `Literal["updated", "not_modified", "failed"]`，
  對齊 `services/feed_refresh.py::Status` 本來就有的型別。既有測試
  `test_refresh_feed_success_keeps_inserted_key` 不需要改動斷言就能通過，額外補了一條
  `status` 欄位的斷言。這條路由原本的測試就已經用 `patch()` 蓋掉
  `fetch_and_parse_conditional`，沒有打過真實網路，TODO.md 那條「測試不得依賴真實 DNS」其實
  早就成立，這次一併打勾。
- **`user_bookmarks` 補複合 index**：migration 013 新增
  `user_bookmarks_user_type_created_idx (user_id, bookmark_type, created_at DESC)`。
  `GET /me/bookmarks` 的查詢型態是 `.eq(user_id).eq(bookmark_type).order(created_at desc)`，
  既有的 `user_bookmarks_user_type_idx (user_id, bookmark_type)` 只覆蓋兩個等值篩選，
  `ORDER BY` 仍要另外排序；新 index 讓整條查詢一次索引掃描就能滿足，做法比照 migration 012
  幫 `user_article_reads` 補 keyset index 的先例——保留舊 index，只新增，不做風險較高的欄位替換。
- **`TODO.md` 補打勾**：盤點「技術與可靠性優化」整節時發現 `GET /me/bookmarks` 只回傳
  `ArticleSummary`（PR #37 就做了）、`GET /categories` 已經是 SQL 端 `DISTINCT` RPC
  （`driftread.list_feed_categories()`，migration 011）都是先前漏勾，一併補上；
  `user_feeds` / `user_article_reads` 的複合 index 現況也一併記錄——`user_feeds` 查詢只有
  單一等值篩選，PK 前導欄位已經夠用，不需要額外 index。
- 本 sandbox 的 pip index allowlist 卡在同一類限制（`pip install -r requirements.txt` 連
  `pytest` 都裝不出來），無法在本機跑 `pytest`，交給 CI 的 `backend.yml` 實際跑過；已對兩處改
  動（Pydantic model 型別、純 additive 的 index migration）做語法檢查與人工重讀。
- 對應文件更新：`docs/FEATURES.md` 第 5 節（索引清單補 012／013 兩條，先前也漏了 012）、
  `TODO.md`（本節五個項目打勾／補說明）。

## 階段二十三：部署／回滾 runbook，GHCR image 補 commit sha tag（2026-08-24）

- **新增 `docs/RUNBOOK.md`**：對照 `TODO.md`「補上升級與回滾 runbook，特別記錄 schema
  exposure、grant、RLS 與 runtime config 的部署順序」這條，寫一份給實際操作
  `docker-compose.yml` 的人看的操作手冊——一般部署四步、會動到 Supabase Dashboard
  Exposed Schemas／grant 的部署要先後順序（Dashboard 手動步驟必須先於帶新 migration 的
  `api` image 部署，理由是反過來的話 API 對新 schema／表的請求會直接壞掉而非優雅降級）、
  migration 010 保留的 `public._migrations` 相容 view 何時能安全移除、以及環境變數檢查
  指到 `CLAUDE.md` 既有的三處同步規則。
- **意外發現並修掉的缺口**：寫回滾章節時發現 `.github/workflows/{backend,frontend}.yml`
  的 `docker/build-push-action` 只打 `:latest` 一個 tag——這代表「回滾」在此之前根本沒有
  對應的 image 可指，只能等一次新的、修好的部署把 `:latest` 蓋掉。兩個 workflow 都加上
  `sha-${{ github.sha }}` 第二個 tag（`docker/build-push-action` 的 `tags:` 本來就支援多行
  多個 tag），永久保留、不會被覆寫，回滾 runbook 因此有真的可以操作的步驟：改
  `docker-compose.yml` 三個 `image:` 欄位指到 `:sha-<sha>`。
- **TODO.md 盤點**：連帶重讀「Migration 與部署」整節時發現兩條已經做了但沒打勾——
  migration runner 的 PostgreSQL advisory lock（`migrate.py::acquire_migration_lock`，
  `run_backfills()` 也共用同一把）、以及 migration／backfill 的可追蹤可重試狀態（兩者都記在
  `driftread._migrations`，成功才 commit，重跑會跳過已套用項目）——都補上勾。
- 本 sandbox 沒有網路能跑 `actions/lint` 之類的工具驗證 workflow YAML，用系統已有的
  PyYAML（`python3 -c "import yaml; yaml.safe_load(...)"`）對兩個改動過的 workflow 檔案
  各跑一次 `safe_load` 確認語法正確；多行 `tags:` 寫法本身沿用
  `docker/build-push-action@v6` 官方文件既有的用法。
- 對應文件更新：`docs/FEATURES.md` 第 7 節（部署列補 sha tag 與 RUNBOOK 連結）、
  `TODO.md`（「Migration 與部署」四項打勾／補說明）。

## 階段二十四：偏好設定 UI（2026-08-24）

TODO.md「P1：偏好、推薦與內容探索」的「建立偏好設定 UI，接上既有 `getPreferences()`／
`updatePreferences()`」——後端與 frontend service 早已存在（`routers/me.py` 的
`GET`/`PUT /me/preferences`、`services/me.ts` 的 `getPreferences()`/`updatePreferences()`），
只是沒有頁面可以呼叫它們。

- **`backend/migrations/014_feed_languages_rpc.sql`**：新增 `driftread.list_feed_languages()`，
  仿照 migration 011 的 `list_feed_categories()`——db-side `DISTINCT`、`REVOKE ALL FROM PUBLIC,
  anon, authenticated`，只有 service_role 能 `EXECUTE`。`routers/feeds.py` 新增
  `GET /feeds/languages`，回傳型別與既有的 `GET /feeds/categories` 一致（`list[str]`）。
- **`frontend/src/app/components/preferences`**（新元件，`/me/preferences`）：分類與語言各自
  以 `ob-chip` 呈現成可複選的 toggle 清單，選項來自 `GET /feeds/categories` /
  `GET /feeds/languages`（實際目錄的詞彙，不是寫死的清單），已選狀態載入自
  `GET /me/preferences`，按「儲存偏好」呼叫 `PUT /me/preferences`。沿用
  `bookmarks`/`my-feeds` 既有的「依 `auth.session()` 的 user id 判斷是否已載入」`effect()`
  寫法，避免 `AuthService` 還原 session 前就用空清單渲染。導覽列帳號選單與行動版抽屜都加上
  「偏好設定」連結，排在「收藏」之後。
- **不做的部分**：受控 category/tag vocabulary（同義詞、大小寫、多語標籤正規化）與推薦理由顯示
  是 TODO.md 同一節底下的獨立項目，留給各自的後續 PR；這批只接上既有的兩個欄位。
- **測試**：`backend/tests/test_feeds.py` 新增 `test_list_languages_uses_db_side_dedup`，比照
  既有的 `test_list_categories_uses_db_side_dedup`。`frontend/.../preferences.spec.ts` 覆蓋
  選項與已選狀態載入、toggle 的 immutable 更新、儲存成功/失敗的 toast 與 `saving()` 狀態。
- **本 sandbox 的已知限制**：`npm ci` 仍卡在 `zod-to-json-schema` 那條依賴鏈（403），
  `pip install pytest` 也被 PyPI allowlist 擋下，backend／frontend 測試都無法在本機實際執行，
  與階段二十一、二十二遇到的限制相同；已用 `python3 -m py_compile` 過 backend 改動、系統 `tsc`
  （`--ignoreConfig --noResolve`，僅語法檢查）過 frontend 改動，交給 CI 實際跑過驗證。
- 對應文件更新：`docs/FEATURES.md`（API 端點、DB function、前端路由）、`TODO.md`
  （「建立偏好設定 UI」打勾）。

## 階段二十五：Feed 目錄的語言篩選與可點擊標籤（2026-08-25）

TODO.md「P1：標籤、語言與偏好設定」剩下的兩項——「Feed tag 改為可點擊篩選」與「Feed 目錄加入
language、category、tag 的組合篩選」。`category`／`tag` 篩選、偏好設定 UI 的分類/語言 chip 都已
存在，這批把兩者接起來：目錄頁補上語言篩選，卡片上的標籤本身也能點。

- **`backend/routers/feeds.py`**：`GET /feeds` 新增 `language` 查詢參數，`query.eq("language",
  language)`，與既有 `category`／`tag` 篩選同一種 `AND` 疊加寫法。`feeds` 表本來就有
  `language` 欄位（`Feed` model 早已有），不需要新 migration；語言選項清單沿用階段二十四剛加的
  `GET /feeds/languages`。
- **`frontend/src/app/services/feed.ts`**：`getFeeds()` 簽名插入 `language` 參數（`page,
  pageSize, category, language, tag, search`），唯一呼叫端 `feed-list.ts` 一併更新。
- **`frontend/src/app/components/feed-list`**：
  - 篩選列加一個語言 `<select>`，選項來自新增的 `loadLanguages()`（`getLanguages()`，失敗時
    降級成「全部語言」而不擋頁面，與 `loadCategories()` 同一套容錯）。
  - 卡片上的標籤從純文字 `<li class="ob-chip">` 改成 `<button class="ob-chip">`，點擊即以該
    標籤篩選、再點一次清除（`filterByTag()`）——與偏好設定 UI 的 toggle chip 同一套寫法。目前
    篩選中的標籤會反白（`ob-chip--success`），篩選列上方另外顯示一個可點擊清除的「標籤篩選：
    ⟨tag⟩」提示。
  - 標籤按鈕疊在卡片標題連結的 stretched `::after` 之上（`position: relative; z-index: 1`），
    沿用 `.subscribe-btn`／`.subscribe-chip` 已有的做法，點擊標籤不會被卡片本身的導覽連結吃掉。
  - `hasFilters`／`clearFilters()` 一併涵蓋 `language`／`tag`。
- **測試**：`backend/tests/test_feeds.py` 新增 `test_list_feeds_filters_by_language`。
  `frontend/.../feed-list.spec.ts` 新增一個 describe block：分類/語言/標籤三者一起送進
  `getFeeds()`、點同一個標籤兩次會清除（toggle）、`hasFilters`／`clearFilters()` 涵蓋新欄位。
- **不做的部分**：受控 category/tag vocabulary（同義詞、大小寫、多語標籤正規化）仍是 TODO.md
  同一節底下的獨立項目；feed-detail／discover 頁面上的標籤目前維持純文字展示，沒有一併改成連回
  目錄頁篩選的連結（`feed-list` 本身也還沒有 query-param 同步，留給需要深連結時的後續 PR）。
- **本 sandbox 的已知限制**：`npm ci` 仍卡在 `zod-to-json-schema` 依賴鏈（403），
  `pip install pytest` 被 PyPI allowlist 擋下，backend／frontend 測試都無法在本機實際執行，與
  階段二十一至二十四相同；已用 `python3 -m py_compile` 過 backend 改動、系統 `tsc`
  （`--ignoreConfig --noResolve`，僅語法檢查）過 frontend 改動，交給 CI 實際跑過驗證。
- 對應文件更新：`docs/FEATURES.md`（`GET /feeds` 參數說明、信息源瀏覽功能列）、`TODO.md`
  （兩項打勾，「建議開發批次」第 5 項改為進行中）。
## 階段二十六：我的閱讀流、未讀數與已讀管理（2026-08-19）

TODO.md 建議開發批次第 4 批。此前「已讀」只有 `POST /me/articles/{id}/read`（單篇標記、無法
復原）與 `GET /me/reads`（回原始 read receipt id 列表，cursor 分頁但前端從未使用），沒有任何一個
地方能一次看到「所有已訂閱來源的新文章」——`/me/feeds` 只列訂閱本身，要讀新文章得逐一點進每個
feed 詳情頁翻最新 10 篇。也沒有未讀數，也沒有批次已讀。

- **`user_article_reads` 不新增表**：一列存在即代表「已讀」，這批只加查詢端的 DB function 與
  index，讀寫路徑仍是同一張 002 建的表——`DELETE` 該列就是「標為未讀」。
- **`backend/migrations/015_reading_stream.sql`（新）**：三個 `driftread` schema 內的 DB
  function，EXECUTE 只授權 `service_role`（同 `sample_feed_candidates` / `list_feed_categories`
  的鎖法）：
  - `list_reading_stream(...)`：跨 `user_feeds` 聚合每個已訂閱來源的 `articles`，LEFT JOIN
    `user_article_reads` 帶出 `is_read` / `read_at`，keyset 分頁。排序鍵是
    `COALESCE(published_at, fetched_at) DESC, id DESC`——未解析出發佈日期的文章退回抓取時間，
    避免落進 Postgres `DESC` 預設的 `NULLS FIRST` 卡在最前面，也讓 cursor 比較不必特別處理
    NULL。
  - `reading_stream_unread_counts(...)`：每個已訂閱來源的未讀數，LEFT JOIN 而非 anti-join，
    讀完的來源仍會列出、只是 0，前端拿來畫「各來源未讀數」的篩選下拉。
  - `mark_reading_stream_read(...)`：伺服器端一次 `INSERT ... SELECT ... ON CONFLICT DO
    NOTHING`，供「明確範圍」全部已讀用（單一來源／整個閱讀流），不必先把符合的 article id
    全部撈回 Python 再逐筆 upsert。
  - 新增 `articles(feed_id, fetched_at DESC)` 索引；`user_feeds` 與 `user_article_reads` 既有的
    複合主鍵已經覆蓋這批查詢在這兩張表上的存取模式，不必再加。
- **`backend/routers/me.py`**：
  - `DELETE /me/articles/{id}/read`——`mark_read` 的反向操作，標為未讀。
  - `POST /me/reads/mark-all`——帶 `article_ids` 就精準標那幾篇（目前頁面）；不帶則用
    `feed_id` / `before` 走上面的 RPC（明確範圍，三者都空即整個閱讀流全部已讀）。回傳
    `{marked}`。
  - `GET /me/stream`——聚合文章流，`cursor` / `limit`（上限 100）/ `feed_id` / `unread_only`。
  - `GET /me/stream/unread-counts`——總未讀數與各來源未讀數。
  - `utils.py` 新增 `encode_keyset_cursor` / `decode_keyset_cursor`，把 `GET /me/reads` 內
    原本寫死在 router 裡的 cursor 編解碼抽出來給 `/me/stream` 共用，行為不變。
- **`frontend/src/app/services/reading-stream.ts`（新）**：`ReadingStreamService`，
  `providedIn: 'root'` 單一快取，同時餵給導覽列帳號選單的未讀數 badge 與閱讀流頁面本身
  （同 `SubscriptionService` 讓多個元件共用一份狀態的角色，見階段二十一）。單篇標記已讀／未讀
  樂觀更新＋失敗回滾＋pending 期間忽略重複呼叫；「本頁全部已讀」送目前頁面未讀文章的 id 清單；
  「明確範圍全部已讀」（可選單一來源）不做本地樂觀更新——範圍可能涵蓋這頁從未載入過的文章，
  成功後改用伺服器回應重新整理未讀數，並把畫面上已載入、落在範圍內的列直接標成已讀。
- **`frontend/src/app/components/reading-stream`（新，`/me/stream`）**：主要閱讀入口。cursor
  「載入更多」（不一次載入全部）、總未讀 / 本頁未讀（`ObStat`）、「只看未讀」（server-side
  filter）與「隱藏已讀」（client-side 篩選，兩者刻意分開——前者改變 `GET /me/stream` 抓什麼，
  後者只影響已抓到的資料怎麼顯示）、來源篩選下拉（選項即 `reading_stream_unread_counts` 回傳的
  已訂閱來源清單，不必另外呼叫 `GET /me/feeds`）、逐篇已讀／未讀切換、兩種全部已讀動作
  （明確範圍那個帶 `ConfirmService` 確認對話框，訊息依是否有作用中的來源篩選而不同）。
  導覽列（`layouts/public-layout`）帳號選單第一個項目換成「我的閱讀」（帶未讀數 chip），原本
  排最前的「我的訂閱」讓出主要閱讀入口的角色，往後移一位，並在自己的頁首加一顆「前往我的閱讀」
  按鈕；`/me/feeds` 仍是唯一的來源管理入口（訂閱清單、OPML 匯入匯出），沒有拿掉任何既有功能。
- **測試**：新增 `test_me.py` 的 mark-unread／mark-all／stream／unread-counts 案例（沿用既有
  `mock_db` + `dependency_overrides` 慣例）；`reading-stream.spec.ts`（service，`TestBed.inject`
  + `TestBed.flushEffects()`，同 `subscription.spec.ts` 的模式）與
  `components/reading-stream/reading-stream.spec.ts`（元件，`TestBed.inject(ReadingStreamService)`
  拿與元件共用的同一個 root instance 斷言狀態，而不是碰元件自己 `protected` 的欄位——同
  `feed-detail.spec.ts` 對 `SubscriptionService` 的作法）。本 sandbox 對 PyPI 與 npm registry
  的存取都被 allowlist 擋下（`pip install pytest` 403、`npm ci` 卡在
  `zod-to-json-schema` 同一批依賴鏈），backend／frontend 測試都無法在本機實際執行——與階段
  二十一的已知限制相同，交給 CI 實際跑過；已用人工重讀全部改動檔案與呼叫鏈一遍。
- **刻意先不做**：`GET /me/stream` 目前只在讀者主動載入該頁時抓資料，不是即時／WebSocket
  推送新文章或即時更新未讀數；`article-reader` 開文章時仍各自呼叫 `POST /me/articles/{id}/read`，
  沒有回頭同步已載入的 `ReadingStreamService` 快取，所以在另一個分頁／視窗開文章不會立刻反映在
  已開啟的閱讀流頁面上，要等下一次 `reload()`。兩者都留給之後的批次或後續 PR。
- 對應文件更新：`docs/FEATURES.md` 第 1、3、4、5 節、`TODO.md`（批次 4「我的閱讀流」七項全部
  打勾，「建議開發批次」第 4 項打勾）。

## 階段二十七：Feed 完整文章列表（cursor 分頁、已讀／收藏內嵌切換）（2026-08-28）

TODO.md 建議開發批次第 7 批的第一部分。此前 feed 詳情頁只顯示 `GET /feeds/{feed_id}` 內嵌的最新
10 篇文章，完全沒用到 `GET /feeds/{feed_id}/articles`——這個 offset 分頁端點寫好之後，前端
`ArticleService.getArticles()` 從未被任何元件呼叫過。而且它排序只靠單一 `published_at` 欄位，
`published_at` 為 NULL 的文章會落進 Postgres `DESC` 預設的 `NULLS FIRST`，offset 分頁在新文章
持續進站的情況下也會讓「載入更多」重複或漏掉文章——同 migration 015 替我的閱讀流解決過的問題。

- **`backend/migrations/016_feed_article_list.sql`（新）**：`list_feed_articles(p_feed_id,
  p_user_id, p_cursor_sort_at, p_cursor_id, p_limit)`，`driftread` schema 內的 DB function，
  排序鍵與 cursor 形狀直接沿用 `list_reading_stream`（migration 015）的
  `COALESCE(published_at, fetched_at) DESC, id DESC`，索引也沿用同一個
  `articles_feed_id_sort_at_idx`，不需要新索引。`p_user_id` 可為 NULL——這個 function 服務公開
  端點，未登入呼叫時兩個 LEFT JOIN（`user_article_reads`／`user_bookmarks`，後者固定
  `bookmark_type = 'favorite'`）的條件都不成立，`is_read`／`is_bookmarked` 自然是 false，不需要
  額外分支。`SECURITY INVOKER`，EXECUTE 只授權 `service_role`，同 `list_reading_stream` 一套
  鎖法。
- **`backend/models.py`**：新增 `FeedArticle`（`ArticleSummary` 加 `fetched_at`／`is_read`／
  `is_bookmarked`）與 `PaginatedFeedArticles`；移除不再使用的 `PaginatedArticles`。
- **`backend/routers/articles.py`**：`GET /feeds/{feed_id}/articles` 從 `page`／`page_size`
  offset 分頁改成 `cursor`／`limit`（上限 100）keyset 分頁，寫法與 `routers/me.py` 的
  `/me/stream` 完全對稱（`decode_keyset_cursor` 解 400、`encode_keyset_cursor` 編下一頁
  cursor）。新增 `get_optional_user` 依賴（同 `routers/recommendations.py`／`discover.py` 已有的
  模式）：帶有效 token 時傳 `p_user_id`，否則傳 `None`，端點本身維持公開、不需要登入才能看文章
  列表。
- **`frontend/src/app/services/article.ts`**：`getArticles()` 簽名改成
  `(feedId, cursor?, limit?)`，回傳 `PaginatedFeedArticles`。
- **`frontend/src/app/components/feed-detail`**：文章列表獨立於 feed metadata 載入（各自的
  loading／error 狀態，互不阻塞），「載入更多」同 `reading-stream` 的 pattern；每列在登入後顯示
  已讀／收藏切換按鈕（未登入不顯示——單篇的已讀狀態沒有像訂閱那樣的「登入後回來完成」流程可以
  接，直接不出現比較誠實），樂觀更新＋失敗回滾＋pending 期間忽略重複點擊，寫法與
  `ReadingStreamService` 的 `markRead`/`markUnread` 同一套，但因為 feed 詳情頁本身不追蹤未讀數，
  這裡的 pending/patch 邏輯直接寫在元件裡，沒有另外拉一個 service。收藏固定走 `favorite` 類型
  （同「稍後讀」共用同一個 `POST /me/bookmarks`，這裡只是預設分類，UI 上仍可另外用既有的
  `/me/bookmarks` 頁面管理兩種收藏）。已讀文章的標題同 `reading-stream` 用 `.is-read` 降低對比而
  非劃掉，維持可讀性。
- **不做的部分**：`GET /feeds/{feed_id}` 內嵌的固定 10 篇 `articles` 欄位維持不動——沒有其他前端
  消費它，但這是開放 API 的一部分，拿掉有未知的外部風險，維持它的成本也接近零；`feed-detail` 頁
  本身已經不再讀這個欄位。
- **測試**：`backend/tests/test_articles.py` 新增 `list_feed_articles` 呼叫參數（含匿名／已登入
  兩種 `p_user_id`）、cursor 編解碼與分頁邊界的案例。`frontend/.../feed-detail.spec.ts` 新增
  `FeedDetail article list` describe block：首頁載入、載入更多、已讀／收藏樂觀更新與失敗回滾、
  pending 期間忽略重複點擊（用 `Subject` 卡住尚未 resolve 的請求驗證，而非假設 `of()` 的同步
  resolve 能測出 in-flight 狀態）、session 從 null 非同步解析出已登入身分後重新載入（見下）。
- **PR review 修正**（Codex）：`ngOnInit()` 原本只在元件建立時呼叫一次 `loadArticles()`；但
  `AuthService.session` 是非同步還原的持久化 session（見 `services/auth.ts`），直接訪問頁面時
  即使讀者其實已登入，`session()` 一開始仍是 `null`。原本的一次性載入會因此在還沒拿到 token 前
  就送出請求，`is_read`／`is_bookmarked` 全部回 false，且 session 還原後不會重新載入——同
  `bookmarks.ts`／`my-feeds.ts` 已經處理過的那類問題。改成建構子內的 `effect()`，依
  `auth.session()` 的使用者 id 觸發載入（`articlesLoadedFor` 記錄目前是替誰載入的，`undefined`
  代表「還沒載入過」，用來與「已登入但 id 為 null 沒有意義」的匿名情況區分），涵蓋初始匿名載入、
  session 非同步解析、登出與換帳號四種情況。測試沿用 `my-feeds.spec.ts` 的 pattern：
  `AuthService.session` 用真的 `signal()` 而非普通函式（否則 Angular 的 `effect()` 沒有訊號可以
  追蹤，測不出重新載入的行為），`session.set(...)` 後 `fixture.detectChanges()` 讓元件自己的
  effect 重新 flush。

  這個修法本身又引入新的競態：identity effect 觸發已登入的重新載入時，前一輪匿名請求可能還在
  飛行中——若它比已登入的回應晚到，會用全部是 false 的 `is_read`／`is_bookmarked` 蓋掉剛套用好
  的已登入狀態；同理，`loadMoreArticles()` 的回應若晚於一次新的 identity 重新載入落地，也會把
  舊身分的文章接到新載入的列表後面。Codex 第二輪抓到這點，修法是加一個 `articlesGeneration`
  計數器（`loadArticles()` 每次呼叫遞增，`loadMoreArticles()` 只讀取不遞增），`next`／`error`
  callback 落地時比對呼叫當下記下的值，不相符就丟棄——與 `ReadingStreamService` 的
  `_itemsGeneration` 同一套 pattern。新增迴歸測試：用 `Subject` 手動控制兩個請求的 resolve
  順序，讓匿名回應刻意晚於已登入回應落地，斷言最終畫面是已登入那份、不是被匿名回應蓋掉。

  Codex 第三輪接著抓出 `articlesGeneration` 還沒涵蓋到的另一半：讀者點了已讀／收藏切換、
  request 還沒回來時登出或換帳號，identity effect 會先把列表重新載入成新身分的資料，但舊切換的
  `error` callback 落地時原本會無條件把 `wasRead`／`wasBookmarked` 蓋回去——蓋的不是它自己那份
  已經不在畫面上的舊列表，而是新身分剛載入、正確的那份。`pendingRead`／`pendingBookmark` 也是同一
  個問題的另一面：舊切換的 callback 現在被 generation 檢查擋下，不會再走到原本清除 pending flag
  的那行，若同一個 article id 剛好也在新身分的列表裡（同一個 feed，通常就是），它的已讀／收藏
  按鈕會卡在永久 disabled。修法：`toggleRead()`／`toggleBookmark()` 進入時各自記下當下的
  `articlesGeneration`，`next`／`error` callback 落地時比對，不符就整段跳過；`loadArticles()`
  額外把 `pendingRead`／`pendingBookmark` 重置成空集合（放在遞增 generation 的同一個地方）——
  這兩個 pending set 的清除本來就只會發生在切換自己的 callback 裡，換代後那條路徑不會再走到，
  只能由取代它的那次載入自己負責清乾淨。迴歸測試：用 `Subject` 卡住一次 `markRead`，切換身分
  觸發重新載入並斷言 pending flag 立刻歸零（不是卡住），接著讓新身分的真實資料落地，最後讓卡住
  的舊 `markRead` 才失敗，斷言畫面停留在新身分的正確值、不是被蓋回舊的樂觀回滾值。
- **本 sandbox 的已知限制**：`pip install pytest`／`npm ci` 仍被 allowlist 擋下，backend／
  frontend 測試都無法在本機實際執行——與階段二十一至二十六相同；已用 `python3 -m py_compile`
  過 backend 改動、系統 `tsc`（`--ignoreConfig --noResolve`，僅語法檢查，過濾掉預期內的
  module-not-found／implicit-any／缺 test runner 型別錯誤後沒有其他訊息）過 frontend 改動，交給
  CI 實際跑過驗證。
- 對應文件更新：`docs/FEATURES.md`（Feeds/Articles API、文章預覽功能列、第 5 節資料表）、
  `TODO.md`（「Feed 完整文章列表」四項打勾，「建議開發批次」第 7 項改為進行中）。

## 階段二十八：/me/* 端點的跨使用者資料隔離測試（2026-09-02）

同一個「持續改善專案」排程任務。接續 TODO.md「技術與可靠性優化 / Auth 與安全」一直未打勾的
「為登入後的 user-scoped API 加上跨使用者資料隔離測試」——這批端點的隔離完全不靠 RLS：
`user_feeds`／`user_article_reads`／`user_bookmarks`／`user_preferences` 四張表雖然開了
owner-only policy（見 migration 002），但後端一律用 `service_role` client 呼叫（見
docs/FEATURES.md 第 5 節與 TODO.md Phase 0 的記錄），RLS 對這個 client 完全不生效；真正把
使用者資料隔開的只有 `routers/me.py`／`routers/opml.py` 裡每個 handler 自己記得在查詢與寫入時
帶上 `user.id`。這件事此前完全沒有回歸測試守著——`test_me.py` 的既有測試只覆蓋單一使用者
（固定 `sub: "user-abc"`）的行為是否正確，抓不到「兩個不同使用者的請求互相污染」這類錯誤。

- **新增 `backend/tests/test_me_isolation.py`**：涵蓋 `routers/me.py` 全部 14 個端點
  （訂閱清單／訂閱／取消訂閱、標記已讀／取消已讀、`GET /me/reads`、`POST /me/reads/mark-all`
  的兩條路徑——`article_ids` 走 table upsert、不帶則走 `mark_reading_stream_read` RPC——
  `GET /me/stream`、`GET /me/stream/unread-counts`、收藏清單／加入／移除、偏好設定
  讀取／更新）與 `routers/opml.py::export_opml`。每個測試用兩個不同使用者（各自簽發合法
  JWT，`sub` 不同）呼叫同一端點兩次，共用同一個 mock_db，斷言送進 Supabase `.eq("user_id",
  ...)` 過濾條件、`upsert()` 寫入 dict 或 RPC 的 `p_user_id` 參數，在兩次呼叫裡分別對應各自
  呼叫者、且彼此不同——會抓到「user_id 被硬寫」「跨請求沿用前一個使用者的值」「從錯的地方
  （而非驗證過的 JWT）取 id」這幾類會讓應用層隔離悄悄失效、但單一使用者測試看不出來的回歸。
- **範圍以外**：`POST /me/import/opml` 雖然也是 user-scoped（訂閱寫入帶 `user.id`），但要
  driving 這個端點需要 mock 檔案上傳與逐一 feed 的 `fetch_and_parse` 外部請求，複雜度與這批
  「查詢/寫入參數斷言」型態的測試不同，留給後續 PR。
- **本 sandbox 的已知限制**：`pip install pytest` 仍被 PyPI allowlist 擋下（`No matching
  distribution found for pytest`），backend 測試無法在本機實際執行——與階段二十一至二十七
  相同；已用 `python3 -m py_compile` 過新檔案，並逐一比對 `routers/me.py`／`routers/opml.py`
  的每個查詢鏈與既有 `test_me.py` 的 mock 慣例（`mock_db.table.return_value.select.return_value
  .eq...` 的分層結構、`_reads_chain` 同款 keyset 分頁 mock）手動核對每個測試的 mock 設置與
  斷言路徑，交給 CI 實際跑過驗證。
- 對應文件更新：`TODO.md`（「Auth 與安全」一項打勾，並記錄範圍與未涵蓋的 `import/opml`）。

## 階段二十九：補上 /me/import/opml 的跨使用者隔離測試（2026-09-03）

同一個「持續改善專案」排程任務，接續階段二十八明確列為範圍以外的 `POST /me/import/opml`——
當時因為要驅動這個端點走到真正寫入 `user_feeds` 的那一步，除了 mock DB 之外還得先讓
`validate_fetch_url()`／`fetch_and_parse()` 兩個外部呼叫成功，複雜度與其餘「純 DB 查詢/寫入
斷言」型態的測試不同，故另開一批。

- **`backend/tests/test_me_isolation.py` 新增 `test_import_opml_subscribes_with_calling_users_id`**：
  用 `unittest.mock.patch("routers.opml.validate_fetch_url", ...)` 與
  `patch("routers.opml.fetch_and_parse", ...)`（兩者皆為 `routers/opml.py` 從
  `services/feed_discovery.py`／`rss_parser.py` import 進自己命名空間的名字，故照本專案既有
  慣例——見 `test_discover.py::test_discover_checks_existing_feeds_without_bulk_in_filter` 對
  `routers.discover.discover_feeds` 的 patch 寫法——patch 在使用處而非定義處）把兩者換成回傳固定值的假 `async def` 函式，讓一份
  單一 outline 的 OPML 檔案能真的跑到 `db.table("feeds").upsert()` 與
  `db.table("user_feeds").upsert()` 兩次寫入。兩個使用者各自匯入一次，斷言 `user_feeds` 那次
  upsert（用「dict 裡有沒有 `user_id` 鍵」跟同一個 mock 上的 `feeds` upsert 分開）帶的
  `user_id` 對應各自呼叫者。
- 對應文件更新：`TODO.md`（「跨使用者資料隔離測試」項目補記 `import_opml` 已涵蓋）。

## 階段三十：`POST /api/discover/import` 改為要求登入（2026-09-03）

TODO.md「Auth 與安全」批次的第一項：這個端點原本 `get_optional_user`，未登入呼叫者一樣能把
遠端 feed 回應的第三方文字（`title`／`description`／`website_url`／`language`）直接 upsert 進
公開、無使用者範圍的 `feeds` catalog，供所有使用者的目錄瀏覽／發現／猜你喜歡共用。既有的
per-IP rate limit（每分鐘 20 次）擋不住輪換 IP 的長期灌入，且每次成功呼叫都會在全域目錄留下
一筆無法歸責的紀錄。

- **`backend/routers/discover.py`**：`discover_and_import` 的 `user` 參數從
  `AuthUser | None = Depends(get_optional_user)` 改成 `AuthUser = Depends(get_current_user)`，
  未帶合法 bearer token 在任何抓取或 DB 寫入之前就回 `401`。原本「已登入才順便訂閱」的
  `if user:` 分支跟著拿掉——訂閱一律發生。`POST /discover`（只回傳候選清單，從不寫入）維持
  公開不需要登入。
- **`frontend/src/app/components/discover/discover.ts`**：`importFeed()` 未登入時不再直接呼叫
  後端，改為導向 `/login?redirect=/discover`，同既有 `subscribeExisting()` 對已存在 feed 的
  處理模式；按鈕文字對應從「匯入到資料庫」改成「登入以匯入並訂閱」。
- **測試**：`backend/tests/test_discover.py` 新增未登入 401（DB 從未被呼叫）與已登入完整匯入
  ＋自動訂閱路徑兩個案例；既有的私網 URL／metadata URL／rate limit 系列測試（`test_discover.py`
  ／`test_main.py`／`test_rate_limit.py`）補上合法 bearer token，讓它們繼續驗證各自原本要測的
  行為（URL 驗證、rate limit），不被新加的 401 蓋過去。`discover.spec.ts` 新增對應的
  「未登入導向登入頁、不呼叫後端」案例，取代原本測「未登入匯入不標記訂閱」的案例（該行為已不
  適用——未登入現在根本不會呼叫匯入）。
- **本 sandbox 的已知限制**：`pip install pytest`／`npm ci` 仍被 allowlist 擋下，backend／
  frontend 測試都無法在本機實際執行——與階段二十一至二十七相同；已用 `python3 -m py_compile`
  與 `ruff check` 過 backend 改動、系統 `tsc`（`--ignoreConfig --noResolve`）過 frontend 改動，
  交給 CI 實際跑過驗證。
- 對應文件更新：`docs/SECURITY.md`（新增 #30）、`TODO.md`（「Auth 與安全」該項打勾）。
- **PR review 修正第一輪（Codex，P2）**：未登入點擊匯入時，原本只把 `redirect=/discover` 帶去
  登入頁，候選清單與選中的 feed URL 都會在導頁後消失，讀者得重新貼一次網址、重新發現、再點一次
  匯入。第一版修法比照既有的 `subscribeFeed` query param 模式，帶一個 `importFeedUrl` 參數，
  `Login.submit()`（新增注入 `DiscoverService`）登入成功後偵測到就呼叫 `importByUrl()`。
- **PR review 修正第二輪（Codex，兩個 P2）**：
  1. 第一版的 `importFeedUrl` 是直接讀 query string——任何人都能構造
     `/login?importFeedUrl=<攻擊者網址>` 連結，受害者只要照常登入，就會在完全沒點過「匯入」的
     情況下，被伺服器抓取那個攻擊者網址、把回應寫進全域 catalog、還自動訂閱受害者帳號——正好
     繞過這個 PR 原本要補的「需要使用者主動操作才能寫入 catalog」防線。修法：改用
     `sessionStorage`（新增 `frontend/src/app/shared/pending-import.ts`，
     `setPendingImportFeedUrl()`／`takePendingImportFeedUrl()`，讀取即清除、只能被重放一次）
     取代 query param——只有 Discover 頁面自己的 JS 在讀者真的點擊匯入後才會寫入這把 key，
     外部連結無法注入。`subscribeFeed` 沿用既有的 query param 設計不動：它訂閱的是已在目錄裡、
     經過驗證的既有 feed id，不會觸發伺服器對任意網址的抓取與寫入，風險層級不同，不在這次修法
     範圍內。
  2. 呼叫 `importByUrl()` 到回應落地之間，若讀者登出或換帳號，原本會無條件把這筆訂閱
     `markSubscribed()` 到*現在*登入的身分上，即使伺服器實際上是替*發起請求當下*的使用者建立的
     ——同 `Discover.importFeed()` 已有的 `requestedFor` 防線是同一類問題，只是這條 resume 路徑
     漏補。修法：`Login.submit()` 在送出 `importByUrl()` 前記下 `auth.session()?.user?.id`，
     回應落地時比對現在的身分，不符就跳過 `markSubscribed()`，同 `Discover.importFeed()` 一套
     寫法。
  - 新增 `frontend/src/app/shared/pending-import.ts`。`login.spec.ts` 新增／改寫案例涵蓋：
    resume 讀取後即清除、query param 版的 `importFeedUrl` 不會被採用、換帳號後不
    `markSubscribed()`。`discover.spec.ts` 既有的登出點擊匯入測試改斷言寫入 `sessionStorage`
    而非帶 query param。
- **PR review 修正第三輪（Codex，P2）**：第二輪把 stash 換成 `sessionStorage` 後，還留一個缺口：
  `Login.submit()` 是「只要登入成功就讀 sessionStorage 裡有沒有待匯入的值」，沒有檢查這次登入
  是不是真的從那個匯入導頁過來的。讀者點匯入、在 `/login` 按上一頁放棄、之後在同一個分頁因為
  別的原因（例如想看自己的訂閱）正常登入，會在完全沒有再點過匯入的情況下，把那個早已放棄的
  URL 悄悄匯入並訂閱——把「任何後續登入」錯當成「使用者確認了這個舊決定」。修法：
  `setPendingImportFeedUrl()` 改成回傳一次性 nonce（`crypto.randomUUID()`），連同 URL 一起存成
  `{url, nonce}`；`Discover.importFeed()` 把這個 nonce 一併放進導去 `/login` 的
  `importNonce` query param。`Login.submit()` 只有在目前這次登入的 query string 裡帶著
  `importNonce`、且與 sessionStorage 存的 nonce 相符時才會 resume；沒有 `importNonce`（不是從
  這個匯入流程來的登入）完全不去碰 sessionStorage，讓被放棄的項目留在原地——之後讀者若真的
  重新點一次匯入，會覆蓋掉舊的 `{url, nonce}`，舊 nonce 自然失效，不需要額外清理。
  `pending-import.ts`／`discover.ts`／`login.ts` 的內部說明同步更新；`login.spec.ts` 新增
  「沒有 `importNonce` 不 resume」「nonce 不相符不 resume（且仍清掉舊值）」兩個案例，既有的
  resume／錯誤／換帳號三個案例補上 `importNonce` 與新的 stash 格式；`discover.spec.ts` 的既有
  案例改成驗證回傳的 `importNonce` 與 sessionStorage 內容能對上，而非驗證固定字串。
- **PR review 修正第四輪（Codex，P2）**：`pending-import.ts` 直接呼叫 `sessionStorage.setItem`／
  `getItem`／`removeItem`，沒有考慮 storage 不可用的情況（無痕模式、封鎖 cookie／storage）——
  這類環境下 `setItem()` 可能直接 throw，而這個呼叫發生在 `Discover.importFeed()` 導向
  `/login` 之前，未接住的例外會讓整個點擊沒有反應：既不導去登入頁，也沒有任何錯誤訊息。專案裡
  `AdminKeyStore`（`services/admin-key.ts`）已有處理同一種失效模式的既有寫法。修法：`set`／
  `take` 兩個函式的 storage 呼叫都包進 `try/catch`——寫入失敗時仍照常回傳 nonce（讓導頁繼續
  進行，只是登入後沒有東西可以 resume，等同讀者沒點過匯入）；讀取失敗時回傳 `null`（等同沒有
  待處理的匯入），不讓例外往外冒。新增 `pending-import.spec.ts`，涵蓋一般 set／take 往返、
  read-once、nonce 不符、storage 為空，以及 `setItem`／`getItem` 各自 throw 時的容錯行為
  （`vi.spyOn(Storage.prototype, ...)`）。
- **PR review 修正第五輪（Codex，P2）**：第四輪把 storage 呼叫包進 `try/catch`，但
  `crypto.randomUUID()` 的呼叫本身留在 `try` 區塊外——在不安全來源（非 HTTPS、非
  localhost）或較舊瀏覽器上，`crypto.randomUUID` 可能整個不存在，直接呼叫會 throw，而這發生在
  `Discover.importFeed()` 呼叫 `router.navigate()` 之前且未接住，一樣會讓整個點擊沒有反應。
  修法：新增 `generateNonce()`，把 `crypto.randomUUID()` 包進 `try/catch`，失敗時退回
  `Date.now()`／`Math.random()` 組成的字串。這個 nonce 不需要密碼學等級的不可猜測性——全程只在
  同一個分頁內產生與比對，從未傳輸到任何攻擊者觀察得到的地方——退回方案在這裡是安全的。
  `pending-import.spec.ts` 新增案例：`vi.spyOn(crypto, 'randomUUID')` 模擬拋出，斷言仍能拿到
  可用且彼此不同的 nonce，且能正常完成 resume。

## 階段三十一：`ReadingStreamService` 補齊 pending-write 與並發 GET 的 reconciliation（2026-09-07）

- **背景**：PR #43 code review 時就記錄在 TODO.md 的已知缺口——`ReadingStreamService`
  只有 `_itemsGeneration`／`_countsGeneration` 兩個「較新請求蓋掉較舊請求」的計數器，沒有
  `SubscriptionService` 那套「這個 id 有 pending 寫入或已確認的較新寫入，GET 回來的舊快照不能
  贏」的 ticketing。當時故意留給後續 PR，因為都是窄視窗、只影響畫面顯示到下次 reload／filter
  變更為止，不影響伺服器端資料正確性。
- **改動**：`frontend/src/app/services/reading-stream.ts` 新增單一共用的 `version` 單調序號，
  每次 `beginFetch()`（GET 前）與每次寫入 commit 都各自抽一個 ticket，同
  `SubscriptionService` 的 `beginFetch`/`confirmWrite`/`asOf` 設計：
  1. `load()`/`loadMore()` 回應套用前先 `reconcileItems()`：id 若仍在 `_pending`（該篇文章自己
     的寫入還沒結束）就沿用回應前的舊值，而不是被可能落後的伺服器快照蓋掉；否則若
     `_confirmedReadAt` 記錄的確認時間點晚於這個 GET 的 `asOf`，改採確認值——GET 有可能是在
     那次確認送出「之前」就已經發出的。
  2. 未讀數改成「最後一次接受的 GET 快照（baseline）＋尚未確認被該快照涵蓋的本地 delta 清單」，
     `recomputeCounts()` 統一算出顯示值。GET 回應一律套用、更新 baseline 並把 ticket 早於等於
     這次 `asOf` 的 delta 修剪掉，不再整批丟棄——舊版的 `_countsGeneration` 在偵測到「這次回應
     比某個本地寫入舊」時會整包丟棄，包含第一次 `loadCounts()` 都還沒回來過的情況；那種情況下
     即使丟棄也照樣把 `countsLoaded` 設成 `true`，之後沒有任何機制重試，未讀數就永遠卡在
     clamp 過後的錯誤猜測值。
  3. `markAllReadInView` 現在也把批次的目標 id 一併登記進 `_pending`（並在 settle 後清除），
     `markRead`/`markUnread` 既有的 `isPending` guard 因此也會排除掉正在被批次處理的文章，
     反之亦然——避免同一篇文章被批次與單篇操作各自套用一次 optimistic count delta，重複計算
     同一次伺服器端變更。`markAllReadInScope` 的成功回呼比照排除仍 pending 的文章，不去覆蓋它
     正在進行中的單篇寫入。
- **測試**：`reading-stream.spec.ts` 新增四個案例，各自對應上面修掉的情境——`markRead` 在
  `loadCounts()` 第一次回來前提交，斷言最終仍會拿到真正的 baseline 而不是卡住；`load()` 在
  同一篇文章有 pending 寫入時保留樂觀值；`load()` 在寫入已確認、但 GET 是在確認之前發出時改採
  確認值；`markAllReadInView` 排除掉正有 `markUnread` pending 的文章。既有測試不需要改動斷言即
  可通過（唯一調整：移除已被新機制取代的 `_countsGeneration` 相關內部欄位，行為對外不變）。
- **本 sandbox 的已知限制**：`npm ci` 被 `registry.npmjs.org` 的 network egress allowlist 擋下
  （一個 `@angular/cli` 的間接依賴 `zod-to-json-schema`），與先前多個 PR 遇到的限制相同，無法在
  本機實際跑 `vitest`／production build。已用系統 `tsc`（`--ignoreConfig --noResolve`）過新增
  改動，並用專案的 `.prettierrc.json` 設定跑過 `prettier --check`；邏輯正確性以逐步手動追蹤三個
  新增測試案例的 ticket／delta 數值驗證，交給 CI 的 `frontend.yml` 實際跑過 `npm test` 驗證。

## 階段三十二：JWT 驗證改為支援 Supabase JWKS（ES256／RS256），保留 HS256 相容（2026-09-09）

- **背景**：TODO.md「Auth 與安全」批次最後兩個未完成項目——`backend/auth.py` 從一開始就只
  接受 HS256 shared secret（`SUPABASE_JWT_SECRET`）。Supabase 目前預設把新專案的 JWT signing
  key 改成非對稱（ES256，也支援 RS256），HS256 secret 仍可用但屬於逐步淘汰的舊路徑；只認
  HS256 意味著已經（或將要）輪替到新式 signing key 的專案會拿到完全驗證不了的 token。
- **改動**：`auth._verify_token` 從固定用一把密鑰改成先讀 token header 的 `alg`，再決定走哪條
  固定路徑——`alg=HS256` 一律用 `SUPABASE_JWT_SECRET`；`alg` 為 `ES256`／`RS256` 一律改用
  `jwt.PyJWKClient` 向 JWKS 端點（預設 `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`，可用
  新增的 `SUPABASE_JWKS_URL` 覆寫）取得對應 `kid` 的公鑰驗證；其餘 `alg` 一律 401。兩條路徑各自
  固定死對應的驗證方式與金鑰來源，不會因為 token header 而互相借用——不會重開「alg 混淆」
  （攻擊者把 header 的 `alg` 換成 HS256、拿公鑰當 HMAC secret 偽造簽章）這類洞。
  Signing key rotation／cache refresh 兩項直接交給 `PyJWKClient` 自己的機制，不另外手寫快取：
  `cache_keys=True` 快取取回的 JWKS 文件 `lifespan`（300）秒；快取裡找不到的 `kid` 會觸發一次
  無條件重新抓取，所以剛輪替的新 signing key 第一個帶新 `kid` 的 token 就能驗證，不必等快取
  過期。`requirements.txt` 的 `pyjwt` 依賴加上 `[crypto]` extra（帶入 `cryptography`）——原本
  只用 HS256 時不需要它，PyJWT 的非對稱演算法沒有這個套件會直接說「不支援該演算法」。
- **相容性**：沒有新增必填環境變數。`SUPABASE_URL` 本來就必填，JWKS 端點預設由它推導；
  `SUPABASE_JWKS_URL` 是選填覆寫，只有自架或非標準網域才需要。尚未輪替 signing key 的專案
  送來的 token 仍是 `alg=HS256`，行為與改動前完全相同。
- **測試**：`backend/tests/test_auth.py` 新增 `TestJwksVerification`——ES256／RS256 token 經
  JWKS 驗證成功、匿名使用者仍被拒、不在 JWKS 裡的金鑰（含刻意選一個不存在的 `kid`）與缺
  `kid` 的 token 都要 401、輪替情境（換一把新 `kid` 簽的 token，只在快取沒有該 `kid` 時觸發
  「恰好一次」重新抓取，不是每次都打 JWKS 端點）、JWKS URL 推導與覆寫、`SUPABASE_URL` 未設定
  時的非對稱驗證回退成 500。既有 HS256 案例不變，額外補了不支援的 `alg`（`none`）必須 401。
- **本 sandbox 的已知限制**：`pip`／`uv` 的 PyPI index 被 network egress allowlist 擋下，
  與先前多輪修法相同的既有限制，無法直接 `pip install -r requirements.txt` 跑專案的
  `pytest`。改用 `python3 -m py_compile` 驗證語法、`ruff check` 過 lint；額外用一個獨立
  venv（`uv venv` + 從 uv 的本地 wheel cache 離線裝 `cryptography`／`cffi`，並沿用系統既有的
  `PyJWT` 套件目錄）搭配一個只實作 `HTTPException`／`status`／`Header` 三個名字的最小 fastapi
  替身模組，直接呼叫 `auth._verify_token()`（不經過完整的 FastAPI app）逐一手動重現上面測試
  案例涵蓋的每個情境並斷言結果——HS256 correct／wrong secret、匿名拒絕、`alg=none` 拒絕、
  ES256／RS256 成功、輪替時恰好一次重抓、錯誤金鑰拒絕、缺 `kid` 拒絕、JWKS URL 推導與覆寫、
  缺 `SUPABASE_URL` 時 500，全部通過。這只驗證了 `auth.py` 本身的邏輯，FastAPI route 與
  依賴注入的接線交給 CI 的 `backend.yml` 實際跑 `pytest`。
- 對應文件更新：`TODO.md`（「JWT 驗證由只接受 HS256...」與「支援 signing key rotation 與 JWKS
  cache refresh」兩項打勾）、`docs/SECURITY.md`（新增 #31）、`README.md` 與 `.env.example`
  （新增 `SUPABASE_JWKS_URL` 說明）。
- 對應文件更新：`TODO.md`（「ReadingStreamService 補齊 pending-write...」打勾並補上機制說明）。
- **PR review 修正第一輪（Codex，3 個 P2，均證實為真）**：
  1. **未讀數的 ticket 修剪條件用錯了比較對象**：`loadCounts()` 接受一個新 baseline 時，原本用
     「delta 的 commit ticket 是否 ≤ 這次 GET 的 `asOf`」決定要不要修剪掉這筆 delta——但
     commit ticket 只記錄「client 端何時做出樂觀猜測」，不代表伺服器當時已經處理完那次寫入。
     一個仍在 pending（尚未拿到伺服器回應）的寫入，即使它的 ticket 早於某次 GET 的 `asOf`，
     那次 GET 抓到的也可能是寫入生效「之前」的伺服器狀態——照舊邏輯會把這筆 delta 誤判成
     「已經反映在快照裡」而修剪掉，實際上該篇文章的寫入之後才確認成功，未讀數會憑空少算一次
     直到下次真正刷新。修法：把 delta 儲存結構從「ticket 陣列」改成「依 article id 存淨值的
     Map」（`_countDeltasByArticle`，同一篇文章的多次調整直接互相抵銷，不留兩筆），
     `recomputeCounts()` 判斷「這筆 delta 現在還要不要疊加」的依據改成同 `reconcileItems()`
     一致的規則：這篇文章若仍是 `_pending`，無論 ticket 為何一律疊加（寫入還沒有結果，任何
     baseline 都不能假設已經反映它）；只有寫入已確認（`_confirmedReadAt` 有記錄）時，才拿
     那次「確認」的 ticket 去跟 baseline 的 `asOf` 比。
  2. **「較舊請求被較新請求蓋掉」只認已經套用的 baseline，沒認已經發出的請求**：`loadCounts()`
     的 error handler 原本用「這次失敗的 `asOf` 是否早於目前 baseline 的 `asOf`」判斷要不要
     忽略這個失敗——但如果兩個 `loadCounts()` 同時在飛、較舊的那個先失敗、較新的那個都還沒回來
     （baseline 還沒被任何一個更新過），這個條件就抓不到「其實已經有更新的嘗試在路上」，較舊
     那次的失敗會被誤判成整個操作失敗，把 `countsLoading` 提早關掉、`onError` 提早觸發，即使
     真正較新的請求隨後成功。修法：新增 `_countsLatestIssuedAsOf`，在每次呼叫的當下（而非拿到
     回應時）就更新——error handler 改成跟這個「最新已發出」的 ticket 比，不是跟「最新已套用」
     的 baseline 比；success handler 也只在自己就是最新已發出的那次時才清 `countsLoading`。
  3. **`load()`/`loadMore()` 用來 reconcile 的 `previous` 陣列在請求「發出當下」就截取了**：
     若 GET 發出時文章還是未讀、`markRead()` 是在 GET 已經送出、回應尚未回來之間才啟動並樂觀
     翻成已讀，`reconcileItems()` 看到這篇文章仍是 `_pending`，會拿舊的（GET 發出當下截取的）
     `previous` 快照蓋回去——那個快照本身就還沒反映這次樂觀更新，所以會把已讀又蓋回未讀；
     等到 POST 真的成功，成功回呼只呼叫 `confirmReadState()` 記錄確認結果，並不會重新
     `patchItem()` 那一列，錯誤的未讀狀態會一直留到下一次 `load()`/`loadMore()` 才自動修正。
     修法：`reconcileItems()` 不再吃外部傳入的 `previous` 參數，改成在方法內部呼又時當下讀
     `this._items()`——這樣拿到的一定是回應抵達那一刻的最新狀態，包含回應抵達前才啟動的樂觀
     更新。
  - **測試**：`reading-stream.spec.ts` 新增三個案例，各自對應上面三點——`loadCounts()`
    在寫入仍 pending 時，即使 delta 的 commit ticket 早於某次 GET 的 `asOf`，未讀數仍要保留該
    delta（若照原本邏輯執行會斷言失敗，證實這是真的迴歸測試而非重複既有覆蓋）；`loadCounts()`
    在較舊請求先失敗、較新請求還沒回來時忽略那次失敗且不提早關閉 `countsLoading`；
    `load()` 在 GET 發出「之後」才啟動的 markRead optimistic 更新，回應抵達時仍要保留已讀狀態
    （既有的「pending 保留樂觀值」案例其實沒踩到這個 bug——那個案例裡寫入是在 `load()` 呼叫
    「之前」就啟動的，`previous` 截取當下已經反映過樂觀值，不足以證偽舊邏輯，故補這個更精確的
    案例）。既有測試全數維持原斷言可通過。
  - 本輪的網路限制與驗證方式同上——`npm ci` 仍被擋下，改用 `tsc --noResolve` 與
    `prettier --check` 驗證這兩個檔案，邏輯正確性以手動逐步追蹤新增與既有測試案例的
    ticket／delta 數值運算確認，實際 `npm test` 交給 PR #56 的 CI 跑過（`Build` job 綠燈）。
- **PR review 修正第二輪（Codex，2 個 P2，1 修 1 記錄為已知限制）**：
  1. **修**：`markAllReadInView` 的批次部分失敗時，原本先跑失敗批次的 rollback（呼叫
     `commitCountDelta` 觸發 `recomputeCounts()`），才跑成功批次的 `confirmReadState()`。
     成功批次的文章在 `_pending` 已於函式最前面被整批清掉、但還沒被
     `confirmReadState()` 標記確認的這段空窗期裡，若剛好被失敗批次的 rollback
     觸發一次 `recomputeCounts()`，會被誤判成「未 pending 且未確認、ticket 又早於等於
     baseline」而排除在外——`confirmReadState()` 本身不會觸發 recompute，所以這筆遺漏
     直到下次別的地方觸發 recompute 前都不會自己修正，未讀數／badge 會少扣那個成功批次
     的量。修法：把「確認成功批次」的迴圈移到「處理失敗批次 rollback」之前，讓成功批次的
     delta 在任何 rollback 觸發的 recompute 發生前，就已經進入「已確認且 ticket 新於
     baseline」的可疊加狀態。新增 `reading-stream.spec.ts` 案例（620 篇、前 500 成功
     後 120 失敗、baseline 未讀數設一個不會被 clamp 蓋掉差異的大數字），照舊順序執行會
     斷言失敗（顯示未讀數完全沒扣），驗證這是真的迴歸測試。
  2. **記錄為已知限制，未修**：同一篇文章身上，一個仍 pending 的單篇 markRead/markUnread
     與一個涵蓋它的 `markAllReadInScope` 各自獨立送出去，client 端無法從兩個回應誰先抵達
     推斷兩者在伺服器端真正的 commit 順序——任何用回應抵達順序或呼叫 `confirmReadState()`
     先後當決勝規則的修法，都只是把現有的不確定性換一個方向，並不是真的解掉它，需要 API
     額外提供列版本／時間戳之類的排序依據才能穩妥解決。這是窄視窗（兩個獨立寫入要短時間內
     命中同一篇文章）、不影響伺服器端資料正確性、只影響 UI 顯示到下次 reload 為止的既有已知
     限制類型（同本階段開頭「背景」段所述 PR #43 遺留缺口的精神），記錄在 TODO.md
     的「技術與可靠性優化」小節，留待有更明確的排序依據時再處理，不在本 PR 內強行猜一個
     決勝規則。
- **PR review 修正第三輪（Codex，2 個 P2，1 修 1 記錄為已知限制）**：
  1. **修**：`loadCounts()` 成功回呼原本只跟「最後一次被接受的 baseline」比較 ticket 決定
     要不要丟棄這次回應——但如果一個較新（ticket 較大）的 `loadCounts()` 呼叫最終是以
     **失敗**收場（例如 `markAllReadInScope` 寫入成功後觸發的刷新剛好打不到後端），
     baseline 從來沒被那次較新的呼叫推進過，一個更舊、還在飛的請求解析回來時就會通過
     這個比較被誤判成「還沒被蓋過」而被接受，用寫入前的舊數字覆蓋 badge——即使呼叫端才剛被
     `onError` 告知這次刷新失敗。修法：把成功回呼的丟棄條件從「比 baseline 的 ticket 舊」
     改成「比最後一次**發出**的 ticket（`_countsLatestIssuedAsOf`，失敗回呼已經在用同一個）
     舊」——只要曾經發出過更新的請求，不管那個更新的請求最後是成功還是失敗，比它舊的回應
     一律視為作廢，不再有機可乘；順便讓「什麼時候該清 `countsLoading`」的邏輯跟著簡化成
     「接受回應就清」，因為現在能被接受的回應必然就是最後一次發出的那個。新增
     `reading-stream.spec.ts` 案例：較新請求先失敗、較舊請求帶著假數字之後才回來，斷言
     未讀數與 `countsLoaded` 都維持初始狀態（照舊邏輯執行會斷言失敗，證實是真的迴歸測試）。
  2. **記錄為已知限制，未修**：`_countDeltasByArticle` 對「仍是 `_pending` 的文章」一律疊加
     delta、完全不比較 ticket，這本來就是上一輪修 bug 時的刻意選擇，Codex 這輪指出它的
     鏡像代價——若某篇文章的寫入其實已經在伺服器端 commit、只是自己的回應還沒送達
     client，一個「之後才發出、卻先抵達」且已經反映這次寫入的 `loadCounts()` GET 會讓這篇
     文章的 delta 被多算一次。改成比照已確認寫入去比較 ticket，會直接讓上一輪才修掉的原始
     bug（pending 寫入被 baseline 誤判成「已反映」而整個丟棄）復發——兩個方向的 bug 無法只靠
     client 端的 ticket 排序同時解掉，性質與根因和上面「`markAllReadInScope` 與 pending
     markRead/markUnread」那項完全一樣。在 `recomputeCounts()` 的方法註解與 `TODO.md`
     都記錄了這個取捨與原因，不強行對調方向。
  - 本輪驗證方式同前兩輪：`npm ci` 仍被 network egress allowlist 擋下，改用
    `tsc --noResolve` 與 `prettier --check` 驗證改動，邏輯以手動追蹤新增測試案例的
    ticket 數值運算確認，實際跑測試交給 PR #56 的 CI。

- **PR review 修正第四輪（Codex，2 個 P2，全修）**：
  1. **`_countDeltas` 改成不合併、每寫入一筆 entry**：原本以 article id 為 key 合併淨值——
     一篇文章連續兩次寫入會被相消成一個數字。問題情境：文章原本已讀，`markUnread` 成功確認
     （`+1`），接著發出 `loadCounts()`；GET 還沒回來前，同一篇文章又被 `markRead`（`-1`）。
     合併淨值 `+1-1=0`，整條記錄直接被刪掉——但這個「0」抹掉的是兩筆完全不同性質的資訊：
     舊的 `+1` 已經確認成功、只是還不確定目前 baseline 有沒有反映它；新的 `-1` 則是還沒有
     任何伺服器回應的樂觀猜測，兩者需要各自獨立判斷。等 GET 回來，剛好只反映了
     `markUnread`（還沒反映 `markRead`），因為記錄已經被刪掉，未讀數少了「還有一個 pending
     寫入尚未疊加」的資訊；`markRead` 之後真的成功時，也沒有任何機制再把未讀數修正回來，
     卡住直到下次完整刷新。修法：`_countDeltas` 從 `Map<articleId, {feedId, amount}>` 改成
     一個陣列，每次 `commitCountDelta()` 都新增一筆獨立 entry
     `{articleId, feedId, amount, pending, confirmedAt}`，回傳該 entry 的參照；寫入成功時
     呼叫新的 `confirmCountDelta(entry, ticket)` 原地標記確認（`ticket` 直接沿用
     `confirmReadState()` 回傳的同一個，兩者代表同一個事件）；失敗時呼叫新的
     `removeCountDelta(entry)` 整條移除，不再用推入相反數字相消的方式處理 rollback。
     `recomputeCounts()`／`loadCounts()` 的 GC 迴圈都改成逐筆判斷
     `entry.pending || entry.confirmedAt > baselineAsOf`，不再用 `isPending(articleId)`
     查全域 `_pending` set——這帶來一個附帶好處：上一輪「批次成功要在失敗 rollback 觸發的
     recompute 之前先確認」那個順序限制，現在因為 entry 自己的 `pending` 欄位與共用的
     `_pending` set 已經脫鉤，兩個迴圈用哪個順序執行都不影響正確性，`markAllReadInView`
     的註解一併更新說明這點（順序仍保留，只是原因換了）。
  2. **`reconcileItems()` 的 pending 分支只該合併已讀狀態**：文章有 pending 寫入時，原本是
     整個回傳 `previous` 裡的舊物件（`return prior`），這連同已讀狀態一起把該篇文章可能被
     feed 重新抓取（`upsert_articles()`）更新過的標題／摘要／作者／發布時間等欄位也一併蓋回
     舊快取值，直到下一次沒有寫入競爭的 reload 才會修正。修法：改成
     `{ ...item, is_read: prior.is_read, read_at: prior.read_at }`，只從舊物件合併這兩個
     真正在競爭中的欄位，同已確認分支原本就有的寫法一致。
  - **測試**：`reading-stream.spec.ts` 新增兩案例——一篇文章先 `markUnread` 確認、
    `loadCounts()` 發出後又 `markRead`（仍 pending）：斷言整個過程與最終未讀數都正確
    （照舊的合併邏輯執行，GET 回來後與 `markRead` 成功後都會停在錯誤值，證實是真的迴歸
    測試）；`load()` 在文章有 pending 讀取切換時，回應帶回的新標題不會被舊快取蓋掉。
  - 本輪驗證方式同前——`npm ci` 仍被擋下，改用 `tsc --noResolve`／`prettier --check`；
    這輪額外用一個獨立的最小重現檔案（不含 rxjs／Angular import）搭配完整型別解析的
    `tsc` 驗證了 `(typeof this._countDeltas)[number]` 與 `new Map(...)` 這類寫法本身沒有
    型別錯誤——本檔案內的 `--noResolve` 檢查在這幾行報的兩個型別錯誤經確認是
    `--noResolve` 本身破壞 tuple 型別推導的假警報，不是真的問題。

- **PR review 修正第五輪（Codex，1 個 P2，效能，已修）**：`markAllReadInView` 樂觀套用階段
  對每個 target 各呼叫一次 `commitCountDelta()`，而 `recomputeCounts()` 的成本是 O(目前
  未平倉 entry 數)——對同一批 target 逐篇呼叫，等於把「送出 HTTP 請求之前」這段準備工作做成
  O(n²)；一次全部標已讀命中的文章數大時（例如單一多產來源一次數百篇）這段純本地運算會明顯
  變慢，且發生在任何網路請求送出之前。失敗批次的 rollback 迴圈原本也是逐篇呼叫
  `removeCountDelta()`，有同樣的問題（雖然單一批次上限是 `MARK_ALL_BATCH_SIZE=500`，量體
  較小但邏輯一樣不划算）。修法：新增 `pushCountDelta()`（只建立 entry、不觸發 recompute）
  取代樂觀套用迴圈裡的 `commitCountDelta()`，迴圈結束後才呼叫一次 `recomputeCounts()`；
  新增 `removeCountDeltas()`（批次移除＋只 recompute 一次）取代失敗 rollback 迴圈裡逐篇呼叫
  的 `removeCountDelta()`（該函式本身也改成呼叫 `removeCountDeltas([entry])`，避免重複邏輯）。
  單篇 `markRead`/`markUnread` 沿用的 `commitCountDelta()`/`removeCountDelta()`（單次呼叫即
  recompute）不受影響，因為那本來就只呼叫一次。未新增測試：這是可觀察行為不變、只有內部
  呼叫次數／時機改變的效能修正，既有的大批次（620 篇）正確性測試已經覆蓋修改後的邏輯算出
  同樣正確的結果；要斷言「recompute 只呼叫一次」需要暴露 signal `.set()` 呼叫次數這類內部
  實作細節，不值得為此新增測試耦合，故只以人工推演確認前後行為一致、複雜度改善。
## 階段三十二：PostgREST／database 例外的一致 API error mapping（2026-09-08）

TODO.md「技術與可靠性優化」批次的最後一項：`backend/database.py::get_client()` 是全專案唯一的
Supabase client 建構點，任何 route 呼叫 `.execute()` 時，一旦 postgrest-py 拋出 `APIError`
（unique constraint、check constraint、not-null、RLS 拒絕……），因為沒有任何 handler 接住，
會直接落到 Starlette 的預設行為——沒有 JSON body 的裸 500，前端拿不到任何可用資訊，也無法區分
「資料衝突」跟「真的壞掉了」。目前已知會走到寫入路徑的是 `admin_discovery.py`／
`discovery_candidates.py`／`link_harvest.py` 對 `discovery_targets`／`discovery_candidates`
的 `.insert()`（前兩者的先查後寫在併發下仍有 race window），但這是一致性修法，不是只補這幾個
call site。

- **`backend/errors.py`（新）**：`map_postgrest_error(exc) -> (status_code, body)`。用一份
  SQLSTATE（`23505`／`23503`／`23502`／`23514`／`22P02`／`42501`）＋PostgREST 自己的
  `PGRST116`（`.single()` 零筆或多筆）對照表，分別映射到 409／409／400／400／400／403／404；
  對照不到的一律回通用 `{"detail": "Internal server error"}` 的 500，不把 postgrest-py 的
  `message`／`details`（可能含表名、欄位名、原始 constraint 名稱）洩漏給呼叫端——`code` 用
  `getattr(exc, "code", None)` 讀取而非直接存取屬性，同 `routers/feeds.py` 既有對
  postgrest-py 版本差異的防禦寫法。
- **`backend/main.py`**：`@app.exception_handler(APIError)` 註冊上述映射；映射到 500（代表
  對照表沒認得的錯誤碼）的情況才寫 server-side error log（帶真正的 `code`／`message`），
  409／400／403／404 屬於正常的請求結果，不當成需要留意的操作問題來記。
- **測試**：新增 `tests/test_errors.py`，涵蓋每個對照碼、未知碼、缺 `code`、以及完全沒有
  `code` 屬性的物件（防禦寫法本身）；`tests/test_feeds.py` 新增一個透過真正的 `client` fixture
  打 `GET /api/feeds/{id}`、讓 mock 的 `.execute()` 拋出 `APIError(code="23505")` 的整合測試，
  斷言拿到的是映射後的 409 而不是未接住的例外——證明 handler 真的被 FastAPI 註冊上，不只是
  `map_postgrest_error()` 本身邏輯正確。
- **本 sandbox 的已知限制**：`pip install -r requirements.txt` 被 PyPI 的 network egress
  allowlist 擋下，與先前多個 PR 遇到的限制相同，無法在本機安裝 `postgrest`／`fastapi` 實際跑
  `pytest`。`postgrest.exceptions.APIError` 的建構子簽名（接受一個 dict，讀出
  `message`／`code`／`hint`／`details` 四個屬性，`.get()` 帶預設值故缺鍵不會噴例外）已透過
  postgrest-py 官方文件（readthedocs `api/exceptions.html`）核對過；已用
  `python3 -m py_compile` 與 `ruff check` 過新增／改動的 backend 檔案，交給 CI 的
  `backend.yml` 實際跑過 `pytest` 驗證。
- 對應文件更新：`TODO.md`（「PostgREST／database 例外...API error mapping」項目打勾並補上
  機制說明）。
- **PR review 修正（Codex，兩個 P2）**：
  1. 500 的 log 只記 `code`／`message`，沒有 `details`／`hint`，也沒有帶 traceback——這個
     handler 本來就是取代 Starlette 預設會印出完整 traceback 的行為，只記兩個欄位等於讓真正
     需要調查的未知錯誤反而少了診斷資訊。`main.py` 補上 `hint`／`details`、`request.method`／
     `request.url.path`，並加上 `exc_info=exc` 保留 traceback。
  2. `PGRST116` 不是只代表「零筆」，PostgREST 對 `.single()`／`maybe_single()` 這個 code 同時
     覆蓋零筆跟多筆兩種情況（`maybe_single()` 只吃掉零筆的例外，多筆仍會 raise）——原本無條件
     映射成 404 是錯的，`routers/admin_discovery.py::seed_targets` 的
     `.eq("host", host).maybe_single()` 就是個真的會踩到的案例：migration 006 說明
     `discovery_targets` 對 `host`沒有 unique constraint（只 unique 在 `url`，因為一個 OPML
     目錄可以在同一個 host 貢獻多筆 feed），一個熱門 host 累積多筆是預期中的正常狀態,
     多筆同 host 時把它報成「找不到」還吞掉 log，會讓這類資料狀態異常變得無法被發現。
     修法：`errors.py` 改成讀 `details` 欄位裡的 `"Results contain N rows"`（PostgREST 在零筆
     跟多筆時 `message` 相同，只有 `details` 的筆數不同），`N == 0` 才映射 404，其他情況
     （含 `details` 無法解析或缺漏）一律落回一般 500，交給上面補強的 log 記下來。
     `tests/test_errors.py` 新增零筆／多筆／`details` 無法解析／`details` 缺漏四個案例。
- **PR review 修正第二輪（Codex，P2）**：第一輪的 `_ROWS_IN_DETAILS` 只認得舊版 PostgREST 的
  `"Results contain N rows, application/vnd.pgrst.object+json requires 1 row"`——較新版本
  （`message` 也從「JSON object requested, multiple (or no) rows returned」換成「Cannot
  coerce the result to a single JSON object」）改成單數的 `"The result contains N rows"`，
  原本的規則式（大小寫、`Results`／`result`、`contain`／`contains` 都是精確比對）在這個版本上
  完全不會 match，等於每個零筆的 PGRST116 都會落回一般 500，而不是原本要的 404——跟這個 PR
  想修的問題方向正好相反。修法：`_ROWS_IN_DETAILS` 改成
  `r"results?\s+contains?\s+(\d+)\s+rows?"`（`re.IGNORECASE`），同時吃兩種版本的措辭，不釘死
  在其中一種。`tests/test_errors.py` 的零筆／多筆案例都改成 `@pytest.mark.parametrize`，兩種
  措辭各測一次。
- **PR review 修正第三輪（Codex，P2）**：`42501`（insufficient_privilege）原本映射成 403，
  隱含「這次請求的呼叫者沒有權限」——但 `database.py::get_client()` 是全專案唯一的 client
  建構點，永遠用 `SUPABASE_KEY`（依 TODO.md Phase 0，必須是 service_role key），完全繞過
  RLS，也沒有任何 per-request／per-user 的身分。這個架構下 `42501`唯一可能的成因是
  service_role key 本身或它的 schema／function grant（migration 010）設定錯誤——是部署層級
  的錯誤設定，不是某次請求真的被拒絕；映射成 403 不只講錯故事，還讓它跳過
  `status_code >= 500` 才會走的完整診斷 log，變成一次完全沒有留下痕跡的資料庫權限失效。
  修法：把 `42501` 從 `_STATUS_BY_CODE` 移除，讓它落回一般 500（連同上面已經補好的
  `hint`／`details`／traceback log）。程式碼註解與 `tests/test_errors.py` 同步更新；等
  TODO.md「一般使用者路徑改用 user JWT scoped client」那項真的做了，`42501` 才會重新變成
  一個有意義的 per-request 403，到時要把這個排除規則拿掉。
- **PR review 修正第四輪（Codex，P2）**：`errors.py` 把 `23505`（unique_violation）全域映射成
  409 之後，出現一個沒預料到的跨層副作用——`frontend/src/app/services/admin.ts::report()` 是
  所有 admin API 呼叫共用的錯誤處理，原本把「任何 409」都當成「`approveCandidate` 核准了一個
  已被拒絕的候選」，顯示對應提示。這個假設在這個 PR 之前是對的，因為在此之前只有
  `approve` 端點自己用 `HTTPException(409, ...)` 明確丟過 409；但現在
  `seedTargets()`（`POST /admin/discovery/targets`）這類完全不相關的寫入，一旦與
  `discovery_targets.url` 的 unique constraint 競爭，也會經過新的全域 handler 變成 409，
  卻被 `report()` 誤判成「候選已被拒絕」，讓操作者看到完全對不上的提示。
  修法：`report()` 的 `case 409` 改成只在 `context === '核准失敗'`（`approveCandidate()`
  自己的 context 字串）時才顯示候選專屬訊息，其他 context 一律走既有的
  `${context}：${apiMessage(...)}` 通用衝突訊息；同步更新 `AdminService` 頂部說明 409
  語意的註解。新增 `frontend/src/app/services/admin.spec.ts`（這個服務先前完全沒有測試，
  也是本專案第一個用 `HttpTestingController` 直接測 HttpClient-based service 的案例）：
  `approveCandidate()` 收到 409 時顯示候選訊息、`seedTargets()` 收到 409 時顯示通用衝突訊息
  兩個案例。
- **CI 修正**：這是這個 PR 第一次改到 `frontend/`，第一次真的觸發 `frontend.yml`——結果
  `Build`（跑 `npm test`／Vitest）失敗：`it('...', (done) => { ...; done(); })` 這種
  Jasmine 風格的非同步寫法在這個專案的 Vitest 底下不成立，`done` 收到的是一個
  `TestContext` 物件，不是可呼叫的 callback（`TS2349: This expression is not callable`）。
  修法：拿掉 `done`，改成同步斷言——`HttpTestingController.flush()` 本來就是同一個
  call stack 內同步送達 subscriber，`subscribe({ error: () => {} })` 之後接著呼叫
  `.flush()`，再直接斷言 `toastCalls`，不需要任何 async/await 或 done。
- **CI 修正第二輪**：上面那次推上去之後還是紅——這次是真的執行到請求了，但
  `httpMock.expectOne('/api/admin/discovery/candidates/c1/approve')`（字串形式的
  `expectOne` 是對 `req.url` 做精確字串比對）找不到相符的請求：這個測試環境下
  `HttpClient` 會把相對路徑解析成絕對網址（`http://localhost:8000/api/...`）才送進
  mock backend，不是保持原本的相對路徑。第一個測試的 `expectOne` 因此直接 throw，連帶讓
  `afterEach` 的 `httpMock.verify()` 抓到一個沒被 flush 掉的請求，而第二個測試的
  `beforeEach` 又因為第一個測試中途失敗、沒能讓 TestBed 正常收尾而撞上
  「Cannot configure the test module when the test module has already been
  instantiated」——三個錯誤其實是同一個根因級聯出來的。修法：`expectOne` 改用 predicate
  （`(req) => req.url.endsWith(...)`）比對路徑尾端，不管前面解析出的是相對還是絕對網址；
  `beforeEach` 補上 `TestBed.resetTestingModule()`（`discover.spec.ts` 既有的寫法），
  每個測試都從乾淨的 TestBed 開始，不互相依賴前一個測試有沒有正常收尾。
- **PR review 修正第五輪（Codex，P2）**：`TODO.md` 這一項的完成說明從第一版之後就沒再更新，
  還寫著「RLS 拒絕映射到 403」，但第三輪已經把 `42501` 從映射表移除、改落回通用 500；也完全
  沒提到 `22P02`（invalid_text_representation）跟 `PGRST116` 零筆／多筆的區分。修法：改寫
  說明文字對齊 `errors.py` 最終版的實際行為，避免之後的維護者照著這段過期說明去猜 API
  contract。

## 階段三十三：推薦回饋持久化——喜歡／不喜歡／跳過跨裝置、分層評分權重與推薦理由（2026-09-10）

TODO.md「建議開發批次」第 6 批：猜你喜歡的喜歡／不喜歡／跳過過去完全只存在瀏覽器
localStorage（`RecommendationService`），換裝置或清掉瀏覽器資料就整個消失，`routers/
recommendations.py` 也完全不知道使用者過去的回饋，只能靠呼叫端把 `liked`／`disliked`
（各上限 50 筆）老實帶回來。

- **`backend/migrations/019_recommendation_feedback.sql`（新）**：`driftread.
  user_feed_feedback`，`PRIMARY KEY (user_id, feed_id)`——一個使用者對一個 feed 只留最新
  立場（`feedback_type`：`liked` / `disliked` / `skipped`），不是逐筆事件記錄，同
  `user_feeds`／`user_preferences` 既有的單列狀態模式。RLS owner policy 沿用 migration 010
  最終版寫法（init-plan 化 `auth.uid()` ＋排除 Supabase 匿名登入 session）；migration 010
  已把 `driftread` schema 新表的預設權限 REVOKE 給 `authenticated`，這裡重新明確 GRANT。
- **`backend/routers/me.py`**：`GET`／`PUT`／`DELETE /me/feed-feedback{,/​{feed_id}}`——
  `PUT` upsert（`on_conflict="user_id,feed_id"`），每次都重寫 `created_at`（upsert 只在
  INSERT 時套用欄位 DEFAULT，若不手動更新，改變立場不會更新時間戳，`skipped` 的短期衰減
  就會用到第一次而非最新一次的時間）。
- **`backend/routers/recommendations.py`**：評分從三個等權 flat set（category/tag/language
  membership）改寫成 `Signals` dataclass——依訊號來源分層加權（`_WEIGHT_SUBSCRIBED`／
  `_WEIGHT_LIKED`／`_WEIGHT_BOOKMARKED`／`_WEIGHT_DISLIKED`／`_WEIGHT_SKIPPED`，強度依
  TODO.md「訂閱為強正向；喜歡為正向；收藏為中度正向」排序，外加不喜歡／跳過的負向權重）；
  `_load_signals()` 一次查齊訂閱、`user_preferences`、`user_feed_feedback`、收藏文章所屬來源
  四種來源，`_SKIP_DECAY`（14 天）內的 `skipped` 才排除與降權，過期則完全不影響排序（不是
  永久排除）；`_reason()` 依訂閱／喜歡／收藏優先序，解釋候選命中的 category 或 tag，回應
  model 改成 `RecommendedFeed { feed, reason }`。新增 `skipped` query 參數（獨立於
  `disliked`），供匿名呼叫端沿用既有的純排除語意——沒有伺服器端時間戳可供衰減。順手清掉
  `models.py` 定義了但從未被任何程式碼引用的 `RecommendationRequest`。
- **`frontend/src/app/services/recommendation.ts`**：`liked`／`disliked`／`skipped` 三個
  互斥的本地立場（喜歡一個 feed 會同時把它從不喜歡／跳過裡移除，反之亦然，鏡射伺服器端
  一個 feed 只留一筆的模型）；新增 `skip()`，與 `dislike()`分開——猜你喜歡卡片的「跳過」
  過去誤用 `dislike()`，讓一個輕量的「先不看」被記成 feed 詳情頁「不喜歡」按鈕那種明確負向
  訊號。已登入時三個動作都額外呼叫 `MeService.setFeedFeedback()` 盡力持久化（fire-and-
  forget，失敗不影響本地狀態或拋出）。**刻意不**在登入時把伺服器回饋合併進本地
  localStorage：這三個 key 是瀏覽器層級、不分帳號的，合併等於讓一個帳號的喜好留在瀏覽器裡
  影響下一個登入的帳號或匿名瀏覽——推薦排序本來就不依賴前端本地狀態（`_load_signals` 每次
  重查資料庫），拿掉自動合併不影響跨裝置的推薦品質，只是本地 `isLiked`／`isDisliked` 這類
  UI 提示在其他裝置上不會立刻反映，可接受的落差。
- **`frontend/src/app/components/recommendations`**：`skip()` 改呼叫
  `RecommendationService.skip()`；卡片新增推薦理由（`item.reason`，`null` 時不顯示）；
  回應型別從 `Feed[]` 改為 `RecommendedFeed[]`。
- **測試**：`backend/tests/test_me_feed_feedback.py`（新）涵蓋三個端點；
  `test_me_isolation.py` 補三個跨使用者隔離案例；`test_recommendations.py` 的
  `TestScoreCandidates` 改用 `Signals`，新增 `TestReason` 與已登入者 liked／disliked／
  skipped（含過期衰減）／bookmarked 訊號端到端案例，既有案例改讀新的 `row["feed"][...]`
  回應形狀。`frontend/src/app/services/recommendation.spec.ts`（新，這個 service 先前完全
  沒有測試）涵蓋互斥立場、已登入才持久化、持久化失敗不影響本地狀態、`getRecommendations()`
  的 50 筆上限與「最近的在後」排序。`recommendations.spec.ts` 同步改用 `RecommendedFeed`。
- **本 sandbox 的已知限制**：`pip`／`npm` 的 PyPI／npm registry index 皆被 network egress
  allowlist 擋下，與先前多輪修法相同的既有限制，無法在本機安裝依賴跑真正的
  `pytest`／`vitest`。後端新增的純邏輯（`Signals`／`_score`／`_reason`／skip 衰減時間運算）
  已用一組獨立的 stub 模組（替換 `fastapi`／`supabase`／`auth`／`database`／`models`／
  `rate_limit`，只留 stdlib 依賴）直接 import 並執行實際的 `routers/recommendations.py`
  逐案例驗證；`python3 -m py_compile` 與 `ruff check` 過所有新增／改動的 backend 檔案；
  frontend 用系統 `tsc --ignoreConfig --noResolve` 驗證語法，實際 `npm test`／production
  build 交給 CI 的 `frontend.yml`／`backend.yml` 執行。
- 對應文件更新：`TODO.md`（「推薦回饋持久化」全數打勾並記錄與原規格的兩處刻意偏離：
  不額外複製 `subscribed`／`unsubscribed` 進 `user_feed_feedback`、不做匿名登入自動合併；
  「建議開發批次」第 6 項打勾）、`docs/FEATURES.md`（第 1 節功能總覽、第 2 節推薦邏輯改寫
  為分層權重表、第 3 節新增三個 `/me/feed-feedback*` 端點、第 5 節新增資料表與索引）。
- **PR review 修正（Codex，1 個 P1，2 個 P2，均證實為真）**：
  1. **P1**：`test_final_order_reflects_score_not_quota_origin` 沒有跟著新的分層權重調整——
     `_WEIGHT_LIKED` 的 category +2、tag +1，讓案例裡「category 命中」的 preferred（+2）跟
     「兩個 tag 命中」的 exploratory（1+1=+2）同分，Python `sort()` 的 stable 特性讓同分時
     preferred 留在前面，斷言因此必然失敗。修法：exploratory 改成三個 tag 命中（+3），
     確保跟 preferred 的 +2 有明確差距，同時驗證過即使套用新權重仍然通過。
  2. **P2**：`RecommendationService.getRecommendations()` 先前不論登入與否都把本地
     `liked`／`disliked`／`skipped` 三個陣列當 query string 送出，原意是「已登入者的伺服器端
     回饋跟本地陣列並存也無妨」——但兩個實際後果都是真的洞：(a) 已登入者的 `liked` 訊號會被
     算兩次（一次來自 `_load_signals` 讀到的 persisted 列，一次來自這裡的 query param 重新
     觸發 `add_liked()`），把 `_WEIGHT_LIKED` 的 +2／+1 悄悄疊成 +4／+2；(b) `skipped` 陣列
     完全沒有時間戳可供前端自行判斷是否過期，只要還留在 localStorage 就會永遠隨每次請求送出，
     讓伺服器端 `_SKIP_DECAY`（14 天）刻意設計的「過期後不再排除」失效——query param 路徑
     不管新舊一律無條件加進 `excluded`。修法：`getRecommendations()` 只在**未登入**時才附上
     這三個 query param；已登入者完全信任 `_load_signals` 每次都重新查表這件事，不再有本地
     陣列可以干擾。
  3. **P2**：同一個 feed 上快速連續操作（例如先按喜歡、還沒等回應就按不喜歡）會各自送出一個
     獨立的 `PUT /me/feed-feedback/{feed_id}`，兩個請求之間沒有任何順序保證——如果先送出的
     那個晚到伺服器（不同 replica、網路抖動……），upsert 最終落地的值就會是較舊的操作，
     且不會有任何錯誤讓使用者或程式發現這筆資料其實跟本地狀態不一致，直到下次跨裝置推薦讀到
     錯的立場。修法：`RecommendationService` 用一個 `Map<feedId, Subscription>` 追蹤每個
     feed 目前唯一在途的 persist 請求，發出新請求前先 `unsubscribe()` 掉同一個 feed 舊的
     那個（`HttpClient` 的 unsubscribe 會真的取消底層請求），保證同一個 feed 永遠只有最多
     一個請求在飛，沒有兩個結果可以互相蓋過。
  - **測試**：`recommendation.spec.ts` 新增已登入時不附加 query param、以及同一 feed 連續
    操作會取消前一個請求兩個案例。

## 階段三十四：全文搜尋——文章與 Feed 分開搜尋，PostgreSQL tsvector + GIN index（2026-09-14）

TODO.md P2「全文搜尋」：過去唯一的關鍵字搜尋是 `GET /feeds?search=` 的
`ilike '%keyword%'`（無 index、大表整表掃描），而且完全沒有文章層級的搜尋——`articles`
表從未被搜尋觸及。

- **`backend/migrations/020_full_text_search.sql`（新）**：`articles`／`feeds` 各加一個
  `search_vector`（`GENERATED ALWAYS AS to_tsvector('simple', ...) STORED`）＋GIN index。
  語言設定刻意統一用 `simple`、不做逐語言 stemming：tsvector 的 lexeme 依建立時的
  regconfig 決定，查詢端 tsquery 必須用同一個 regconfig 才能命中，而 Driftread 的搜尋橫跨
  多個 feed／多種語言混在同一次結果裡，沒有單一查詢能同時對「每篇文件各自用不同 regconfig
  建的 tsvector」都選對 config；加上目前有語言偵測的 zh／ja／ko／th／vi 等，Postgres 內建
  本來就沒有對應斷詞字典。統一 `simple` 讓建索引與查詢兩端永遠一致，是「無法可靠斷詞時提供
  可預測 fallback」的落地方式，真正逐語言 stemming 留待之後有需要再做。新增
  `search_articles()`／`search_feeds()` 兩個 DB function：`websearch_to_tsquery` 比對
  `search_vector`，`ts_rank_cd` 排相關度，`(rank, sort_at/created_at, id)` 三欄 keyset
  分頁（同分退回既有日期／id 決勝規則）。三層 CTE 而非單層：`ranked` 只算 rank（GIN index
  篩過的列才算），`paged` 做 cursor 篩選＋排序＋LIMIT，最外層才對已經分頁過的那一頁（≤100
  列）呼叫 `ts_headline`——`ts_headline` 要重新掃過整段文字找命中片段，比 `ts_rank_cd` 貴
  得多，一個熱門關鍵字命中幾千篇文章時不該對每一篇都算一次。`SECURITY INVOKER`、EXECUTE
  只授權 `service_role`，同 `list_reading_stream`／`list_feed_articles` 的既有鎖法。
- **`backend/routers/search.py`（新）**：`GET /search/articles`、`GET /search/feeds`，
  分開端點、分開回應形狀（TODO.md 明列「Feed 名稱／描述搜尋與文章搜尋分開呈現」）。
  `q` 必填（1–200 字）、`language` 可選、cursor 分頁（`limit` 上限 100，同
  `GET /feeds/{feed_id}/articles` 的 400-on-malformed-cursor 慣例）。文章搜尋是公開端點，
  帶有效 token 時每筆回傳呼叫者自己的 `is_read`／`is_bookmarked`（同
  `list_feed_articles`）。
- **`backend/utils.py`**：新增 `encode_rank_cursor`／`decode_rank_cursor`——三欄
  `(rank, marker, id)` 版的 keyset cursor，`rank` 用 `repr()` 而非 `str()` 編碼以保證
  Postgres `real`（float4）經 JSON 往返成 Python `float64` 再送回時精確相等，cursor
  的 rank 比對才不會因為浮點數表示誤差錯過或重複結果。
- **前端**：`components/search`（新，`/search`）——文章／來源兩個分頁，各自獨立的
  cursor 分頁狀態與載入更多；`(query, language)` 相同時切換分頁或重新整理不重打 API。
  命中摘要片段固定走既有 `stripHtml()` 呈現成純文字（不特別高亮 `ts_headline` 加的
  `<b>` 標籤）——避免把 RSS 內容裡本來就可能存在的標籤與 `ts_headline` 自己加的標籤
  混在一起走 `[innerHTML]`，同現有 `stripHtml` 的既有防線一致。`services/search.ts`
  對應兩個端點。公開導覽列（含手機版抽屜選單）加上「搜尋」連結。
- **測試**：`backend/tests/test_search.py`（新，兩個端點的空/帶使用者、language 篩選、
  cursor 編碼/解碼、分頁下一頁存在與否）；`test_utils.py` 補 rank cursor 的編解碼、浮點
  數精確往返、格式錯誤拒絕案例。`frontend/src/app/services/search.spec.ts`（新，
  HttpTestingController 驗證查詢參數）；`components/search/search.spec.ts`（新，提交／
  分頁切換快取／language 變更重打／載入更多／stale response 被新搜尋蓋過／錯誤狀態）。
- 對應文件更新：`TODO.md`（「全文搜尋」四項全數打勾並記錄與規格的刻意偏離：language-aware
  config 統一走 `simple` 而非逐語言 stemming；建議開發批次第 7 項更新為全文搜尋已完成，
  資料夾管理仍未開始）、`docs/FEATURES.md`（第 1 節功能總覽、第 3 節新增 Search 端點、
  第 4 節新增 `/search` 路由、第 5 節新增 `search_vector` 欄位／`search_articles`／
  `search_feeds`／GIN index）。
- **本 sandbox 的已知限制**：與先前多輪修法相同，`pip`／`npm` 的 PyPI／npm registry
  index 皆被 network egress allowlist 擋下，無法在本機安裝依賴跑真正的
  `pytest`／`ng test`。已用 `python3 -m py_compile` 驗證所有新增／改動 backend 檔案的
  語法，`flake8` 驗證新增程式碼無 lint 問題（既有 `models.py` 一個 `Any` 未使用的既有
  問題與本次改動無關，未動它）；frontend 新增檔案已對照既有元件（`feed-detail.ts`、
  `bookmarks.spec.ts`、`feed-list.html` 的 `ngModel`／`ngSubmit` 慣例）逐行核對語法與
  慣例一致性，實際 `npm test`／production build 交給 CI 的 `backend.yml`／
  `frontend.yml` 執行。
- **PR review 修正（Codex，九輪，1 個 P1，18 個 P2，均證實為真）**：
  1. **P1**：`articles.content` 沒有欄位層級長度上限（只有抓取階段整個 feed 下載量的
     5 MiB 上限），而 Postgres 的 tsvector 序列化後有約 1 MiB 的大小限制——單篇超大文章
     會讓 `search_vector` 這個 generated column 的計算直接丟出
     `string is too long for tsvector`，新文章寫入失敗，或者這個 migration 替既有資料
     回填該欄位時整個 migration 失敗、擋住後端啟動。修法：新增
     `driftread.bounded_search_text(text)`（`IMMUTABLE` SQL function，`left(text, 100000)`），
     所有送進 `to_tsvector`／`ts_headline` 的文字都先經過它——100,000 字元就算全是
     4-byte UTF-8 字元也只有 400 KB，遠低於 1 MiB 上限，且沒有真實搜尋情境需要比這更
     後面的內文才能命中。
  2. **P2**：`search_articles` 的命中摘要片段原本不論比對命中的位置在哪，一律固定取
     `summary`（沒有摘要才退回 `content`）餵給 `ts_headline`——當一篇文章有非空
     `summary` 但查詢其實只命中 `content` 時，回傳的片段會是一段完全沒有標記到關鍵字的
     `summary` 開頭，與端點承諾的「命中摘要片段」不符。修法：分別檢查
     `summary`／`content` 各自的（同樣經過 `bounded_search_text` 界限的）tsvector
     是否命中查詢，取真正命中的那一個餵給 `ts_headline`；兩者都沒命中（純粹命中
     title／author）才退回原本的預設值——`ts_headline` 對沒有命中的文字不會報錯，只是
     顯示一段沒有標記的開頭摘錄。
  3. **P2**：`GET /search/articles`／`GET /search/feeds` 兩個公開端點原本沒有掛任何
     rate limit——與 `GET /recommendations`（同樣是公開、每次呼叫都有真實 DB 排序工作）
     先前的既有洞一樣：呼叫端可以不斷送出熱門關鍵字查詢，讓資料庫對每次查詢的所有命中
     排序、再對分頁後的結果算 `ts_headline`，造成可避免的 CPU 耗用。修法：兩個端點各自
     掛 `Depends(rate_limit("search_articles"))`／`Depends(rate_limit("search_feeds"))`，
     沿用 `/discover`／`/discover/import`／`/recommendations` 同一套「每個 client IP
     每端點 20 requests / 60 秒」預設值與獨立配額（bucket 用不同 `name`，互不影響）。
  4. **P2**：`decode_rank_cursor` 用 Python `float()` 解析 cursor 裡的 rank 部分，但
     `float()` 也會無錯誤地解析 `'nan'`／`'inf'`／`'-inf'` 這類字串——一個刻意構造、
     base64 格式正確但帶有非有限值的 cursor 會通過解碼，把 NaN／Inf 當成 RPC 的
     `real` 參數送進資料庫，依 JSON 序列化／PostgREST 處理方式不同，結果可能是跳出
     `ValueError` 判斷路徑回傳非預期的 500、或是產生不合理的分頁結果，而不是文件承諾的
     400。修法：解碼後多一道 `math.isfinite(rank)` 檢查，非有限值一律視同格式錯誤的
     cursor。
  5. **P2**（第二輪 review）：`articles.content` 刻意保留原始 HTML（供 reader 頁
     `[innerHTML]` 呈現用），`title`／`summary`／`author` 則已經是
     `rss_parser.py::_plain_text()` 產生的純文字——`search_vector` 這個 generated column
     卻把 `content` 原封不動串進 `to_tsvector`，讓 tag 名稱、屬性、class、連結網址這些
     呈現用的標記語法本身變成可搜尋詞彙：搜尋 `href` 或某個 CSS class 名稱會命中完全不
     相關的文章，重複出現的樣板標記也會稀釋 `ts_rank_cd` 的相關度排序。修法：新增
     `driftread.strip_html_for_search(text)`（`IMMUTABLE` SQL function，
     `regexp_replace(text, '<[^>]*>', ' ', 'g')`——每個標籤換成空白而不是直接砍掉，避免
     `"...句尾</p><p>下一句"` 少了空白黏成一個詞），`search_vector` 的 generated column
     與 `search_articles` 命中摘要片段的 `content` 分支都先過這道處理，不追求跟
     `_plain_text()` 完全一致的還原精確度——這裡只是搜尋索引前處理，不是要呈現給讀者看的
     文字。
  6. **P2**（第二輪 review）：`routers/articles.py::get_article`（`GET /articles/{id}`）
     原本用 `.select("*")` 查單篇文章，migration 020 替 `articles` 加上
     `search_vector` 後，這個萬用字元查詢會連帶把這個對長文章可能有數十到數百 KB 的
     generated tsvector 從 PostgREST 撈回並序列化，即使 `Article` 回應 model 從未使用它
     ——每次讀一篇文章都白白多傳一份幾乎跟全文一樣大的資料。修法：改成明確欄位清單
     `id,feed_id,title,url,summary,content,author,published_at,fetched_at`，不含
     `search_vector`。
  7. **P2**（第三輪 review）：`search_articles` 的 JOIN 只用 `f.id = a.feed_id` 取
     `feed_title`，從未檢查 `f.archived_at`——已封存來源的文章仍然完全可以透過這個新的
     公開搜尋端點被搜到／列出，與封存流程本身給操作者的承諾（`admin-feeds.ts` 封存
     確認對話框：「封存後這個來源不再出現在前台」）矛盾，也跟 `search_feeds` 早已排除
     已封存來源的既有行為不一致。修法：`ranked` CTE 的 `WHERE` 加上
     `f.archived_at IS NULL`，同 `search_feeds`／`GET /feeds` 既有行為。
  8. **P2**（第三輪 review）：`routers/feeds.py` 的 `list_feeds`（`GET /feeds`，一次最多
     100 筆）與 `get_feed`（`GET /feeds/{id}`）都用 `.select("*")`，migration 020 替
     `feeds` 加上 `search_vector` 後，這個最多索引 100,000 字元 description 的 generated
     tsvector 也會被撈回——`Feed` 回應 model 從未用到它，分頁列表的浪費隨頁面大小疊加。
     修法：改成同一份明確欄位清單 `_FEED_COLUMNS`（`Feed` model 的全部欄位，不含
     `search_vector`），兩處呼叫共用。`routers/admin.py`／
     `services/discovery_candidates.py` 還有其他 `feeds.select("*")` 既有用法，這次
     review 沒有指出（後台操作端點，不是高流量的公開路徑）——範圍留給之後真的需要時再
     處理，不在這個 PR 裡順手清掉。
  9. **P2**（第四輪 review）：第二輪加的 `strip_html_for_search()`（`<[^>]*>` → 空白）
     在屬性值裡出現字面 `>` 時會提早停在那個 `>`，不是標籤真正的收尾——例如
     `<a title="2 > 1" href="https://example.com">text</a>`，會把
     ` 1" href="https://example.com">` 這段原封不動留在索引／headline 文字裡，搜尋還是
     能命中看不見的屬性值或連結網址，等於沒真正解掉第二輪那個發現。修法：正規表示式改用
     跟 `frontend/src/app/shared/html.ts` 的 `ATTRS`／`TAG_RE` 同一套 quote-aware 邏輯
     （翻譯成 Postgres 的 regex 語法）——標籤的屬性部分改成
     `(?:[^>"']|"[^"]*"|'[^']*')*`：不是屬性值的字元逐一比對，遇到雙引號／單引號包起來的
     一整段（不論裡面有沒有 `>`）當成一個單位跳過，只有真的沒有配對引號的異常標籤才退回
     原本天真的 `[^>]*`。
  10. **P2**（第四輪 review）：`components/search/search.ts` 從未對
      `AuthService.session()` 做任何反應——`AuthService` 是非同步還原已登入 session
      的（同 `feed-detail.ts`／`reading-stream.ts`／`bookmarks.ts` 已經處理過的同一類
      問題），如果讀者在 session 還原完成前就送出搜尋，那次請求會是匿名的，
      `is_read`／`is_bookmarked` 全部回傳 false；`searchKey` 只看 query／language，不含
      使用者身分，session 還原後重新送出同一個查詢會被當成「已經載入過」直接跳過，讀者
      會一直卡在匿名結果上。修法：跟其餘元件同一套模式——建構子裡加一個
      `effect()` 追蹤 `auth.session()?.user?.id`，身分改變時清掉 `articleLoadedForKey`
      這個快取鍵；若當下就在文章分頁且有進行中的查詢就立刻重新載入，若在來源分頁則只
      invalidate，等切回文章分頁時 `loadActiveTab()` 既有的快取鍵比對自然會重新載入
      （來源搜尋本來就不帶使用者狀態，不需要在身分改變當下就重打）。
  11. **P2**（第五輪 review）：`strip_html_for_search()` 加了 quote-aware 標籤比對後，
      仍然只移除標籤本身，`<script>`／`<style>` 元素的「內容」（JS 程式碼、CSS
      selector／屬性值）沒有被當成標籤，原封不動變成一般可見文字留在索引裡——即使前端
      畫面上完全不會顯示這些內容。`rss_parser.py::_plain_text()` 早就用 `_DROP_WHOLE_RE`
      處理過同一個問題（先整個元素含內容砍掉，再處理一般標籤）。修法：
      `strip_html_for_search()` 改成兩段 `regexp_replace`——先用跟 `_DROP_WHOLE_RE` 對應
      的 pattern（quote-aware 屬性、大小寫不分、`\1` 反向參照比對收尾標籤）整個砍掉
      `<script>`／`<style>` 元素（標籤＋內容），再套用既有的一般標籤比對。
  12. **P2**（第五輪 review）：`feeds.description` 也不保證是純文字——
      `rss_parser.py::_text()`（channel 層級 description 用的就是它）只是回傳元素解碼後的
      文字內容，不像 `_plain_text()` 會處理成純文字；發佈者若在 `<description>` 裡跳脫
      HTML，XML unescape 之後就是貨真價實的 `<...>` 標記，這個 generated column 卻原封
      不動索引——搜尋 `href`／`class` 或某個網址一樣能命中一個描述根本沒顯示這些字的
      feed。修法：`feeds.search_vector` 的 generated column 與 `search_feeds` 的
      `ts_headline` 呼叫，`description` 都先過 `strip_html_for_search()`，跟
      `articles.content` 同一套處理與理由。
  13. **P2**（第五輪 review）：`components/search/search.html` 只在 `published_at` 存在時
      才顯示日期，但 model 上 `published_at` 是 nullable、`fetched_at` 永遠非空（元件自己
      的測試 fixture 也是這樣寫的），沒解析出發佈日期的文章因此完全不顯示日期，也跟後端
      `COALESCE(published_at, fetched_at)` 的排序邏輯不一致——結果少了「顯示日期」這個
      端點本來就承諾的欄位。修法：改成 `(row.article.published_at ?? row.article.fetched_at)`，
      一律顯示，退回抓取時間。
  - **測試**：`test_utils.py` 新增四種非有限值（`nan`／`inf`／`-inf`／`Infinity`）的
    拒絕案例；`test_articles.py`／`test_feeds.py` 各新增案例斷言對應端點的
    `.select(...)` 參數是明確欄位清單、不是 `"*"`；`components/search/search.spec.ts`
    新增三個案例（session 還原後重新載入文章結果、尚未送出查詢時 session 還原不觸發任何
    請求、身分改變當下人在來源分頁不立即重打但切回文章分頁會重打），做法同
    `feed-detail.spec.ts`／`my-feeds.spec.ts` 既有對 session 訊號的測法（`session.set(...)`
    後在同一個 fixture 上再呼叫一次 `detectChanges()` 讓元件自己的 `effect()` 真正跑一次
    ——`TestBed.flushEffects()` 是給注入來源為 `TestBed.inject()` 的 effect，不是給
    component fixture 的）。P2-9／11／12（SQL 正規表示式）與 P1（截斷）都是這個 sandbox
    無法連上真正 Postgres 執行的 SQL 邏輯，同本節前段記錄的既有限制，靠人工覆核；P2-13
    是純樣板改動，既有 spec 沒有對 DOM 渲染斷言的慣例（都是狀態／行為層級），跟隨這個
    檔案既有風格沒有另外補 DOM 測試；rate limit dependency 不影響既有測試——
    `conftest.py` 的 `_reset_rate_limits` 每個測試前都會清空命中紀錄，且每個測試案例
    只送一到兩次請求。
  14. **P2**（第六輪 review）：第五輪加的 `<(script|style)\b...` 這個 pattern 裡的
      `\b`，在 PostgreSQL 的 ARE（Advanced Regular Expression）語法裡是「backspace」
      這個字元跳脫（同 `\a`／`\f`／`\n`／`\r`／`\t` 那組 character-entry escape），**不是**
      Perl／PCRE 那種 word-boundary constraint——這是這兩種 regex 方言一個真實存在、
      容易誤踩的差異。結果是這個 pattern 幾乎永遠不會命中（因為真實輸入裡 "script" 或
      "style" 後面幾乎不會剛好接一個字面 backspace 字元），第五輪那個「先整個砍掉
      script／style 元素」的修法因此完全是 no-op，退回成只砍標籤、留下內容的舊行為，
      沒有真的解掉問題。修法：換成 Postgres 自己 regex 方言裡真正的 word-boundary
      constraint escape `\y`（對應 word-start／word-end 分別是 `\m`／`\M`），讓
      `<script>`／`<script ...>` 命中，但 `<scriptx>` 這種更長的標籤名稱不會被誤砍。
  - **測試**：這一項是 SQL 正規表示式本身的方言差異，這個 sandbox 無法連上真正
    Postgres 執行驗證，同本節前段記錄的既有限制，靠人工覆核（對照 PostgreSQL 官方文件
    的 constraint escape／character-entry escape 對照表逐字核對）。
  15. **P2**（第七輪 review）：`components/search/search.ts` 的 `searchKey` 用一個位元組
      分隔 `activeQuery`／`language` 兩段組成快取鍵，原意是想用一個「查詢文字與語言代碼都
      不會出現」的分隔字元，但寫進原始碼時該位元組是直接以字面 NUL byte（`\x00`）存在
      檔案裡，不是文字跳脫序列——結果 `git diff --numstat` 之類的工具把整個檔案判定成
      binary，正常的逐行 diff／merge 都失效。修法：改成 TypeScript 範本字面值裡的合法
      跳脫序列 `\u0000`（反斜線＋u0000 六個字元），執行期仍會被直譯成同一個 NUL
      字元、行為完全不變，但原始碼檔案本身變回純文字。
  - **測試**：純位元組層級的原始碼修正，行為不變（同一個 NUL 分隔字元，只是換成合法的
    文字跳脫序列表示），既有 `search.spec.ts` 的快取鍵相關案例（分頁切換快取、language
    變更重打）不需要跟著改。
  16. **P2**（第八輪 review）：`services/articles.py::upsert_articles()`（排程刷新每個
      feed 都會呼叫，每批最多 200 篇）原本用預設的 `returning="representation"`，
      migration 020 替 `articles` 加上 `search_vector` 後，這個回應會連同每篇文章可能
      數十到數百 KB 的 generated tsvector 一起序列化回傳，但呼叫端只用
      `len(result.data)` 算筆數，完全沒用到內容本身。修法：改成
      `returning="minimal"`（`Prefer: return=minimal`，完全不回傳列內容），筆數改用
      `len(chunk)` 直接算——同一批次內的 `(feed_id, url)` 已在呼叫前用 dict 去重過，
      upsert 在沒有 `ignore_duplicates` 的情況下一定是每筆要嘛新增要嘛更新，不會有
      「送出去但沒被回應提到」的列，所以 `len(chunk)` 跟原本 `len(result.data)`
      在數學上恆等，不是近似值。
  17. **P2**（第八輪 review）：`strip_html_for_search()` 只拆標籤，不處理標籤拆完後
      留在文字裡的 HTML entity（`&eacute;`／`&nbsp;` 之類）——escaped markup 常見這種
      情況：發佈者把自己的 HTML 原文用 `&amp;` 跳脫過一次才塞進 XML，XML parser 只解一次
      `&amp;`，裡面本來就是 entity 的部分（例如 `&eacute;`）解完後還是原封不動的文字。
      結果搜尋「Café」這個可見字命中不了索引裡的「Caf&eacute;」，「eacute」這個
      entity 名稱本身反而變成一個看不見卻能被搜到的詞。另外，把每個標籤都換成空白也會
      拆散行內標記中間的詞——`micro<em>soft</em>` 畫面上是一個字「microsoft」，索引
      卻變成兩個獨立詞「micro」「soft」，讀者搜畫面上看到的字反而找不到；CJK 文字中間
      被行內標籤（例如 `<a>`、`<em>`）包住幾個字時問題更明顯，因為中文本來就沒有空白
      斷詞，硬插一個空白會把一段連續文字切成兩截。修法：`strip_html_for_search()`
      改成區分「區塊標籤」（`p`／`li`／`div`／`h1`-`h6`／`br` 等，同
      `rss_parser.py::_BLOCK_TAGS`／`frontend/src/app/shared/html.ts::BLOCK_TAGS`
      原封不動照抄）換成空白，其餘所有標籤（含行內標記與註解）直接移除、不留分隔——
      跟這個專案既有的 `_plain_text()`／`stripHtml()` 同一套判斷依據；並在標籤全部
      拆完之後（避免把「示範用的逃脫標籤文字」不小心解回真標籤又被上一步吃掉）多一道
      只處理 XML 預定義的 `&amp;`／`&lt;`／`&gt;` 加上極常見的 `&nbsp;` 這四種明確、
      無歧義的 entity 解碼（`&amp;` 放最後解，避免雙重跳脫的 `&amp;lt;` 被連環解成
      `<`）。刻意不嘗試完整的 HTML 具名 entity 對照表（上千筆，`&eacute;` 這種）——
      在 SQL 裡手刻這個正是這個專案自己的 `frontend/src/app/shared/html.ts`
      的 `decodeEntities()` 已經寫下教訓、明確不要做的事（「每次 review 都會冒出另一種
      跟 Python `html.unescape()` 不一致的地方」）；真正完整的修法該放在 Python、在
      抓取階段做，同 `backend/backfill.py` 呼叫真的解析器的作法，不是在這裡養一張
      越補越大的 regex 表。
  - **測試**：第 16 項是 `services/articles.py`，`tests/test_articles_service.py`
    的 `_FakeTable.upsert` 補上 `returning` 參數並斷言等於 `"minimal"`，既有案例的
    行為不變（fake 本來 `result.data` 存的就是傳入的 chunk 本身，跟改用 `len(chunk)`
    在數學上等價）。第 17 項是 SQL 正規表示式，這個 sandbox 無法連上真正 Postgres
    執行驗證，同本節前段記錄的既有限制，靠人工逐字元核對（block-tag 清單對照
    `rss_parser.py::_BLOCK_TAGS` 逐一比對、quote-aware pattern 沿用第四輪已驗證過的
    escaping 方式、entity 解碼順序手動追蹤三個範例：`&amp;lt;`、`&lt;script&gt;`
    示範文字、一般 `&amp;`／`&nbsp;`）。
  18. **P2**（第九輪 review）：`strip_html_for_search()` 內部好幾道
      `regexp_replace`，但 `articles.content` 沒有欄位層級長度上限（理論上可接近
      抓取階段整個 feed 下載量的 5 MiB 上限）——`bounded_search_text()` 原本是等
      `strip_html_for_search()` 跑完、串接完 title／summary／author 之後才對「最終
      串接結果」做長度限制，這代表 regex 本身是對著未經界限的原始 HTML 掃描，掃描
      成本沒有上限。`search_articles` 的命中摘要片段那段 CASE 更是把
      `strip_html_for_search(content)` 對同一篇文章重複呼叫兩次（WHEN 判斷命中一次、
      THEN 分支再算一次），而這個端點單次查詢最多回傳 100 篇文章——等於單次公開搜尋
      請求最壞情況要對未界限的原始內文跑到 200 次多階段 regex 掃描，是可避免的
      DB CPU／延遲尖峰。修法：`bounded_search_text()` 改成在 `strip_html_for_search()`
      **之前**先跑（截斷是 O(1) 的 `left()`，先做完全不影響後面 regex 的正確性——
      stripping 的每一種取代都只會讓字串變短或不變，先界限原始輸入，出來的結果保證
      一樣有界限，不需要再包一層）；`search_articles` 額外把去 HTML 這道計算搬進
      `paged` 之後新增的第四層 CTE `enriched`（`SELECT *, strip_html_for_search(...)
      AS stripped_content FROM paged`），同一列只算一次、WHEN／THEN 兩處共用同一個
      欄位——刻意不放進 `ranked` 或 `paged` 本身，這兩層是在 LIMIT 篩選**之前**跑過
      所有命中的列，把貴的逐列文字處理放在那裡，就是這整個三（現在四）層 CTE 設計
      一開始想避免的事：只有真的會回傳給呼叫端的那一頁才該付這個成本。`search_feeds`
      的 `description` 只有單一候選欄位、單一呼叫點，沒有「重複算兩次」的問題，但
      一樣改成「先界限再處理」而不是「先處理再界限」。
  - **測試**：SQL 查詢結構調整，這個 sandbox 無法連上真正 Postgres 執行驗證，同本節
    前段記錄的既有限制，靠人工核對：CTE 執行順序（`enriched` 直接 `FROM paged`，
    `paged` 自己的 `ORDER BY ... LIMIT` 保證只有已經篩選過的那一頁會流進
    `enriched`）、`bounded_search_text` 與 `strip_html_for_search` 呼叫順序在六個
    呼叫點（articles／feeds 的 generated column、`search_articles` 的 WHEN／THEN／
    ELSE 三處、`search_feeds` 的 ts_headline）全部一致改成「先界限再去 HTML」。
  19. **P2**（第十輪 review）：第十八項把 `bounded_search_text()` 搬到
      `strip_html_for_search()` 之前執行後，帶出一個新的邊界案例——當一篇文章原始
      `content` 超過 100,000 字元、且截斷點剛好落在一個 `<script>`／`<style>` 元素
      中間時，截斷會把該元素的收尾標籤（`</script>`／`</style>`）一併切掉。第五、六輪
      加的「整個元素連內容砍掉」那道 `regexp_replace` 要求比對到 `</\1\s*>` 收尾標籤
      才會命中，收尾標籤被截斷後這道 pattern 就不會命中這個（截斷後）未閉合的元素，
      後面一般標籤那道 pass 只會砍掉殘留的開頭 `<script ...>` 標籤本身，把 JS／CSS
      內容原封不動當成一般可見文字留在索引裡——跟第五、六輪想解的問題（script／style
      內容不該被索引）本質相同，只是換了個從「截斷」帶出來的新誘因。修法：
      `<(script|style)...>.*?</\1\s*>` 的收尾比對改成
      `<(script|style)...>.*?(?:</\1\s*>|$)`——用 alternation 多接受「字串結尾」當成
      收尾點之一，`.*?` 是 lazy quantifier，仍然優先比對到真正的收尾標籤，只有真的
      遇不到收尾標籤（截斷造成）才會一路吃到字串結尾，把截斷後的殘缺元素整個砍掉而不是
      留下沒加保護的內容。
  20. **P2**（第十輪 review）：`services/feed_refresh.py::refresh_one()` 三處
      `db.table("feeds").update(...)` 呼叫（抓取失敗、304 not modified、成功更新這三條
      路徑）都沒有使用回應內容，但都沿用 postgrest-py `update()` 預設的
      `returning="representation"`——migration 020 替 `feeds` 加上 `search_vector`
      後，排程刷新（預設每批 50 個 feed）每次更新都會連同該 feed 可能高達數十 KB 的
      generated tsvector 一起序列化回傳，同第十六項 `services/articles.py::upsert_articles()`
      已經解掉的同一類浪費，只是這次是 feed 更新而非 article upsert，那次的修法沒有覆蓋
      到這裡。修法：三處都加上 `returning="minimal"`，行為與回傳值皆不變（呼叫端本來就
      只依賴 side effect，不讀取任何回應內容）。
  - **測試**：第 19 項是 SQL 正規表示式，這個 sandbox 無法連上真正 Postgres 執行驗證，
    同本節前段記錄的既有限制，靠人工核對 lazy quantifier 搭配 alternation 的比對順序
    （逐字元手動追蹤一個刻意截斷在 `<script>` 中間的範例字串）。第 20 項是
    `backend/tests/test_feed_refresh.py` 的 `_FakeTable`／`_FeedsProxy.update()`
    fake 補上 `returning` 參數（原本只接受單一 payload 位置參數，呼叫端傳
    `returning="minimal"` 關鍵字參數會直接拋 `TypeError`），三個既有 `update()` 呼叫點
    的既有測試案例（`db.feed_updates` 斷言）不需要跟著改——fake 記錄的仍然是同一個
    `payload`，`returning` 只是額外接受、不影響任何既有斷言。

## 階段三十四：偏好設定與推薦回饋的前端整合測試，並修掉訂閱回應晚到多吃一張卡的 race（2026-09-15）

TODO.md「Frontend 與 CI」最後一個未完成的測試項目：訂閱 CTA 與我的閱讀流早就補過，偏好設定
與推薦回饋當時寫「都還沒實作，測試無從補起」，但 PR #44（偏好設定 UI）與 PR #58（推薦回饋
持久化）之後這句話已經過期——功能都在，只是那條清單沒回頭補測試。

- **`frontend/src/app/components/preferences/preferences.spec.ts`**：在 PR #44 既有的載入／
  toggle／儲存成功失敗／stale generation 案例之外新增五個案例——(1) 送出的 payload 是 toggle
  後的選擇而非載入時的（`PUT /me/preferences` 整批覆寫兩個陣列，取消勾選的分類只有在 payload
  裡真的不見才會消失，先前的測試完全沒有斷言過送出去的內容）；(2) toggle chip 在按下儲存前
  不送任何請求——chip 純粹是本地意圖，沒有 per-toggle 請求需要 debounce 或 de-dup，唯一的 PUT
  發生在 `save()`，連點由 `[disabled]="saving()"` 擋住；(3) 儲存失敗不清掉選擇，重試送出同一
  份 payload（這裡沒有「伺服器端舊值」可以回滾，清掉只會讓使用者重選一次）；(4) 只有
  `GET /feeds/categories`／`GET /feeds/languages` 失敗時表單仍可用，不進 `error` 狀態——代價
  只有兩個 toast 與空 chip 清單，與「`GET /me/preferences` 失敗必須整個表單藏起來」的既有
  設計相對照；(5) 三個讀取全部 settle 前維持 `loading`，避免先閃出一個空選擇的表單，而那個
  表單的儲存按鈕會把畫面上的空選擇 PUT 出去。`setup()` 改成會記錄每次 `updatePreferences()`
  的 payload，並可覆寫兩個 catalog observable。
- **`frontend/src/app/components/recommendations/recommendations.spec.ts`**：新增
  `Recommendations feedback actions` 區塊。回饋持久化本身（`PUT /me/feed-feedback/{feed_id}`、
  未登入不送、失敗不回滾本地狀態、同一 feed 只保留一個在途請求）是 `RecommendationService`
  的責任，`services/recommendation.spec.ts` 已經逐項覆蓋，這裡刻意不重複；補的是元件自己的
  接線：喜歡／跳過各自回報哪個 feed id、卡片是否立刻前進（回饋是 best-effort，不該等回應）、
  `已喜歡 N 個` 讀的是 service 的 `liked()` 而不是元件私有計數，以及回饋與在途訂閱之間的互動。
  `SubscriptionService` 的 mock 改成把成功／失敗回呼留著由測試決定何時觸發，才有辦法測到
  「訂閱還在途中」這個視窗。
- **`frontend/src/app/components/recommendations/recommendations.ts`（真實 bug 修正）**：寫上面
  最後一個案例時發現的 race——`subscribe()` 的成功回呼無條件呼叫 `next()`，但訂閱在途期間只有
  訂閱 按鈕自己被 `isSubscribePending` 停用，跳過／喜歡 兩顆按鈕仍然可以點。讀者按下 訂閱 後
  不等回應直接按 跳過，牌堆會先前進一格，訂閱回應到達時再前進一格——`next()` 只進不退，中間
  那張推薦就這樣沒被看過就從這批消失（`loadMore()` 重抓一批也一樣，回應晚到會吃掉新牌堆的第
  一張）。修法最小：成功回呼只在「畫面上仍是當初按下訂閱的那張卡」（`this.current?.feed.id
  === feed.id`）時才 `next()`，`rec.like()` 這個比 skip 更強的正向訊號照樣記錄。對應測試三個：
  跳過之後訂閱成功不再前進、讀者沒動時訂閱成功照常前進、跳過之後訂閱失敗同樣不動牌堆。
- **PR review 修正（Codex，P2，證實為真）**：上面那個修法只比對 `feed.id`，漏了一種情況——
  訂閱還在途中時，讀者點了「再推薦一批」（`loadMore()`），新抓回來的牌堆剛好在同一位置又出現
  同一個 feed（該訂閱還沒 commit，伺服器沒有理由排除它），成功回呼比對 `feed.id` 會誤判成
  「還在原本那張卡」，把這張其實從未被看過的新卡片也吃掉。修法：改成連牌堆本身的陣列參照一起
  比對（`this.feeds() === deck`）——`loadMore()` 每次呼叫都會 `set()` 一個全新陣列，即使內容
  剛好重複，參照必然不同，藉此區分「同一張卡還沒換」與「牌堆已經整批換過，只是恰好重複」。
  新增一個案例：`loadMore()` 换出的新牌堆第一張恰好也是 feed-1 時，舊的訂閱回應到達不會吃掉它。
- **本 sandbox 的已知限制**：`registry.npmjs.org` 依舊被 network egress allowlist 擋下（這次
  連 metadata 都是 403，`npm ci` 卡在 `@angular/cli` 的間接依賴 `zod-to-json-schema`），
  `node_modules` 裝不起來，因此本機跑不了 `vitest`、`ng build`，連 `prettier --check` 都跑不
  起來（`npx prettier` 同樣 403）。已用系統 `tsc 6.0.2`（`--ignoreConfig --noResolve`）確認三
  個改動檔案沒有語法或型別結構問題（只剩因為 `--noResolve` 而必然出現的 TS2307／TS2304 模組
  與 global 缺失），並手動照專案 `.prettierrc`（printWidth 100、single quote）對齊格式、確認
  沒有超寬行。實際 `npm test`／production build 交給 CI 的 `frontend.yml` 驗證。
- 對應文件更新：`TODO.md`（「為訂閱 CTA、我的閱讀流、偏好設定與推薦回饋補前端整合測試」四項
  到齊後打勾並改寫過期的括號說明，另記錄上面那個 race 的修正）。

## 階段三十五：支援每個來源的使用者自訂名稱（2026-09-16）

TODO.md P2「資料夾與來源控制」的一項：訂閱清單裡的名稱一律來自 `feeds.title`，來源自己取的
名字不一定合用，而 `feeds.title` 是所有訂閱者與公開目錄共用的，改寫它會影響其他人。

- **`backend/migrations/021_user_feed_custom_title.sql`**：`driftread.user_feeds` 新增
  `custom_title TEXT`（nullable，`CHECK` 長度上限 200，同 `discovery_candidates.title` 的
  既有上限）。NULL 代表「沿用 feed 原本的 title」；空白字串一律在寫入前正規化成 NULL（見下），
  不讓「沒有自訂名稱」與「自訂名稱是空字串」變成兩種要分別處理的狀態。
- **`backend/models.py`**：新增 `SubscribedFeed`（`Feed` 加一個 `custom_title` 欄位，只用在
  `GET /me/feeds`——這是訂閱關係的屬性，不是 feed 本身的，故意不放進 `Feed` 本體污染其他讀取
  路徑）與 `SubscriptionUpdate`（`custom_title: str | None`，上限 200）。
- **`backend/routers/me.py`**：`list_subscriptions` 改回傳 `list[SubscribedFeed]`，一併
  select `user_feeds.custom_title`；新增 `PATCH /me/feeds/{feed_id}`
  （`update_subscription`）：先查訂閱是否存在（未訂閱回 404，同 `routers/admin.py` 既有的
  「先查存在再動作」寫法），空白／純空白字串在這裡（不是在 model）正規化成 NULL 再寫入。
- **前端**：`models.ts` 新增 `SubscribedFeed`；`MeService.listSubscriptions()` 回傳型別改為
  `SubscribedFeed[]`，新增 `updateSubscription()`。`components/my-feeds`：卡片標題改顯示
  `custom_title || title`，有自訂名稱時原本的目錄標題以「原名：」小字保留在旁，不會被完全
  蓋掉；新增「重新命名」原地編輯（`Enter` 送出、`Esc` 取消），儲存時裁剪前後空白、空白值清除
  自訂名稱，失敗不影響已顯示的清單並跳 toast。
- **測試**：`backend/tests/test_me.py` 四個案例（`GET /me/feeds` 回傳 `custom_title`、
  設定會裁剪空白、空白值清除為 NULL、未訂閱回 404 且不呼叫 `update`）；
  `backend/tests/test_me_isolation.py` 補一個跨使用者隔離案例（同其餘 `/me/*` 端點的既有
  寫法：兩個使用者各呼叫一次，斷言送進 `update().eq("user_id", ...)` 的值互不相同）；
  `frontend/src/app/components/my-feeds/my-feeds.spec.ts` 四個案例（成功設定並裁剪、空白值
  清除、失敗不動清單並跳一次 toast、取消編輯不送請求），既有的 `Feed`／`Feed[]` 測試 fixture
  與型別註記一併改成 `SubscribedFeed`／`SubscribedFeed[]`，配合 `MeService` 回傳型別的變更。
- **本 sandbox 的已知限制**（同 PR #58／#59 記錄的既有情況）：`pypi.org`／
  `registry.npmjs.org` 這次連 metadata 都被 network egress 政策擋下 403，backend 的
  `pip install`、frontend 的 `npm install` 都裝不起來，本機跑不了 `pytest`／`vitest`／
  `ng build`。backend 三個改動檔案已用 `python3 -m py_compile` 確認語法正確；frontend 四個
  改動檔案用系統 `tsc 6.0.2`（`--ignoreConfig --noResolve`）確認沒有語法或結構問題（只剩
  `--noResolve` 必然出現的模組/global 缺失噪音），並手動核對 `.prettierrc`
  （printWidth 100、single quote）與既有測試的 mock chain 寫法一致。實際
  `pytest`／`npm test`／production build 交給 CI 的 `backend.yml`／`frontend.yml` 驗證。
- 對應文件更新：`TODO.md`（「支援每個來源的使用者自訂名稱」打勾並記錄實作位置）、
  `docs/FEATURES.md`（功能總覽、`/me/feeds` API 表新增 `PATCH` 列、`user_feeds` 資料表列）。
- **PR review 後修正**（合併前）：自動 review 指出兩個 edge case——(1) 切換登入使用者時，
  重新命名編輯器的 `renamingId`／`renameValue` 沒有跟著重置，若元件維持掛載且兩人剛好訂閱
  同一個 feed，會讓後一位使用者的卡片一開就帶著前一位使用者尚未送出的自訂名稱；(2) 儲存中
  只有畫面上的按鈕被 `[disabled]` 擋住，輸入框的 `Enter`／`startRename` 本身沒有守門，同一次
  編輯可能被重複送出，較晚回來的回應可能把較新的編輯器狀態蓋掉。兩處都加上守門（`renaming()`
  guard、使用者 id 變動時清空編輯狀態），並在 `my-feeds.spec.ts` 補上對應案例。

## 階段三十六：支援來源靜音／暫停（2026-09-21）

TODO.md P2「資料夾與來源控制」的另一項：目前要讓一個訂閱的來源不再出現在「我的閱讀」，唯一
的辦法是整個取消訂閱，之後想找回來還得重新訂閱一次，會丟失既有的已讀狀態關聯（訂閱關係本身
被刪掉重建）。靜音讓讀者可以「先不看這個來源」而不必付出這個代價。

- **`backend/migrations/022_user_feed_mute.sql`**：`driftread.user_feeds` 新增
  `muted_at TIMESTAMPTZ`（nullable）。NULL 代表未靜音；非 NULL 記錄靜音當下的時間戳，同
  `feeds.archived_at`（migration 002）既有的「用時間戳而不是純布林」慣例，時間戳本身是免費
  多出來的資訊，比布林多記錄「何時」。`list_reading_stream`／`reading_stream_unread_counts`
  （皆 `CREATE OR REPLACE`，簽章不變、既有 grant 不必重下）加上 `uf.muted_at IS NULL`：已靜音
  的訂閱從「我的閱讀」的文章時間流、總未讀數與來源篩選清單中整個消失。`mark_reading_stream_read`
  刻意不改——靜音是可逆的，不該讓「這篇文章在靜音期間新增」變成「解除靜音後被追溯標記已讀」；
  `GET /me/feeds` 與取消訂閱都直接查 `user_feeds`，不受這兩個 function 的改動影響，靜音的來源
  仍照常列在「我的訂閱」。
- **`backend/models.py`**：`SubscribedFeed` 新增 `muted_at: datetime | None`；`SubscriptionUpdate`
  新增 `muted: bool | None`，與既有的 `custom_title` 各自獨立、預設都是 `None`。
- **`backend/routers/me.py`**：`list_subscriptions` 一併 select `muted_at`；`update_subscription`
  改用 `body.model_fields_set` 判斷請求 body 真的帶了哪個欄位，只更動那些欄位——避免「只想切換
  靜音」的請求因為 `custom_title` 預設是 `None` 而把已設定的自訂名稱誤清空，反之亦然；body 兩個
  欄位都沒帶則整個不呼叫 `update`。
- **前端**：`models.ts` 的 `SubscribedFeed` 新增 `muted_at`；`MeService.setMuted()` 獨立成自己的
  請求，刻意不跟 `updateSubscription` 共用一次 PATCH body，避免靜音時意外帶上記憶體裡當下的
  `custom_title` 值重新送一次。`components/my-feeds`：卡片顯示「已靜音」標籤與
  靜音／取消靜音按鈕，用 `mutingIds`（每個 feed id 各自的在途狀態）擋同一張卡片的重複點擊；
  未擋跨卡片操作，因為靜音沒有像重新命名編輯器那樣「同時只能開一個」的共用 UI 狀態需要保護。
- **測試**：`backend/tests/test_me.py` 新增 5 案例（`GET /me/feeds` 回傳 `muted_at`、靜音、
  取消靜音、只設定 `custom_title` 不動靜音狀態、空 body 整個不呼叫 `update`）；靜音的跨使用者
  隔離由既有的 `test_update_subscription_scoped_per_user` 涵蓋（走的是同一段
  `update().eq(user_id).eq(feed_id)` call path，與更新哪個欄位無關，另開一個案例不會增加涵蓋
  範圍）。`frontend/src/app/components/my-feeds/my-feeds.spec.ts` 新增 4 案例（靜音、取消靜音、
  同一張卡片在途時忽略第二次點擊、失敗不動狀態並跳一次 toast），既有 fixture 補上 `muted_at`。
- 對應文件更新：`TODO.md`（「支援來源靜音／暫停」打勾並記錄實作位置）、`docs/FEATURES.md`
  （功能總覽、`/me/feeds` API 表、`user_feeds` 資料表列、`list_reading_stream`／
  `reading_stream_unread_counts` 說明）。

## 階段三十七：擷取生命週期、中文來源與營運監控（PR #65，issue #63／#64，2026-10-08）

Review 修復：匯入保留累計文章數、空待抽取佇列跳過 HostIndex、seed 嚴格隔離一般名額、正文修訂重設保存期、舊留言／別名審核防繞過。PostgreSQL 完整 migration、SQL fixture 與多連線鎖競態納入 Backend CI。

後續 review 修復：未修改的預設設定可明確套用並入列種子；文章與首頁採集共用新 host 配額；首頁採集另記錄嘗試時間，避免文章 backlog 或失敗造成每個 tick 重抓。

補入共用 `app_settings` 表、型別設定註冊、版本衝突保護與後台全域設定頁；語言／分類探測方向取代硬編碼中文名額，儲存與新增種子在同一筆交易中完成。

文章寫入以正文／摘要 hash 跳過相同版本；逐版本抽取與正文保存解耦，失敗與截斷保留待處理狀態。新增預設關閉的正文縮減排程與預設 dry-run 的管理端點，保護訂閱、已讀、收藏／稍後讀，保留文章身份並同步全文搜尋。管理首頁新增私有 worker heartbeat／近期執行與文章資料量快照；補入多類中文種子、探測優先名額，以及留言 feed／斜線別名品質規則。部署與限制見 [CRAWLER_OPERATIONS.md](CRAWLER_OPERATIONS.md)。

## 階段三十八：專案規範改由 `AGENTS.md` 單一維護、changelog 依十位段歸檔、文件一致性檢查（PR #67，2026-10-08）

把無感記帳（Seamless Track）已經跑順的開發規範與文件紀錄方式移植過來。

- **`AGENTS.md` 成為 Claude Code 與 Codex 共用的唯一規範，`CLAUDE.md` 只剩一行 `@AGENTS.md`**：Codex 只讀 `AGENTS.md`（PR 的 Codex review 也是），
  Claude Code 有 `CLAUDE.md` 時預設只讀 `CLAUDE.md`——過去 Codex 完全看不到本專案的任何規則。原 `CLAUDE.md` 的內容全部搬進 `AGENTS.md`，另新增：
  - 「資料與外連說明」：backend 全程使用 `service_role`、使用者隔離實際靠應用層 `user_id` 條件；伺服器會主動對外連線——對外說明不可含糊帶過。
  - 「改 X 前先讀 Y」對照表：動到抓取、推薦、發現、正文保存、全域設定、schema、前端樣式、部署前分別要讀哪份文件的哪一段。
  - 快速開始、CI 一覽，以及 backend（DB 查詢、日誌、mock）、frontend（樣式、`[innerHTML]`、樂觀更新）規範的摘要，細節仍連回 `SECURITY.md`、`frontend/README.md`。
  - **migration 規則**：新檔改用 `YYYYMMDDHHMMSS_<name>.sql`、已合併的 migration 不得修改（ledger 只看檔名）、可重跑的存在性防護、`supabase/` 分層原稿與 010 逐字一致。
  - **Changelog 維護**：一個 PR 一個階段條目、review 修復併入同一條目、寫清楚根因與沒做什麼、同步更新 FEATURES／SECURITY／TODO。
  - **文件維護**：本檔與 `docs/` 不重複、現況文件 vs 歷史紀錄、註解引用文件時寫段落名稱而非章節編號。
  - **PR 語言規範**（繁體中文）與 **Code Review 原則**（finding 門檻、優先級、產品規模假設、review scope），依本專案調整：
    公開免認證端點與對第三方 URL 的外連面對不受信任的網路，外部可觸發的安全問題不適用「極端輸入不提」的放寬。
- **CHANGELOG 依十位段歸檔**：原本約 222 KB 的單檔只留最近兩個十位段（階段二十一起，約 135 KB），階段一～二十原文搬到
  `docs/changelog-archive/stages-01-10.md`、`stages-11-20.md`（只搬不改，相對連結補 `../`），搬移規則寫在本檔開頭。
  （Codex review：初版連二十一～三十也搬走，主檔實際只剩一個十位段，與「保留兩個十位段」的規則不符，已搬回。）
  檔頭原本的「尚未合併：#63／#64」已隨 PR #65 合併，改列為階段三十七並移到依時間排序的位置。
- **新增 `scripts/check_docs.py` 與 `.github/workflows/docs.yml`**：`CLAUDE.md` 只能是 `@AGENTS.md`、`AGENTS.md` 不超過 30 KiB
  （Codex 預設只讀前 32 KiB）、所有 Markdown 的相對連結都存在、`AGENTS.md` 以反引號提到的路徑都存在。
  workflow 不設路徑篩選（Codex review：只改名或刪除被引用的程式檔、沒動到 `.md` 的 PR 也會讓連結失效），每次 PR／push 都跑。
  Markdown 交給 CommonMark 解析器 `markdown-it-py`（版本釘在 `scripts/requirements-docs.txt`）而非手寫 regex：Codex review 連續指出圖片、帶標題、
  reference-style 連結與波浪號／縮排／縮排式程式碼區塊的漏判，根因是自己解析 Markdown，改用解析器一次涵蓋；程式碼區塊與行內程式碼裡的示範語法不檢查。
  另外檢查內嵌 HTML 的 `href`／`src`、拒絕逸出 repository 的相對路徑（`../../` 在 runner 上存在、在 GitHub 上是壞連結）、`.env.example` 這類 dotfile 也列入路徑檢查；`.markdown` 與大寫副檔名也掃描、略過 `//host` 協定相對網址、`docs/` 這類帶尾斜線的單層目錄也檢查（Codex review）。
- README 的文件表、CI 表（補上 `supabase/**` 觸發、`npm test`、docs workflow、sha tag）與開發規則改為指向 `AGENTS.md`；
  `SECURITY.md`、`RUNBOOK.md` 引用 `CLAUDE.md` 的地方改指 `AGENTS.md` 的「環境變數維護」。

沒有移植的部分：無感記帳的「每個 PR 疊代版本號」與 Play Store 公告流程——本專案以 `:sha-<commit>` tag 部署、沒有對外版號，不需要。

### 驗證

- `python3 scripts/check_docs.py` 通過；把 `CLAUDE.md` 改成多一行時確實失敗；臨時文件裡的壞圖片、帶標題、角括號、reference-style、清單延續段落與表格裡的壞連結都會被抓到，各種圍欄、縮排式程式碼區塊與行內程式碼裡的示範連結不會誤報；`AGENTS.md` 提到不存在的路徑時會失敗。
- 以逐行比對確認歸檔後的 CHANGELOG 與歸檔檔合起來涵蓋原檔每一行（只有被改寫的檔頭與「尚未合併」標題不同）。
- 未改動任何程式碼，backend／frontend workflow 不受影響。
