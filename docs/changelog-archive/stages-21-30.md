# 變更紀錄歸檔：階段二十一～三十

> 從 [../CHANGELOG.md](../CHANGELOG.md) 整段搬來，**只搬不改**——內容就是當時的紀錄，文中的路徑、行為與「目前」都以當時為準，現況請看 [../FEATURES.md](../FEATURES.md)。

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
