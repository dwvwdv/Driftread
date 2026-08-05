# 安全加固紀錄

PR #14–#21（2026-07-23 ~ 07-30）是一連串安全與正確性修補，來源是同一個「持續改善專案」的排程任務，
每筆各自獨立、都有回歸測試。這份文件把「修了什麼、為什麼會發生、現在的防線長怎樣」記在一起，
避免同類問題再被引入。

---

## 逐筆紀錄

### #14 — PostgREST filter 注入 + N+1 upsert（07-23）

- **問題**：`GET /api/feeds?search=` 與 `GET /api/recommendations` 直接把使用者輸入插進 PostgREST 的 `.or_()` filter 字串。PostgREST 的 filter 語法把 `,` `(` `)` 當結構字元，所以精心構造的 `search`，或（透過 `PUT /me/preferences` 完全可控、當時無長度限制的）`preferred_categories` 條目，可以附加額外的 filter 條件而不只是比對文字。
- **修法**：
  - 新增 `backend/utils.py::escape_postgrest_literal()`（依 PostgREST 的 quoted-literal 規則加引號並轉義），用於 feeds 搜尋。
  - `recommendations.py` 的分類條件從手拼 `.or_()` 改為 `.in_("category", [...])`，完全避開字串插值。
  - `UserPreferences.preferred_categories` / `preferred_languages` 各限 50 筆（縱深防禦）。
- **附帶**：`discover.py` 的 `/discover/import` 與 `admin.py` 的 refresh 原本每篇文章一次 `upsert()` HTTP round trip（50 篇 = 50 次循序請求）。抽成 `services/articles.py::upsert_articles()`，先依 URL 去重（同批重複 URL 會讓 Postgres 拋 `ON CONFLICT DO UPDATE command cannot affect row a second time`），再以 200 篇為一批 upsert。

### #15 — SSRF 守門沒裝在會落庫的端點（07-24）

- **問題**：`services/feed_discovery.py` 有私網 / loopback / link-local 的 host 守門（`_is_safe_host`），但只接在候選探索端點 `POST /discover`。真正**抓取並持久化**的端點都沒呼叫它：`POST /discover/import`（免認證）、`POST /admin/feeds/from-url`、`PATCH /admin/feeds/{id}/refresh`、`POST /me/import/opml`。任何 client 可以指向內部位址（例如雲端 metadata IP `169.254.169.254`），伺服器會去抓；若回應解析得出 RSS/Atom，還會寫進公開的 `feeds` 表。
- **修法**：守門改名為公開的 `services.feed_discovery.validate_fetch_url`，在每個直接呼叫 `fetch_and_parse()` 的地方之前呼叫。OPML 匯入逐 outline 套用（被拒的進既有 `failed` 清單，不讓整個請求失敗），並把單次請求處理的 outline 上限訂為 200（原本無上限且循序抓取）。同時停止把抓取失敗的原始例外文字回傳給 client（會洩漏內部主機名與錯誤成因）。
- **附帶**：`docker-compose.yml` 的 api 服務 `environment:` 漏了 `SUPABASE_JWT_SECRET`，而 `.env.example` 與 `scripts/gen_env.py` 早就要求它 —— 缺這個會讓 `auth._verify_token` 對每個請求丟 500。
- **後續修補（同 PR review）**：重新驗證 redirect 目標，補上經由轉址繞過守門的缺口。

### #16 — `fetch_and_parse` 無上限緩衝回應（07-25）

- **問題**：`backend/rss_parser.py::fetch_and_parse` 是所有真實 feed 匯入路徑實際使用的函式（discover import、admin refresh / from-url、OPML 匯入），它直接 `client.get()` 並把整個 response body 讀進記憶體，**沒有大小上限** —— 而 `services/feed_discovery._fetch` 早就有串流 + 5 MiB 上限。一個通過 SSRF 守門的公開主機只要回傳超大 body，就是直接的記憶體耗盡向量。
- **修法**：`feed_discovery._fetch` 改名為公開的 `fetch_with_cap`，讓 `fetch_and_parse` 委派給它，兩條路徑共用同一份「串流 + byte cap + redirect 重驗」實作，不再各寫一份而漸行漸遠。加上回歸測試 `test_fetch_and_parse_rejects_oversized_response`。
- **附帶**：`DiscoverRequest.url` / `DiscoverImportRequest.feed_url` 加 `max_length=2048`；admin 列表端點的 `limit` 加上界。

### #17 — 全站沒有 request body 上限（07-26）

- **問題**：FastAPI / Starlette 會在任何 route 或 pydantic 驗證執行**之前**就把 request body 完整緩衝進記憶體。#14–#16 加的欄位長度限制都是在那之後才生效。也就是說任何 JSON POST 端點——包含公開免認證的 `POST /api/discover`、`POST /api/discover/import`——完全沒有 payload 大小限制。
- **修法**：`backend/main.py` 新增 `MaxBodySizeMiddleware`，在讀 body 之前檢查 `Content-Length`，超過 `MAX_REQUEST_BODY_BYTES`（6 MiB，比 OPML 的 5 MiB 上限寬鬆，不影響正常匯入）直接回 `413`。註冊在 `CORSMiddleware` **之後**，因此是最外層（Starlette 先執行最後加入的 middleware），在 CORS 與路由做任何事之前就擋掉。
- **已知限制（#27 已修）**：只檢查 `Content-Length` header，涵蓋一般 JSON / multipart client；以 chunked transfer-encoding 串流且不帶 `Content-Length` 的請求不在此檢查範圍。

### #18 — 公開探索端點沒有 rate limit（07-27）

- **問題**：`POST /api/discover` 與 `/api/discover/import` 公開免認證，每次都會對 client 指定的 URL 發出外連請求。SSRF 守門管得住「打去哪」，管不住「打多少」——可以無限迴圈呼叫，把伺服器當成免費的網路掃描器 / 放大器。
- **修法**：`backend/rate_limit.py` —— process 內的 sliding window 限流器（每 IP 每端點 20 requests / 60s），以 FastAPI dependency factory `rate_limit(name)` 提供，沿用 `routers/admin.py::_require_api_key` 的既有模式。兩個端點透過 `dependencies=[Depends(rate_limit(...))]` 掛上，在 route 邏輯與 DB 存取之前生效。刻意做成 process-local（`dict` / `deque`，不用 Redis）：compose 只跑單一後端容器，分散式限流是沒用的複雜度。
- **後續修補（同 PR review）**：
  - 阻止 `X-Forwarded-For` 偽造。`main.py` 加 `ProxyHeadersMiddleware(trusted_hosts="*")`，因為 compose 不對外發布 api 的 port，唯一可能的 peer 就是 nginx；`frontend/nginx.conf` 用 `proxy_set_header X-Forwarded-For $remote_addr`（**不是** `$proxy_add_x_forwarded_for`，後者會把 client 自己帶的值串在前面）。缺這組設定時，每個請求看到的 client 都是 nginx 容器位址，限流會把所有真實使用者算成同一人。
  - 限流器記憶體上界：`MAX_TRACKED_CLIENTS = 10_000`。

### #19 — OPML 上傳的 XML entity expansion（07-28）

- **問題**：`POST /api/me/import/opml` 用原生 `xml.etree.ElementTree.fromstring()` 解析不可信上傳，未關閉 DTD entity 宣告與外部 entity 參照。任何登入使用者（此端點只要有效 bearer token，無需特殊權限）可上傳一個小小的 billion-laughs `DOCTYPE` 檔案耗盡單一後端容器的記憶體 / CPU，或嘗試 XXE。
- **修法**：改用 `defusedxml.ElementTree.fromstring`（預設 `forbid_entities=True`、`forbid_external=True`），直接拒絕並拋 `DefusedXmlException`。

### #20 — `.single()` 讓「查不到」變成 500（07-29）

- **問題**：`GET /api/feeds/{id}`、`GET /api/articles/{id}` 與 admin 的 `archive` / `unarchive` / `refresh` 都用 `.select(...).eq("id", ...).single().execute()`，再 `if not result.data: raise HTTPException(404)`。對真實的 supabase / postgrest-py client 而言那是死碼：`.single()` 送 `Accept: application/vnd.pgrst.object+json`，0 筆命中時 PostgREST 回 `406`（`PGRST116`），postgrest-py 對非 2xx 一律拋 `APIError`，永遠不會以 `data=None` 正常返回。`main.py` 沒有對應的 exception handler，於是變成未處理的 `500`。
- **重現**：對任何格式合法但不存在的 UUID 打 `GET /api/feeds/{uuid}` → 500 而非 404，公開端點無需認證即可觸發。
- **修法**：5 處全部改為 `.maybe_single()`（0 筆時回 `data=None` 不拋錯），既有的 404 分支才真正可達。加上回歸測試斷言 not-found 路徑呼叫 `.maybe_single()` 且從不呼叫 `.single()`——原本的測試用裸 `MagicMock()` 當 db，兩種方法都會靜默通過，抓不到這個 bug。
- **後續修補（同 PR review）**：`maybe_single()` 有可能回傳裸 `None` 而非 `data=None` 的物件，一併防守。

### #21 — 遠端 feed XML 的 entity expansion（07-30）

- **問題**：`rss_parser.parse_feed()` 仍用原生 `xml.etree.ElementTree.fromstring` 解析遠端抓來的 RSS / Atom。#19 修掉了 OPML 上傳的同一個漏洞類別，但 feed 解析器本身（`fetch_and_parse()` 與 `discover_feeds()` 使用）被漏掉了。
- **嚴重度高於 #19**：OPML 上傳需要認證，feed 解析卻可經由完全公開的 `/api/discover` 觸發。任何匿名呼叫者都能指向自己控制的 feed URL 供應 billion-laughs payload。既有的 SSRF 守門與下載 byte cap 都幫不上——cap 限制的是**下載**的位元組數，不是 entity 展開**後**的大小。
- **修法**：改用 `defusedxml` 的 `fromstring`（已是專案依賴，`routers/opml.py` 已用同樣 import 形式），並把 `xml.etree.ElementTree.ParseError` 與 `defusedxml.common.DefusedXmlException` 都正規化成 `ValueError`。這順帶補掉第二個潛在缺口：`services/feed_discovery.py` 的 `discover_feeds` / `_validate_feed` 只 `except ValueError`（預期「不是合法 feed」以此形式浮現），但格式錯誤的 XML 原本拋的是未被攔截的 `ET.ParseError`（`SyntaxError` 子類，不是 `ValueError`），會以未處理例外的形式穿過公開的 `/api/discover`。

### #22 — discover 候選連結未經 SSRF 驗證（07-30）

- **問題**：`discover_feeds()` 對使用者給的 URL 呼叫了 `validate_fetch_url()`，但從那個頁面 HTML 抽出來的候選 feed 連結沒有。`_extract_feed_links()` 用 `urljoin(base_url, href)` 產生候選，絕對路徑的 `href` 會直接覆蓋 base，因此可以指向任何位址；`_validate_feed()` 再把它交給 `fetch_with_cap()`，而後者當時只驗證 **redirect hop**，不驗證初始 URL。
- **嚴重度**：`/api/discover` 公開免認證。攻擊者只要架一個正常的公開頁面，內含
  `<link rel="alternate" type="application/rss+xml" href="http://169.254.169.254/latest/meta-data/">`，伺服器就會去請求那個內部位址。#15 修的是「呼叫端忘記守門」，這次是「守門位置不對」——連結不是使用者打的，是遠端內容給的。
- **修法**：把守門移到唯一的抓取瓶頸點，`fetch_with_cap()` 對**初始 URL 與每個 redirect hop** 都跑 `validate_fetch_url()`，而不再依賴每個呼叫端各自記得。重複驗證一個已驗證過的 URL 只多一次 DNS 查詢。
- **測試**：`test_discover_feeds_rejects_private_alternate_link` 斷言 `MockTransport` 從未收到往 `169.254.169.254` 的請求（修法前這個測試會失敗，實際觀察到該請求）。
- **附帶**：`MAX_HTML_BYTES`（2 MiB）從未被任何程式碼引用——`discover_feeds` 的第一次抓取一律用 `MAX_FEED_BYTES`。這個常數是死碼，卻讓人以為有一道 2 MiB 的 HTML 上限。已刪除，文件改為記載實際的單一 5 MiB 上限。
- **已知限制：DNS rebinding 仍可繞過守門（#25 已修）**。`_is_safe_host()` 用 `socket.getaddrinfo()` 解析主機名做判斷，但接下來 `client.stream()` 連線時 httpx 會**獨立再解析一次**。攻擊者控制的 domain 可以在驗證那次回公開 IP、在連線那次回 `169.254.169.254` 之類的位址（短 TTL 或多筆 A 記錄輪替），於是 `/api/discover` 這條公開免認證路徑仍可能發出內部請求。
  這是 check-then-fetch 這種守門形式的固有弱點，從 #15 引入守門起就存在，不是這次改動造成的。真正關掉的做法是 pin-and-connect：解析一次、只連到那個已驗證的 IP，同時保留原始 hostname 給 HTTP `Host` 標頭與 TLS SNI（httpx 自訂 transport + `sni_hostname` request extension）。`services/feed_discovery.py::PinnedTransport` 在 #25 落地了這個修法，細節見 SECURITY.md 的 #25 條目。

### #24 — 自主發現：從「使用者觸發」變成「自我驅動」的外連（07-30）

主動發現新 RSS 源這個功能把對外面向的性質整個換掉了：以前每一次外連都是某個人剛剛
按下按鈕觸發的，而且吃 rate limit；現在有一個無人看管、依自己排程持續對第三方網站
發請求的迴圈。以下六點是為此做的設計與仍存在的風險。

**1. 自主放大 / 濫用**

路徑上沒有人，也沒有 `rate_limit()`（端點由 `X-API-Key` 把關，真正的量能上限是批次與
並發參數）。給運維人員自己估算流量的算式：

> 每輪 `PROBE_BATCH_SIZE`(20) × (1 robots + 1 首頁 + 最多 7 條 fallback 路徑 + N 個
> alternate link 驗證) ≈ 200 個請求。`TICK_SECONDS` 900 ⇒ 約 800 req/h，分散在約 80 個
> 不同 host 上，每個 host ≤ 0.5 req/s。

上限：`PROBE_BATCH_SIZE`、`PROBE_CONCURRENCY`(3)、`HOST_DELAY_SECONDS`(2)、
`FEED_DISCOVERY_ENABLED` **預設 false**，以及 `POST /admin/discovery/run` 在停用時回
**503**。最後這點是刻意與 `/admin/feeds/refresh-due` 不同的：refresh 的 flag 只管 worker，
因為手動刷新我們自己已經擁有的源在排程關掉時仍然合理；但對一個主動探測第三方的爬蟲，
「已停用」必須真的是停用，否則那是個運維人員在站方寫信來時無法背書的說法。

`DISCOVERY_USER_AGENT` 應帶上聯絡網址，讓被抓取的站方能找到你要求停止。已查證
`urllib.robotparser` 的 `Entry.applies_to` 只看第一個 `/` 之前的字，所以加括號註記不會
破壞 robots 的 UA 匹配。

**2. ⚠ DNS rebinding（#22 已記錄但未修的繞過）在這裡嚴重得多**

以前攻擊者要主動 POST 一個 URL 到公開端點，並吃掉 20 req/60s 的配額。現在待探測佇列是
**從第三方文章 HTML 填的**：只要讓一個連結出現在任何被收割 feed 的任何文章裡，就能讓
迴圈依自己的排程、無人看管地、最多 `PROBE_MAX_ATTEMPTS` 次去抓那個 host（每次 robots
加上最多 8 個探測請求）。那是很多次獨立的 DNS 解析，而攻擊者只需要其中一次落在內網位址；
check-then-fetch 的窗口在每個請求的每個 hop 都會重新打開。

過渡緩解（都不足以關掉這個洞，只是縮小它）：

- 待探測佇列的 host 有唯一性，同一個 host 不會被反覆排入；
- 解析到私有位址記為失敗，3 次後 `exhausted`；
- `normalize_host()` 在**寫進資料庫之前**就拒絕 IP literal、無點 host 與
  `.local` / `.internal` / `.onion` 等保留 TLD；
- 整個迴圈預設關閉。

**這提高了 pin-and-connect 修法的優先度。建議在任何環境把 `FEED_DISCOVERY_ENABLED`
設為 true 之前先落地那個修法**（解析一次、只連到那個已驗證的 IP，同時保留原始 hostname
給 `Host` 標頭與 TLS SNI）。

**3. 待探測佇列無限膨脹**

一個灌水 feed 每篇 500 個 anchor × 20 篇文章就能排入上千個 host，而每個被探測的 host 又
可能產出約 7 個候選。上限：`HARVEST_MAX_LINKS_PER_FEED`(200，單一 feed 每輪貢獻的網域數)、
`MAX_ANCHORS_PER_DOC`(500)、`MAX_HARVEST_HTML_BYTES`(512 KiB，單篇實際解析的量)、
`MAX_OPML_FEEDS_PER_SOURCE`(500)、`MAX_FRONTIER_SIZE`(50k pending —— 超過後仍累積既有目標
的證據但不再新增目標)、`discovery_targets.url` 的 UNIQUE，以及終態列會離開 partial index。

清理指令見 [FEATURES.md 第 5 節](FEATURES.md)。候選是 `ON DELETE SET NULL` 參照待探測列，
所以刪除終態列不會毀掉審核歷史（尤其是拒絕紀錄）。

**4. denylist 被 redirect 繞過（本次修掉）**

denylist 套用在我們收割到的 host 上，但 `fetch_with_cap_response()` 會跟隨最多 5 次
redirect，而本次改動前每個 hop 只重跑私有位址檢查。於是 `blog.example.com` 可以 302 到
`facebook.com`，任何不在清單上的短網址可以轉去任何地方。

修法是新的 `allow_url` hook：choke point 對**初始 URL 與每個 hop** 都評估一次，且順序在
`validate_fetch_url()` 之後 —— 政策層疊在安全邊界之上，不是取代它。這是 #22 同一個教訓
換個地方出現：**檢查放在唯一的瓶頸點，不要放在各 call site**，因為 call site 會被漏掉，
而 5 次 redirect 等於 5 次逃脫機會。

**4b. 入庫覆寫既有 feed 的 metadata**

候選在佇列裡等待期間，同一個 feed URL 可能已被手動、由擴充或由 OPML 匯入。初版的入庫是
無條件 `upsert(on_conflict="url")`，於是一次無害的重複核准就會把人工整理過的標題、網站、
分類與標籤，換成抓來的值與這次呼叫的空白預設。改為先查再寫：已存在就只把候選標成
`imported` 並指向既有列，一個欄位都不動。

**5. 儲存不受信任的第三方標題 / URL**

候選的 `title` 與 `website_url` 來自遠端 HTML/XML，核准後會進到 `feeds.title` —— 一張
**公開、全世界可讀**的表（004 的 read policy），由 Angular 渲染給每個訪客。Angular 預設會
轉義插值，那處理掉了標記；這裡要處理的是轉義處理不了的東西：

- `sanitize_text()` 移除 C0/C1 控制字元、零寬字元與 **bidi override**。標題裡的 bidi
  override 能在審核者眼前把網域視覺反轉，於是按下核准的人看到的和實際存下的不是同一個
  東西。長度截斷到 200。
- `sanitize_http_url()` 強制 http(s) scheme，讓 `javascript:` 永遠進不了資料庫，更不會
  進到 href。
- 審核 UI **只用插值，絕不 `[innerHTML]`**，而且把我們自己正規化出來的 `source_host`
  顯示在標題**旁邊**，讓偽裝的標題騙不到人。

（既有、面積大得多、不在本次範圍：`article-reader.html` 的 `a.content` 用了
`[innerHTML]`。）

**6. robots / 禮貌性**

預設開啟，且**三個對外階段都套政策**：blogroll 一跳、目錄頁抓取、以及探測。政策由
`services/crawl_policy.py::make_gate()` 產生後傳進 choke point —— 初版只在探測階段掛了政策，
於是另外兩個階段在 `RESPECT_ROBOTS=true` 之下照樣裸奔，正是規則 9 想防的那種「各 call site
各寫一份就會漏」。現在建立政策與使用政策分開，新增階段漏不掉。

**denylist 只套在探測階段。** 收割與目錄讀的是我們自己選的地方（管理員設定的目錄頁、已在
catalog 裡的 feed 的首頁），而 denylist 回答的是「這個 host 值得被收錄成部落格嗎」—— 對
「要從哪裡讀清單」是錯的問題，而且套上去會永久擋死預設目錄清單（它就放在 github.com）。
這兩個階段仍過 URL 形狀檢查與 robots，它們抽出來的連結也照常過 denylist。

RFC 9309 語義：4xx ⇒ 全允許、5xx ⇒ 全拒絕、不可達 ⇒ 拒絕。`Crawl-delay` 尊重但夾在 30 秒 ——
一句 `Crawl-delay: 86400` 不能釘住一個探測槽位一整天。robots.txt 走同一個有 SSRF gate 與
位元組上限的 choke point；**絕不呼叫 `RobotFileParser.read()`**，它會自己 `urlopen` 並繞過
上述全部（已加測試釘住）。cache 有界（2000 origin、LRU）且有 TTL（1 小時），理由同
`rate_limit.py::MAX_TRACKED_CLIENTS`。

「不准」分成永久與暫時兩種，`RobotsDecision.transient` 帶這個資訊：解析出的 `Disallow` 是
站台叫我們別來（終態），而 5xx 或不可達只是站台此刻壞了（退避重試）。兩者混為一談的話，
一次短暫的 503 就會讓那個目標被永久歸檔，站台修好也不會再被看一眼。

`FEED_DISCOVERY_RESPECT_ROBOTS=false` 是給爬自己財產的運維人員用的，合規責任由你自負；
另外它會移除「站台是否可達」的信號，讓重試判斷變粗糙（見 FEATURES.md 第 2c 節的決策表）。

**另外兩點**

- 四張新表開 RLS 但**刻意不建任何 policy**，連 SELECT 都沒有 —— anon key 洩漏也無法列舉
  待探測佇列或候選清單。誰連到誰是 scraping 敏感資料。
- 本次設計刻意讓第三方字串完全不進 PostgREST filter：host 集合都從我們自己的列在記憶體裡
  建（`HostIndex`），候選查詢逐 URL 用 `.eq(...).maybe_single()` 而不是一次 `.in_(...)`。
  **相關的既存暴露，本次未改，記為後續**：`routers/discover.py:41` 把遠端 HTML 抽出的 URL
  直接餵進 `.in_("url", feed_urls)`。（已於 #25 修掉，改為同樣的逐 URL `.eq(...).maybe_single()`。）

### #25 — pin-and-connect：DNS rebinding 的 check-then-fetch 缺口（07-30）

- **問題**：`validate_fetch_url()`（`socket.getaddrinfo()`）與實際的 `client.stream()` 連線各自獨立解析同一個 hostname。#22 記載的已知限制、#24 第 2 點標記為「應優先修」的正是這個 check-then-fetch 窗口：攻擊者控制的 domain 可以用短 TTL 或多筆 A 記錄，讓驗證那次解析回公開 IP、連線那次解析回 `169.254.169.254`（或任何內網位址）。#24 上線後風險更高——待探測佇列從第三方文章 HTML 自動填入，不再需要攻擊者主動打 API。
- **修法**：在傳輸層而非呼叫端修。新增 `services/feed_discovery.py::PinnedTransport`（`httpx.AsyncHTTPTransport` 的子類）：`handle_async_request()` 對 `request.url.host` 做**單次** `socket.getaddrinfo()`（`_resolve_pinned_ips()`），驗證解析出的**每一筆**位址都不是 private / loopback / link-local / multicast / reserved（與 `_is_safe_host()` 同樣保守——任何一筆不安全就整個拒絕，不是只挑安全的那筆連），再把連線目標換成驗證過的 IP，同時把原始 hostname 塞進 `extensions["sni_hostname"]` 供 TLS SNI 使用（`Host` 標頭已在 httpx 組請求時就用原始 hostname 設好，不受影響）。新增 `ssrf_safe_client()` 工廠函式，是全專案唯一該用來建構會抓外部 URL 的 `AsyncClient` 的地方；6 個既有的 `httpx.AsyncClient(...)` 建構點（`feed_discovery.discover_feeds`、`rss_parser.fetch_and_parse` / `fetch_and_parse_conditional`、`robots._fetch`、`directory_sources._fetch`、`link_harvest._fetch_blogroll`）全部改用它——單一工廠而非逐一手改 call site，是規則 9 同一個教訓。
- **`validate_fetch_url()` / `_is_safe_host()` 本身不動**：既有的 check-then-fetch 語意保留在呼叫端（仍然是 `allow_url` 政策層跑之前的第一道門），新的傳輸層解析是**第二道獨立的門**，決定的是真正的連線目標——兩道門之間不再有「驗證用一個 IP、連線用另一個 IP」的落差，因為傳輸層自己的解析結果就是它自己拿去連線的那個。
- **已知限制**：`_resolve_pinned_ips()` 每次呼叫只解析一次，但 `fetch_with_cap_response()` 對每個 redirect hop 都會重新進入這個傳輸層（每個 hop 都是新的一次 `client.stream()` 呼叫），所以每個 hop 各自有自己的單次解析——這是預期行為，不是遺漏。
- **附帶修掉 `routers/discover.py:41`**（#24 附帶記錄的既存暴露，當時未改）：`feed_url` 是從遠端 HTML 的 `<link rel="alternate">` 抽出的第三方字串，原本整批塞進 `.in_("url", feed_urls)`，與 #14 修的 `.or_()` 注入同一類問題（只是經由 `.in_()` 的 list 序列化而非手拼字串）。改為逐一 `.eq("url", feed_url).maybe_single()`，match `services/discovery_candidates.py` 既有的作法，第三方字串完全不進 PostgREST filter 語法。
- **PR review（自動化 code review，九輪）抓出十二個問題，同一輪補上**：

  第一輪：

  1. **P1 — 連線池的 key 被換成了 IP**。`PinnedTransport` 把 `request.url.host` 換成解析出的 IP 再交給底層 transport，這連帶讓 httpcore 的連線池 key 也變成那個 IP。兩個**不同的原始 hostname**如果剛好指到同一個共用主機 / CDN IP（同 IP、不同租戶），就會在池裡撞在一起——第二個 hostname 的請求可能被送進一條「TLS session 是用第一個 hostname 的 SNI 建立的」既有連線，等於繞過了以 hostname 為單位的 TLS 身分驗證。修法：`ssrf_safe_client()` 把 `PinnedTransport(limits=httpx.Limits(max_keepalive_connections=0))` 接上——關掉 keep-alive 重用後，沒有連線會活過它服務的那一個請求，池裡不會留下任何東西可以撞。（`limits` 必須傳進 `PinnedTransport(...)` 建構子本身，不能傳給 `httpx.AsyncClient(**kwargs)`——一旦 `transport` 是明確給的，`AsyncClient` 會完全略過 `limits`／`verify`／`cert`／`http2`／`proxy` 這些參數，只有它自己組預設 transport 時才會套用；這是實作時另外抓到的一個坑，記在 `ssrf_safe_client()` 的 docstring 裡。）
  2. **P1 — `routers/discover.py` 的逐 URL 查詢沒有上限**。把 `.in_()` 改成逐 URL `.eq().maybe_single()` 解決了 filter 注入，但候選數量本身沒有上限——`_extract_feed_links()` 對 `<link rel="alternate">` 的掃描不設限，惡意頁面可以塞進成千上萬個宣告，讓公開免認證的 `/api/discover` 一次觸發同樣多次的驗證抓取（`discover_feeds()` 既有行為）加上（本次新增的）DB 查詢。第一輪修法是 `soup.find_all("link", limit=...)`（見下方第二輪第 4 點，這個做法本身又被抓出問題，第二輪換掉了）。
  3. **P2 — 只取第一筆解析位址會丟失 dual-stack 的 fallback**。未 pin 之前，httpx/httpcore 的預設連線邏輯會依序嘗試 `getaddrinfo()` 回傳的每一筆位址；只回傳 `ips[0]` 等於拿掉了這個 fallback——例如某環境 IPv6 實際不通但仍解析得出 AAAA，若那筆剛好排在前面，抓取會直接失敗而不會退回還可用的 IPv4。修法：`_resolve_pinned_ip()` 改名 `_resolve_pinned_ips()`，回傳**全部**驗證過的位址（驗證邏輯不變：任何一筆不安全就整個拒絕）；`PinnedTransport.handle_async_request()` 依序嘗試，只有連線層失敗才換下一筆重試（見第二輪第 5 點——第一輪只接 `httpx.ConnectError` 沒接對）。全站每個請求都是不帶 body 的 GET（見 `fetch_with_cap_response`），重試沿用同一個 `Request` 物件是安全的——沒有已被消耗一部分的 request stream 需要顧慮。

  第二輪（對第一輪修法的 review）：

  4. **P2 — 用 `find_all("link", limit=50)` 限制掃描數，會在真正的 feed 宣告前就停手**。BeautifulSoup 用 `"html.parser"` 解析時，整份文件（已受 `MAX_FEED_BYTES` 位元組上限約束）在 `find_all` 執行前就已經全部解析進記憶體——`limit=` 只影響回傳幾個元素，不影響解析成本，所以拿它限制掃描數量並沒有真的省到什麼，卻會讓一個 `<head>` 裡有 50 個以上 stylesheet / icon / preload 之類無關 `<link>` 標籤、真正 feed 宣告排在更後面的正常頁面，直接漏掉那個宣告。修法：把 cap 從「掃描的 `<link>` 標籤數」改成「篩選後、符合 `rel="alternate"` 且是 feed content-type 的候選數」——迴圈照樣跑過所有標籤（便宜，反正已經解析好了），只在候選數蒐集到 `MAX_FEED_LINK_CANDIDATES` 時才 `break`，惡意頁面全部標籤都合格的最壞情況仍然一樣被限制住。
  5. **P2 — 位址 fallback 沒接住連線逾時**。`PinnedTransport` 第一輪只 `except httpx.ConnectError`，但 `httpx.ConnectTimeout` 不是 `ConnectError` 的子類——兩者是 `httpx.TransportError` 下的兩個平行分支（`TimeoutException` vs `NetworkError`），不是父子關係。而「封包被默默丟棄」（逾時）其實比「立刻拒絕連線」更是「這條路由實際不通」（例如壞掉的 IPv6）在真實世界最常見的樣子，第一輪的 fallback 因此漏接了它原本要解決的那個情境裡最典型的失敗形態。修法：`except (httpx.ConnectError, httpx.ConnectTimeout)`。

  第三輪（對第二輪修法本身又抓出的問題）：

  6. **P1 — 位址 fallback 沒有共用一個 deadline，攻擊者可用 DNS answer 放大單次請求耗時**。`PinnedTransport` 依序嘗試每個驗證過的位址，但每次呼叫底層 transport 都拿到**完整**的連線逾時設定——沒有東西在嘗試之間遞減剩餘時間。惡意 DNS 可以讓一個 hostname 解析出一長串「公開但打不通」的位址（都通過 SSRF 檢查，因為每個都是真正的公開 IP，只是連不上），一次公開免認證的 `/api/discover` 請求就可能被拖到約「位址數 × 逾時秒數」那麼久——比 pin 之前的行為差。真正正確的修法是共用一個 deadline、逐次遞減每次嘗試分到的剩餘時間，但那需要正確組出 httpx 內部 `timeout` extension 的 dict 形狀，這個 sandbox 沒有網路能裝 httpx 對著真正的函式庫驗證，做錯的後果是每個請求的逾時設定被默默弄壞（比現在這個問題更糟，而且無聲無息）。改採風險小很多的作法：新增 `MAX_PINNED_CONNECT_ATTEMPTS = 2`，最多只嘗試前兩筆驗證過的位址——涵蓋這個 fallback 原本要處理的現實情境（一筆 AAAA、一筆 A），把最壞情況的放大倍率從「攻擊者控制的任意位址數」壓到一個固定的小常數。

  第四輪（對第三輪修法本身又抓出的問題）：

  7. **P2 — 位址上限的截斷沒有顧到位址族**。第三輪的 `ips[:MAX_PINNED_CONNECT_ATTEMPTS]` 是照 `getaddrinfo()` 回傳順序直接截斷——如果一個 resolver 對同一個 hostname 回傳兩筆以上 AAAA 記錄、排在唯一一筆 A 記錄前面，兩個嘗試名額會被 IPv6 佔滿，在 IPv6 實際不通的環境裡，又把第 3 點（dual-stack fallback）原本要解決的情境重新弄壞一次——只是這次不是「只回傳一筆」，而是「回傳的順序讓上限截斷到同一個族」。修法：新增 `_pick_pinned_ips(ips, limit)`，依「先出現的族優先」交錯排列（例如 IPv6、IPv4、IPv6、IPv4……，各自保留族內原始順序）後再截斷，確保只要有兩個以上族存在，上限之內一定各占到至少一個名額。這是純函式、不涉及任何 httpx／httpcore 內部細節，可以在沒有網路的環境裡放心驗證正確性。

  第五輪：

  8. **P2 — 回傳前沒有還原 `request.url`**。`httpx.Client._send_single_request()` 拿到 transport 回傳的 response 後才設定 `response.request = request`，然後才用 `response.request.url` 的 host 當 cookie domain 去解析回應帶的 `Set-Cookie`——這一步發生在 `PinnedTransport.handle_async_request()` **回傳之後**，而 `request` 是同一個被本函式改過 `url` 的物件。如果回傳前沒把 `request.url` 還原成原始 hostname，Set-Cookie 就會被歸檔到那次連線用的 IP 底下；`fetch_with_cap_response()` 手動 redirect 迴圈的下一 hop 用同一個 `client`、對真正的 hostname 建立新請求時，找不到存在 IP 底下的 cookie，帶 cookie 才能過關的重新導向鏈（例如某些反爬蟲挑戰）就會在這裡斷掉。修法：整個重試迴圈包進 `try/finally`，在 `finally` 裡把 `request.url` 還原成 `original_url`——`finally` 保證在 `return`／`raise` 真正把控制權交還呼叫端之前執行，所以呼叫端（不管是拿到 response 還是抓到例外）看到的 `request.url` 永遠是原始 hostname，不會是曾經用過的 pinned IP。

  第六輪：

  9. **P2 — 明確 `transport=` 可能讓環境代理設定失效或被繞過**。`httpx.AsyncClient` 是否要走 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`，取決於 `trust_env` 這個 client 層級參數——這一步和 `limits`／`verify`／`cert` 不同，**不會**因為給了明確的 `transport=` 就被跳過：`Client.__init__` 會照樣讀環境變數組出 `self._mounts`，而 `_transport_for_url()` 對命中 pattern 的 URL 一律回傳 mount 到的 proxy transport，優先於 `self._transport`（也就是 `PinnedTransport`）。這代表如果某個部署在容器／host 層級自己設了代理環境變數（Driftread 自身完全沒有文件記載或支援這件事），這次 PR 修的整個 pin-and-connect 可能在那個環境裡被靜靜繞過——因為實際命中 mount 時走的是 httpx 自己組出來的、沒有 pinning 的 proxy transport，而不是 `PinnedTransport`。修法：`ssrf_safe_client()` 補上 `trust_env=False`——`trust_env` 是唯一一個「即使給了明確 transport 仍然有效」的 client 層級參數，關掉它讓這批 fetch 完全不依賴環境代理設定，把行為變成明確、可預期的：永遠直連。這符合專案目前本來就沒有記載代理支援的現實，而不是依賴對 httpx 內部 mount 與 transport 優先順序的準確理解（這個 sandbox 沒有網路能對著真正的函式庫驗證那個細節）。

  第七輪：

  10. **P2 — `_resolve_pinned_ips()` 在事件迴圈裡做同步阻塞的 DNS 查詢**。`socket.getaddrinfo()` 是阻塞呼叫；直接在 `async def` 函式裡呼叫它並不會把控制權交還事件迴圈，而是整個卡住——不只卡住這次請求所在的 coroutine，是卡住這個 worker process 裡**所有**並發中的請求與背景迴圈，直到 DNS 查詢回來為止（或逾時）。`PinnedTransport.handle_async_request()` 對每個候選 feed 連結、每個 fallback path 都各呼叫一次，而這條路徑正是完全公開、免認證的 `POST /api/discover` 會觸發的——攻擊者只要指向一個回應很慢（或乾脆不回應）的 DNS 伺服器，一次請求就能讓整個 worker 的事件迴圈卡住數秒到數十秒，波及所有其他使用者的請求，是真正的服務層級 DoS。`validate_fetch_url()`／`_is_safe_host()` 呼叫 `socket.getaddrinfo()` 也有同樣的既有問題，但那是本 PR 之前就存在、呼叫點遍布整個專案（把它們改成 async 會牽動每一個呼叫端）的既有型態，不在本次修法範圍——只處理本 PR 新增、範圍完全自足的 `_resolve_pinned_ips()`。修法：改成 `async def`，用 `await asyncio.to_thread(socket.getaddrinfo, host, None)` 把阻塞呼叫丟到執行緒池，讓事件迴圈在等待期間可以繼續處理其他工作；唯一呼叫端 `PinnedTransport.handle_async_request()` 已經是 `async def`，補上 `await` 即可，不需要改動任何其他呼叫路徑。

  第八輪（對第七輪修法本身又抓出的問題）：

  11. **P1 — `asyncio.to_thread()` 把阻塞的 DNS 查詢丟到執行緒池後，等待本身沒有上限**。第七輪把同步的 `socket.getaddrinfo()` 換成 `await asyncio.to_thread(...)`，解決了「卡住整個事件迴圈」的問題，但 `to_thread()` 本身不帶任何逾時——在 pin-and-connect 之前，DNS 解析是 httpcore 在連線階段內部做的，天生就受連線逾時約束；換成獨立的一次解析後，這層約束消失了。攻擊者控制、回應很慢（或乾脆不回）的 DNS 伺服器，可以讓 `_resolve_pinned_ips()` 卡到系統 resolver 自己的逾時（往往遠長於 httpx 配置的 12–15 秒），而且是對每個候選 feed 連結、每個 fallback path 各卡一次，公開免認證的 `/api/discover` 因此仍能被單一請求佔用遠超預期的時間。修法：`_resolve_pinned_ips()` 加上可選的 `timeout` 參數，把 `to_thread()` 呼叫包進 `asyncio.wait_for(..., timeout)`，逾時就拋 `DiscoveryError`。`PinnedTransport.handle_async_request()` 從 `request.extensions["timeout"]["connect"]` 取值傳進去——這正是 `httpx.Client.build_request()` 幫每個請求組好的 `Timeout.as_dict()`，也就是呼叫端原本設定的那個連線逾時，而不是另外發明一個新常數；沒有這個 extension 的請求（例如測試手動建構的 `httpx.Request`）維持原本的不設限行為。已對著實際安裝的 httpx（0.28.1）驗證過 `build_request()` 確實會填好這個 extension，外加涵蓋「逾時會提前中止」與「transport 正確把值傳給 resolver」兩條回歸測試。

  第九輪（對第八輪修法本身又抓出的問題）：

  12. **P1 — `asyncio.wait_for()` 逾時後，執行緒池裡的阻塞查詢還在跑，會塞滿整個執行緒池**。第八輪的 `asyncio.wait_for(to_thread(...), timeout)` 只是不再*等待*那個 `to_thread()` 工作——`socket.getaddrinfo()` 沒有任何合作式取消點，逾時後它仍在原本被丟進去的那個執行緒裡繼續跑，直到系統 resolver 自己放棄為止。`asyncio.to_thread()` 一律把工作丟進 process 共用的預設執行緒池（大小通常是 `min(32, cpu核數+4)`），而這個池是全 app 每一處 `to_thread()` 呼叫共用的。攻擊者只要開多個並發的公開 `/api/discover` 請求、各自指向回應很慢或乾脆不回的 DNS，被放棄的查詢就會逐一佔滿那個共用池的每一條執行緒——之後不管是同一支程式碼對健康 feed 的解析，還是 app 裡任何其他呼叫 `asyncio.to_thread()` 的阻塞工作，都會被排在那些已被放棄、卻還占著執行緒的查詢後面，遲遲拿不到執行緒可以真正開始跑。修法：新增專屬、大小固定（`DNS_RESOLVER_MAX_WORKERS = 8`）的 `concurrent.futures.ThreadPoolExecutor`（`_dns_resolver_executor`），`_resolve_pinned_ips()` 改用 `loop.run_in_executor(_dns_resolver_executor, socket.getaddrinfo, host, None)` 而非 `asyncio.to_thread()`（後者固定用預設執行緒池，沒有指定 executor 的介面）。這不能讓被放棄的查詢真正消失——沒有任何純 Python 手段能安全中斷一個正在執行的 blocking 系統呼叫——但把占用範圍限制在這一個小池子裡，不會波及 app 裡其他所有共用預設執行緒池的阻塞工作。新增回歸測試：先用 `threading.Barrier` 讓 `DNS_RESOLVER_MAX_WORKERS` 個假造的慢查詢確實佔滿專屬池的每一條執行緒，再斷言一個不相關的 `asyncio.to_thread()` 呼叫仍然立刻完成，證明兩個池互不影響。

### #26 — `validate_fetch_url()` 本身仍同步阻塞事件迴圈（08-02）

- **問題**：#25 第七輪的教訓明確記下「`validate_fetch_url()` / `_is_safe_host()` 呼叫 `socket.getaddrinfo()` 也有同樣的既有問題，但… 呼叫點遍布整個專案… 不在本次修法範圍」——那次只修了 `PinnedTransport` 新增的**第二道**解析（pin-and-connect 用的那次），沒有動**第一道**：`validate_fetch_url()` 本身。它在每一條抓取路徑最前面同步呼叫 `socket.getaddrinfo()`，包括完全公開、免認證的 `POST /api/discover`、`POST /api/discover/import`——這是所有 fetch 路徑的**第一個**動作，比 `PinnedTransport` 早執行。攻擊者只要指向一個回應很慢（或乾脆不回應）的 DNS 伺服器，一次請求就能讓整個 worker 的事件迴圈卡住，波及所有其他使用者的請求，是 #25 第七輪同一個服務層級 DoS，只是換了個尚未修補的呼叫點。
- **修法**：把 `_is_safe_host()` 改成 `async def`，解析透過 `_resolve_pinned_ips()` 已在用的同一個專屬、固定大小的 `_dns_resolver_executor`（`loop.run_in_executor(...)`），而非直接在事件迴圈裡呼叫，並帶上可選的 `timeout`（新常數 `DNS_VALIDATION_TIMEOUT_SECONDS = 10.0`——這裡沒有一個現成的、呼叫端配置好的連線逾時可以借用：`validate_fetch_url()` 執行時通常還沒有 httpx client 或 request 物件存在，部分呼叫端如 `routers/opml.py` 的逐 outline 迴圈甚至沒有 `timeout` 變數在作用域內）。`validate_fetch_url()` 隨之改成 `async def`，兩個既有呼叫端（`fetch_with_cap_response()`、`discover_feeds()`）改傳入各自函式簽章上原本就有的 `timeout` 參數；其餘沒有現成 timeout 可傳的呼叫端（`services/feed_refresh.py`、`services/discovery_probe.py`、`routers/admin.py`、`routers/admin_discovery.py`、`routers/discover.py`、`routers/opml.py`）維持用預設值，全部 12 個呼叫點都已在 `async def` 函式裡，只需加上 `await`。
- **不動的部分**：`_is_safe_host()` / `validate_fetch_url()` 的判斷邏輯（private / loopback / link-local / multicast / reserved 檢查）與 #25 記載的 pin-and-connect（`PinnedTransport` 的第二道獨立解析）完全不動——這次只是把既有的第一道門從同步搬成非同步，不改變它判斷什麼、也不改變它與 pin-and-connect 之間「兩道各自獨立的門」的關係。
- **測試**：`backend/tests/test_feed_discovery.py` 新增兩個回歸測試，沿用 #25 第九輪驗證阻塞行為的手法——`test_is_safe_host_runs_dns_resolution_off_the_event_loop` 用 `threading.Event` 讓假造的慢 `getaddrinfo()` 卡住，斷言事件迴圈仍能同時完成一個無關的 `asyncio.to_thread()` 工作，並斷言解析確實發生在 `pinned-dns-resolve` 執行緒（`_dns_resolver_executor` 的 thread name prefix），不是事件迴圈本身；`test_is_safe_host_rejects_dns_timeout` 斷言逾時回傳 `False`（`validate_fetch_url()` 因此拋 `DiscoveryError`），不是掛住或洩漏例外。既有測試（`test_normalize_url_*`、`test_is_safe_host_rejects_oversized_label_without_crashing`）全部改成 `async def` + `await`——`unittest.mock.patch()` 對已改成 `async def` 的目標會自動改用 `AsyncMock`，既有的 `return_value=` / `side_effect=`（含同步函式與例外實例）不必跟著改寫就能繼續正確運作。沒有網路能在這個 sandbox 安裝依賴跑 `pytest`（與 #25 同樣的既有限制），改用 `python3 -m py_compile` 與 `ruff check` 驗證語法與風格。

### #27 — `MaxBodySizeMiddleware` 只擋 `Content-Length`，chunked body 能繞過 6 MiB 上限（08-04）

- **問題**：#17 補上的 body size 上限只檢查請求宣告的 `Content-Length` header，在 body 被讀取前擋掉超額請求。它從一開始就記錄了這個已知限制：以 chunked transfer-encoding 送出、不帶 `Content-Length` 的請求完全不受這道檢查約束。FastAPI/Starlette 在任何 route 或 pydantic 驗證執行前，仍會把整個 body 緩衝進記憶體——公開免認證的 `POST /api/discover`、`POST /api/discover/import` 因此仍是無上限的記憶體耗盡向量，只是換了個不帶 `Content-Length` 的請求形狀而已。
- **第一版修法（CI 抓到問題）**：最初讓包裝過的 `receive()` 逐則 ASGI `http.request` 訊息累加位元組數，超過 `MAX_REQUEST_BODY_BYTES` 就拋自訂例外，指望它一路冒出 `self.app(...)` 讓外層 `try/except` 接住並回 413。這個假設是錯的：FastAPI 的 body 解析（`request.json()` / `request.body()` 那段）本身包了一層寬鬆的 `except Exception`，把讀 body 過程中冒出的任何例外（含這個自訂例外）都吞掉、轉成它自己的 `HTTPException(400, "There was an error parsing the body")` 送出——這是本專案沒有網路裝 `pytest` 前一直沒能實際跑過這條路徑而漏看的細節。CI（`.github/workflows/backend.yml` 的 `Test` job）第一次真的裝了完整依賴跑測試，新測試斷言 413 卻收到 400，當場抓到。
- **修法**：不再依賴例外能不能原封不動地穿過 FastAPI 內部、活到我們自己的 `except` 為止——那是依賴套件的實作細節，不是這個介面承諾過的行為。改成：`limited_receive()` 累計超過上限時，不拋例外，而是回傳 `{"type": "http.disconnect"}`（之後每次呼叫都直接回同一個訊息，不再碰真正的 `receive`）；同時包一層 `guarded_send()`，一旦進入「已超額」狀態就吞掉 app 想送出的每一則訊息（不管是 FastAPI 自己那個 400、或任何其他錯誤處理器的產物），確保它們都到不了真正的 ASGI channel。`self.app(...)` 呼叫外圍仍有 `try/except Exception`（超額時吞掉、非超額時照常往外拋，維持既有的錯誤處理行為不變），但 413 真正送出的地方是 `finally` 區塊：只要判定超額而 `guarded_send` 期間沒有任何回應真的送出去過，就用**原始、未包裝**的 `send` 送出唯一、真正抵達 client 的 413。`Content-Length` 過大時仍走原本「讀 body 前」的早期回絕路徑，不受此次改動影響。
- **測試**：`backend/tests/test_main.py` 新增 `test_oversized_chunked_body_without_content_length_rejected`——用產生器（generator）當 `httpx` 的請求內容，讓 client 端不知道長度而不送出 `Content-Length`；斷言收到 `413`、送出的請求確實沒有 `content-length` header（證明真的走了新的路徑而非舊的 header 檢查），且 `mock_db.table` 從未被呼叫（route 邏輯沒被觸發）。這個測試在 CI 裡實際跑過兩次：第一版實作跑出 `assert 400 == 413` 失敗，改用 send-guard 設計後重推、CI 轉綠，是這一輪唯一一次能在裝有完整依賴的環境下驗證過（而非只靠推理）的部分。沒有網路能在本 sandbox 安裝依賴，改用 CI 本身當驗證環境，本機仍以 `python3 -m py_compile` 與 `ruff check` 驗證語法與風格。
- **已知且刻意的範圍界線（同一輪 code review 抓到）**：串流計數只看得到 route 真的去要的 bytes——沒有宣告 body 欄位的 route（`GET /api/health`、或任何在 body 欄位被解析**之前**就被 auth dependency 擋下的請求）從頭到尾不會呼叫 `receive()`，宣告了一個超大 chunked body 但沒人讀它，不會被這道檢查轉成 413。**沒有跟進到「不管 route 讀不讀都主動把 stream 榨乾」**，是刻意的取捨而非漏改：要做到那樣，這個 middleware 得在背景搶著把 ASGI 的 `receive`（原本是單一消費者的 channel）讀乾淨，同時還要跟 app 自己可能發生的 `receive()` 呼叫協調誰先拿到哪一則訊息——複雜度與潛在的訊息順序 bug，遠不成比例地換來一個已經被其他層擋住的風險：本專案的 `api` 容器從不對外開 port（見 `main.py` 的 `ProxyHeadersMiddleware` 註解），唯一入口是 nginx，而 `frontend/nginx.conf` 沒有覆寫 `client_max_body_size`，代表 nginx 用內建預設值 **1 MiB**（比這裡的 6 MiB 更嚴）先擋一輪，chunked 與一般請求一視同仁；本機開發或未來若有其他方式直接打中 backend，才會真的碰到這個邊界。

### #28 — `GET /api/recommendations` 的 `liked` / `disliked` 未驗證格式，非法值變成未接住的 500（08-05）

- **問題**：`feeds.id` 是 `UUID` 欄位（`migrations/001_initial_schema.sql`）。本專案其他每個接 feed/article id 的端點（`/feeds/{feed_id}`、`/articles/{article_id}`、`/me/*`）都把該參數宣告成 `UUID`，FastAPI/pydantic 因此會在任何 DB 呼叫之前就把格式錯誤的值擋成乾淨的 `422`。唯獨 `GET /api/recommendations` 的 `liked` / `disliked` 兩個 query 參數宣告成 `list[str]`，只限制了陣列長度（`max_length=50`），沒有限制每個字串本身要長得像 UUID。這個端點是公開免認證（`get_optional_user`）、有 rate limit 但沒有其他門檻，任何人都能打。像 `GET /api/recommendations?liked=not-a-uuid` 這樣的請求會直接帶著這個字串走到 `.table("feeds").in_("id", liked)`，以及 `_sample_feeds()` 傳給 `sample_feed_candidates` RPC（migration 007）、型別是 `uuid[]` 的 `p_excluded_ids` 參數——兩處都會讓 Postgres 端擲出 cast 例外，而 `backend/` 全專案沒有任何一個地方對 `postgrest`/`APIError` 掛 `exception_handler`，未接住的例外變成一個裸的 FastAPI `500`，而不是輸入驗證該給的 `4xx`。
- **和既有紀錄的差異**：#14 / #25 的 filter-injection 是「字面上合法的字串裡挾帶 `,`／`(`／`)` 等 PostgREST 保留字元、扭曲查詢語意」；這裡是另一種失效模式——字串格式本身就通不過型別轉換，會在 DB 端直接炸掉，不是查詢語意被劫持。#20 是「`.single()` 對查無資料回的例外沒接住」，成因是查詢結果為空，不是請求輸入本身沒驗證，且該處已經修過；這個端點的 `liked`/`disliked` 缺口在那次修補範圍之外，一直沒補。
- **修法**：`liked`、`disliked` 的型別從 `list[str]` 改成 `list[UUID]`，比照本專案其他所有 id 參數的既有慣例——FastAPI 在路由函式執行前就會驗證每個元素，格式錯誤直接回 `422`，兩個 DB 呼叫點都不會被觸發。兩個實際使用處（組 `excluded` 集合、`.in_("id", ...)`）改成呼叫端呼叫 `str(u)` 轉回字串，同樣沿用其餘 router 一貫的 `str(feed_id)` 寫法。
- **測試**：`backend/tests/test_recommendations.py` 新增 `test_malformed_id_in_liked_or_disliked_is_rejected`（對 `liked`、`disliked` 各跑一次），斷言帶入 `not-a-uuid` 回 `422`，且 `mock_db.table` / `mock_db.rpc` 都沒被呼叫過——證明是請求驗證擋下，不是等 DB 呼叫失敗才處理。沒有網路能在本 sandbox 安裝依賴跑 `pytest`（與 #25/#26/#27 同樣的既有限制），改用 `python3 -m py_compile` 與 `ruff check` 驗證語法與風格，實際執行結果交給 CI（`.github/workflows/backend.yml` 的 `Test` job）驗證。

## 目前的防線總覽

| 層 | 機制 | 位置 |
|----|------|------|
| Request 入口 | `Content-Length` > 6 MiB → 413（讀 body 前）；未宣告或宣告不實時改由串流位元組計數兜底，超額同樣 413（見 #27）——最外層 middleware | `main.py::MaxBodySizeMiddleware` |
| Client 識別 | `ProxyHeadersMiddleware(trusted_hosts="*")` + nginx `X-Forwarded-For $remote_addr` | `main.py`、`frontend/nginx.conf` |
| 濫用防護 | 每 IP 每端點 20 req / 60s，追蹤上限 10k clients | `rate_limit.py` |
| 外連目標 | `fetch_with_cap()` 對**初始 URL 與每個 redirect hop**都跑 `validate_fetch_url()`，阻擋私網 / loopback / link-local——本身是 `async def`，解析走專屬、固定大小的執行緒池且受逾時約束，不會卡住事件迴圈（見 #26）；傳輸層 `PinnedTransport` 對實際連線目標做獨立的單次解析並驗證，關閉 #22 記載的 DNS rebinding check-then-fetch 缺口；該解析同樣受呼叫端配置的連線逾時約束，且在同一個專屬、固定大小的執行緒池執行，逾時後即使查詢仍在跑也不會佔用 app 其他地方共用的預設執行緒池（見 #25） | `services/feed_discovery.py` |
| 爬取政策 | `allow_url` hook 掛在同一個 choke point，對初始 URL 與每個 hop 評估 denylist ∧ robots（在 SSRF gate 之後）| `services/feed_discovery.py`、`discovery_probe.py::_make_gate` |
| 外連大小 | `fetch_with_cap()` 串流 + 5 MiB（所有對外抓取共用）；robots.txt 另限 512 KiB | `services/feed_discovery.py`、`services/robots.py` |
| 自主爬取 | 總開關預設關；批次 / 並發 / per-host 延遲上限；robots 遵循；`POST /admin/discovery/run` 在停用時回 503 | `services/discovery_config.py`、`routers/admin_discovery.py` |
| XML 解析 | 全數 `defusedxml`（feed、OPML 上傳、遠端 OPML 目錄三條路徑） | `rss_parser.py`、`routers/opml.py`、`services/directory_sources.py` |
| DB 查詢 | `escape_postgrest_literal()`、`.in_()` 取代手拼 filter、`.maybe_single()`；第三方字串一律不進 filter；id 類參數一律宣告 `UUID` 型別，格式錯誤在進 DB 呼叫前就回 422（見 #28） | `utils.py`、`routers/*`、`services/link_harvest.py::HostIndex` |
| 第三方文字落庫 | `sanitize_text()`（控制字元 / 零寬 / bidi override）、`sanitize_http_url()`（強制 http(s)）| `services/discovery_candidates.py` |
| 認證 | Supabase JWT（`SUPABASE_JWT_SECRET`）；admin 用 `X-API-Key` | `auth.py`、`routers/admin.py` |
| 資料存取 | 四張 `user_*` 表 RLS owner-only；`feeds` / `articles` RLS + public read；四張 `discovery_*` 表 RLS + **零 policy**（僅 service_role） | `migrations/002`、`004`、`006` |
| 錯誤訊息 | 不回傳原始外連例外文字 | `routers/discover.py`、`routers/admin.py` |

## 改動時要注意的事

1. **新增任何會抓取使用者提供 URL 的程式碼** → 必須先 `validate_fetch_url()`，並透過 `fetch_with_cap()` / `fetch_and_parse()` 取得內容，不要自己寫 `httpx.get()`。
2. **新增任何 XML 解析** → 用 `defusedxml`，不要用 `xml.etree.ElementTree`。
3. **新增任何 PostgREST filter** → 用 `.in_()` / `.eq()` 等結構化 API；非不得已才用 `.or_()`，並且值一定過 `escape_postgrest_literal()`。
4. **「可能查不到」的單筆查詢** → 用 `.maybe_single()`，不要用 `.single()`。
5. **新增公開免認證端點** → 評估是否要掛 `rate_limit(...)`，特別是會觸發外連或大量 DB 工作的端點。
6. **測試用的 db mock** → 避免裸 `MagicMock()` 讓錯誤的方法名靜默通過（#20 的教訓）。
7. **環境變數** → 依 `CLAUDE.md` 規則同步 `.env.example`、`docker-compose.yml`、`scripts/gen_env.py` 三處（#15 就是漏了 compose 那一處）。
8. **新增任何「自主」（非使用者觸發）的外連迴圈** → 必須有獨立的 enable flag 且**預設關閉**、批次與並發上限、per-host 延遲，並經過 robots 檢查。對應的手動觸發端點要一起尊重那個 flag，否則「已停用」是個沒有意義的說法。
9. **新增任何抓取政策**（denylist、robots、allowlist）→ 掛在 `fetch_with_cap_response()` 的 `allow_url` hook 上，不要在各 call site 各寫一份。#22 與 #24 第 4 點是同一個教訓的兩次出現。
10. **任何寫入公開表的第三方文字** → 先過 `sanitize_text()` / `sanitize_http_url()`，前端只能用插值呈現，並且把我們自己算出的識別資訊（host）顯示在旁邊。
11. **任何用 `asyncio.to_thread()` 把阻塞呼叫丟到執行緒池的地方，若原本的呼叫受某個逾時約束** → 記得把那個逾時一併帶進 `asyncio.wait_for(...)`，不要假設「換成 to_thread 就自動安全」（#25 第八輪的教訓：卡住事件迴圈的問題解決了，但無界等待的問題還在，只是換了個形狀）。
12. **任何 `asyncio.wait_for(...)` 包住的阻塞工作，若逾時後底層呼叫仍可能繼續佔用執行緒** → 評估是否該走專屬、大小固定的 `ThreadPoolExecutor`（`loop.run_in_executor(executor, ...)`）而非 `asyncio.to_thread()`（一律用 process 共用的預設池），把可能累積的占用範圍限制在一個小池子裡，而不是波及 app 其他所有共用預設執行緒池的阻塞工作（#25 第九輪的教訓）。
