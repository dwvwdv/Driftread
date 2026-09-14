-- 全文搜尋（TODO.md P2「全文搜尋」）：文章標題／摘要／作者／全文，以及 Feed 名稱／描述，
-- 各自獨立的 tsvector + GIN index，取代 routers/feeds.py 既有 `search` 參數那種
-- `ilike '%keyword%'`（無法用一般 btree index，大表會整表掃描）。
--
-- 語言設定刻意固定用 'simple'（不對 English 等語言做 stemming），原因：
--   1. tsvector 的 lexeme 是依建立時的 regconfig 決定的（例如 'english' 會把
--      "running" 存成詞幹 "run"），查詢端的 tsquery 也必須用同一個 regconfig 才能
--      命中；Driftread 的文章橫跨多個 feed、多種語言混在同一次搜尋結果裡，沒有單一
--      查詢請求能同時對「這批文件各自用不同 regconfig 建的 tsvector」都選對 config。
--   2. Driftread 目前有語言偵測的 zh／ja／ko／th／vi 等，Postgres 內建並沒有對應的
--      斷詞字典（需要額外擴充套件），本來就只能退回不斷詞的 'simple'。
-- 統一用 'simple'，讓「建立索引」與「查詢」兩端永遠用同一個 config，行為在所有語言
-- 下一致且可預期——這就是這個項目「無法可靠斷詞時提供可預測的 fallback」的落地方式；
-- 真正逐語言 stemming（例如另外開分語言的 tsvector 欄位）留到之後有需要再做。
--
-- articles.search_vector 用 GENERATED ALWAYS AS ... STORED：'simple' 是常數
-- regconfig，to_tsvector(regconfig, text) 兩參數版本是 IMMUTABLE（不同於只帶文字的
-- 單參數版本依賴 session 的 search_path，是 STABLE），符合 generated column 的要求。
-- 用 generated column 而不是 migration 015／016 那種 trigger 寫法，是因為這裡不需要
-- 跨資料表查 feed 語言（config 固定），Postgres 自己維護欄位值更簡單、也不會漏更新。
--
-- to_tsvector／ts_headline 對輸入文字有實際大小限制（Postgres 文件：序列化後的
-- tsvector 不能超過約 1 MiB，超過會丟出 "string is too long for tsvector"）。
-- `articles.content` 沒有任何欄位層級的長度上限——只有抓取階段整個 feed 下載量的
-- 5 MiB 上限（見 rss_parser.py），單篇文章的內文理論上可以逼近甚至超過這個 tsvector
-- 限制。一旦真的發生，這個 GENERATED 欄位的計算會直接失敗：新文章寫入失敗，或者
-- （更糟）這個 migration 替既有資料回填這個欄位時整個 migration 失敗、擋住後端啟動。
-- `bounded_search_text()` 把送進 to_tsvector／ts_headline 的文字統一截到 100,000
-- 字元——就算整段都是最極端的 4-byte UTF-8 字元也只有 400 KB，遠低於 1 MiB 上限，
-- 而且沒有任何真實搜尋情境需要比這更後面的內文才能命中。
CREATE OR REPLACE FUNCTION driftread.bounded_search_text(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $$
  SELECT left(coalesce(p_text, ''), 100000)
$$;

-- `articles.content` deliberately keeps the raw HTML (rss_parser.py's
-- `_inner_html`) for the reader page's `[innerHTML]` rendering — unlike
-- `title`/`summary`/`author`, which are already the plain-text output of
-- `rss_parser.py::_plain_text()`. Feeding that raw HTML straight into
-- to_tsvector makes tag names, attributes, class names and URLs inside
-- markup into searchable lexemes: a query for "href" or some CSS class
-- name would match articles whose rendered text never contains that word,
-- and repeated boilerplate markup skews `ts_rank_cd`. This does not try to
-- match `_plain_text()`'s fidelity (block-tag-aware spacing, entity
-- decoding) — it is only search-index preprocessing, not reader-facing
-- text — just replace each tag with a space (not drop it outright, so
-- "...sentence.</p><p>Next" doesn't glue into one word) before it reaches
-- to_tsvector/ts_headline.
--
-- A naive `<[^>]*>` stops at the *first* `>`, including one that's just
-- data inside a quoted attribute value (`<a title="2 > 1" href="...">`) —
-- that leaves everything from the quoted `>` to the tag's real closing `>`
-- (here, ` 1" href="...">`) behind as literal indexed/headlined text, so a
-- search can still hit an invisible `href` or attribute value. The
-- alternation below mirrors frontend/src/app/shared/html.ts's quote-aware
-- ATTRS/TAG_RE (same problem, same fix, translated to SQL's regex flavor):
-- match an HTML comment whole, or a tag whose attributes are matched
-- quote-aware (a `"..."`/`'...'` run is one unit regardless of `>` inside
-- it), falling back to the naive form only for a tag with an unbalanced
-- quote the quote-aware alternative can't otherwise match.
--
-- A tag-only replacement leaves a <script>/<style> element's *body* behind
-- as if it were ordinary visible text — JS identifiers, CSS selectors and
-- property values would become searchable even though nothing on the
-- rendered page shows them. rss_parser.py's own `_plain_text()` already
-- special-cases this (`_DROP_WHOLE_RE`, matched before its generic tag
-- strip) for the same reason; the first regexp_replace below mirrors it —
-- drop the whole element, tags and content together, case-insensitively,
-- before the second pass strips whatever ordinary tags remain. (Postgres's
-- default, non-newline-sensitive matching already makes `.` match a
-- newline, so `.*?` alone spans a multi-line `<script>` body without a
-- separate "dotall" flag.)
CREATE OR REPLACE FUNCTION driftread.strip_html_for_search(p_html text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $$
  SELECT regexp_replace(
    regexp_replace(
      coalesce(p_html, ''),
      '<(script|style)\b(?:[^>"'']|"[^"]*"|''[^'']*'')*>.*?</\1\s*>',
      ' ',
      'gi'
    ),
    '<!--.*?-->|</?[a-zA-Z](?:[^>"'']|"[^"]*"|''[^'']*'')*>|</?[a-zA-Z][^>]*>',
    ' ',
    'g'
  )
$$;

ALTER TABLE driftread.articles
  ADD COLUMN IF NOT EXISTS search_vector tsvector
  GENERATED ALWAYS AS (
    to_tsvector(
      'simple',
      driftread.bounded_search_text(
        coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' ||
        coalesce(author, '') || ' ' || driftread.strip_html_for_search(content)
      )
    )
  ) STORED;

-- `feeds.description` isn't guaranteed plain text either: rss_parser.py's
-- `_text()` (used for the channel-level description) just returns the
-- element's decoded text content, unlike `_plain_text()` — a publisher
-- that escapes markup in `<description>` ends up with real `<...>` HTML
-- after XML unescaping. Same treatment as articles.content, for the same
-- reason (PR #59 review, P2).
ALTER TABLE driftread.feeds
  ADD COLUMN IF NOT EXISTS search_vector tsvector
  GENERATED ALWAYS AS (
    to_tsvector(
      'simple',
      driftread.bounded_search_text(
        coalesce(title, '') || ' ' || driftread.strip_html_for_search(description)
      )
    )
  ) STORED;

CREATE INDEX IF NOT EXISTS articles_search_vector_idx
  ON driftread.articles USING GIN (search_vector);

CREATE INDEX IF NOT EXISTS feeds_search_vector_idx
  ON driftread.feeds USING GIN (search_vector);

-- 文章搜尋：標題／摘要／作者／全文，回傳來源（feed_title）、命中摘要片段
-- （ts_headline，從 summary／content 兩者中實際命中查詢的那一個取，見最外層 SELECT 的
-- CASE 說明）、日期，以及呼叫者已登入時的已讀／收藏狀態——與 list_feed_articles
-- （migration 016）同一套 LEFT JOIN 做法。
-- p_language 可選，narrowing 到單一語言的 feed（沿用 GET /feeds?language= 同樣的欄位），
-- 不影響 tsvector／tsquery 的 config（兩者永遠是 'simple'，見上）。排除已封存來源的文章
-- （同 search_feeds、GET /feeds 既有行為——封存承諾操作者「不再出現在前台」）。
--
-- 排序／分頁鍵是 (rank, sort_at, id) 三欄，不是既有 keyset 分頁的 (sort_at, id) 兩欄：
-- 相關度是主要排序依據，但同一次查詢常有多篇文章拿到相同 rank（例如都只命中一次
-- 同一個詞），需要 sort_at／id 當決勝——與 list_reading_stream 的
-- COALESCE(published_at, fetched_at) DESC, id DESC 同一套「未解析日期退回抓取時間」
-- 邏輯，只是前面多疊一層 rank。rank 由 ts_rank_cd 對同一份 tsquery、同一個 search_vector
-- 計算而來，同樣輸入必定算出同樣的 real 值，用它比對 cursor 是確定性的。
--
-- 三層 CTE 而不是單層：`ranked` 只算 rank（cheap，且是 a.search_vector @@ query.tsq 這個
-- GIN index 已經篩過的列才會算），`paged` 對 `ranked` 的結果做 cursor 篩選＋排序＋
-- LIMIT，最外層才對「已經 LIMIT 過的那最多 100 列」呼叫 ts_headline——ts_headline 要重新
-- 掃過整段摘要／內文找命中片段，比 ts_rank_cd 貴得多，一個熱門關鍵字命中幾千篇文章時，
-- 不應該對每一篇都算一次 headline，只有真的會回傳給呼叫端的那一頁才需要。
CREATE OR REPLACE FUNCTION driftread.search_articles(
  p_query           text,
  p_user_id         uuid DEFAULT NULL,
  p_language        text DEFAULT NULL,
  p_cursor_rank     real DEFAULT NULL,
  p_cursor_sort_at  timestamptz DEFAULT NULL,
  p_cursor_id       uuid DEFAULT NULL,
  p_limit           int DEFAULT 20
)
RETURNS TABLE(
  id            uuid,
  feed_id       uuid,
  feed_title    text,
  title         text,
  url           text,
  summary       text,
  snippet       text,
  author        text,
  published_at  timestamptz,
  fetched_at    timestamptz,
  is_read       boolean,
  is_bookmarked boolean,
  rank          real
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
  WITH query AS (
    SELECT websearch_to_tsquery('simple', p_query) AS tsq
  ),
  ranked AS (
    SELECT
      a.id, a.feed_id, f.title AS feed_title, a.title, a.url, a.summary, a.content,
      a.author, a.published_at, a.fetched_at,
      (r.article_id IS NOT NULL) AS is_read,
      (b.article_id IS NOT NULL) AS is_bookmarked,
      ts_rank_cd(a.search_vector, query.tsq) AS rank,
      query.tsq AS tsq
    FROM driftread.articles a
    JOIN driftread.feeds f ON f.id = a.feed_id
    CROSS JOIN query
    LEFT JOIN driftread.user_article_reads r
      ON r.article_id = a.id AND r.user_id = p_user_id
    LEFT JOIN driftread.user_bookmarks b
      ON b.article_id = a.id AND b.user_id = p_user_id AND b.bookmark_type = 'favorite'
    WHERE a.search_vector @@ query.tsq
      -- Archiving a feed promises the operator it "no longer appears on the
      -- frontend" (admin-feeds.ts's confirm dialog) — search_feeds already
      -- excludes archived feeds for the same reason; without this, an
      -- archived source's articles stayed fully searchable/discoverable
      -- through this new public endpoint.
      AND f.archived_at IS NULL
      AND (p_language IS NULL OR f.language = p_language)
  ),
  paged AS (
    SELECT *
    FROM ranked
    WHERE
      p_cursor_rank IS NULL
      OR rank < p_cursor_rank
      OR (rank = p_cursor_rank AND COALESCE(published_at, fetched_at) < p_cursor_sort_at)
      OR (
        rank = p_cursor_rank AND COALESCE(published_at, fetched_at) = p_cursor_sort_at
        AND id < p_cursor_id
      )
    ORDER BY rank DESC, COALESCE(published_at, fetched_at) DESC, id DESC
    LIMIT LEAST(GREATEST(p_limit, 1), 100)
  )
  SELECT
    id, feed_id, feed_title, title, url, summary,
    -- The match that made this row show up at all could be in the title,
    -- author, summary or content — search_vector is one combined vector
    -- over all four. Headlining a fixed "summary, else content" source
    -- regardless of where the hit actually landed can show a snippet with
    -- no highlighted term at all (e.g. a non-empty summary when the query
    -- only matched the content). Check each candidate body field's own
    -- (bounded, same as the generated column) tsvector against the same
    -- tsquery and headline whichever one actually matched; when neither
    -- does (the hit was in title/author only), fall back to the old
    -- default — ts_headline on non-matching text is not an error, it just
    -- renders as an unhighlighted leading excerpt.
    ts_headline(
      'simple',
      CASE
        WHEN to_tsvector('simple', driftread.bounded_search_text(summary)) @@ tsq
          THEN driftread.bounded_search_text(summary)
        WHEN to_tsvector(
          'simple', driftread.bounded_search_text(driftread.strip_html_for_search(content))
        ) @@ tsq
          THEN driftread.bounded_search_text(driftread.strip_html_for_search(content))
        ELSE driftread.bounded_search_text(
          coalesce(nullif(summary, ''), driftread.strip_html_for_search(content), '')
        )
      END,
      tsq,
      'MaxFragments=1,MaxWords=35,MinWords=15,ShortWord=3,HighlightAll=false'
    ) AS snippet,
    author, published_at, fetched_at, is_read, is_bookmarked, rank
  FROM paged
$$;

-- Feed 搜尋：名稱／描述，與文章搜尋分開呈現（TODO.md 明列的要求），回傳命中摘要片段與
-- 相關度排序，排除已封存來源（同 GET /feeds 既有行為）。同上，ts_headline 只在最外層對
-- 已經分頁過的結果呼叫。
CREATE OR REPLACE FUNCTION driftread.search_feeds(
  p_query             text,
  p_language          text DEFAULT NULL,
  p_cursor_rank       real DEFAULT NULL,
  p_cursor_created_at timestamptz DEFAULT NULL,
  p_cursor_id         uuid DEFAULT NULL,
  p_limit             int DEFAULT 20
)
RETURNS TABLE(
  id            uuid,
  title         text,
  url           text,
  description   text,
  snippet       text,
  website_url   text,
  language      text,
  category      text,
  tags          text[],
  article_count int,
  created_at    timestamptz,
  rank          real
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
  WITH query AS (
    SELECT websearch_to_tsquery('simple', p_query) AS tsq
  ),
  ranked AS (
    SELECT
      f.id, f.title, f.url, f.description, f.website_url, f.language, f.category,
      f.tags, f.article_count, f.created_at,
      ts_rank_cd(f.search_vector, query.tsq) AS rank,
      query.tsq AS tsq
    FROM driftread.feeds f
    CROSS JOIN query
    WHERE f.search_vector @@ query.tsq
      AND f.archived_at IS NULL
      AND (p_language IS NULL OR f.language = p_language)
  ),
  paged AS (
    SELECT *
    FROM ranked
    WHERE
      p_cursor_rank IS NULL
      OR rank < p_cursor_rank
      OR (rank = p_cursor_rank AND created_at < p_cursor_created_at)
      OR (rank = p_cursor_rank AND created_at = p_cursor_created_at AND id < p_cursor_id)
    ORDER BY rank DESC, created_at DESC, id DESC
    LIMIT LEAST(GREATEST(p_limit, 1), 100)
  )
  SELECT
    id, title, url, description,
    -- Only one candidate body field here (unlike search_articles), so no
    -- field-matched-vs-headlined mismatch to resolve — same HTML-stripping
    -- and size bound as the generated column (description isn't guaranteed
    -- plain text either, see above), for the same reasons.
    ts_headline(
      'simple',
      driftread.bounded_search_text(driftread.strip_html_for_search(description)),
      tsq,
      'MaxFragments=1,MaxWords=35,MinWords=15,ShortWord=3,HighlightAll=false'
    ) AS snippet,
    website_url, language, category, tags, article_count, created_at, rank
  FROM paged
$$;

REVOKE ALL ON FUNCTION driftread.search_articles(text, uuid, text, real, timestamptz, uuid, int)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.search_articles(text, uuid, text, real, timestamptz, uuid, int)
  TO service_role;

REVOKE ALL ON FUNCTION driftread.search_feeds(text, text, real, timestamptz, uuid, int)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.search_feeds(text, text, real, timestamptz, uuid, int)
  TO service_role;
