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
- **已知限制**：只檢查 `Content-Length` header，涵蓋一般 JSON / multipart client；以 chunked transfer-encoding 串流且不帶 `Content-Length` 的請求不在此檢查範圍。

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

---

## 目前的防線總覽

| 層 | 機制 | 位置 |
|----|------|------|
| Request 入口 | `Content-Length` > 6 MiB → 413（最外層 middleware） | `main.py::MaxBodySizeMiddleware` |
| Client 識別 | `ProxyHeadersMiddleware(trusted_hosts="*")` + nginx `X-Forwarded-For $remote_addr` | `main.py`、`frontend/nginx.conf` |
| 濫用防護 | 每 IP 每端點 20 req / 60s，追蹤上限 10k clients | `rate_limit.py` |
| 外連目標 | `fetch_with_cap()` 對**初始 URL 與每個 redirect hop**都跑 `validate_fetch_url()`，阻擋私網 / loopback / link-local | `services/feed_discovery.py` |
| 外連大小 | `fetch_with_cap()` 串流 + 5 MiB（所有對外抓取共用） | `services/feed_discovery.py` |
| XML 解析 | 全數 `defusedxml`（feed 與 OPML 兩條路徑） | `rss_parser.py`、`routers/opml.py` |
| DB 查詢 | `escape_postgrest_literal()`、`.in_()` 取代手拼 filter、`.maybe_single()` | `utils.py`、`routers/*` |
| 認證 | Supabase JWT（`SUPABASE_JWT_SECRET`）；admin 用 `X-API-Key` | `auth.py`、`routers/admin.py` |
| 資料存取 | 四張 `user_*` 表 RLS owner-only；`feeds` / `articles` RLS + public read | `migrations/002`、`004` |
| 錯誤訊息 | 不回傳原始外連例外文字 | `routers/discover.py`、`routers/admin.py` |

## 改動時要注意的事

1. **新增任何會抓取使用者提供 URL 的程式碼** → 必須先 `validate_fetch_url()`，並透過 `fetch_with_cap()` / `fetch_and_parse()` 取得內容，不要自己寫 `httpx.get()`。
2. **新增任何 XML 解析** → 用 `defusedxml`，不要用 `xml.etree.ElementTree`。
3. **新增任何 PostgREST filter** → 用 `.in_()` / `.eq()` 等結構化 API；非不得已才用 `.or_()`，並且值一定過 `escape_postgrest_literal()`。
4. **「可能查不到」的單筆查詢** → 用 `.maybe_single()`，不要用 `.single()`。
5. **新增公開免認證端點** → 評估是否要掛 `rate_limit(...)`，特別是會觸發外連或大量 DB 工作的端點。
6. **測試用的 db mock** → 避免裸 `MagicMock()` 讓錯誤的方法名靜默通過（#20 的教訓）。
7. **環境變數** → 依 `CLAUDE.md` 規則同步 `.env.example`、`docker-compose.yml`、`scripts/gen_env.py` 三處（#15 就是漏了 compose 那一處）。
