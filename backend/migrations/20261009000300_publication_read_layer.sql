-- #73 統一文章閱讀語義；來源角色／rights 先由 source migration 建立。
-- Raw articles 僅 backend 可讀，所有消费出口經同一 projection。
CREATE OR REPLACE VIEW driftread.article_publications WITH (security_invoker=true) AS
SELECT a.id, a.feed_id, a.title, a.url, display.summary,
 CASE WHEN f.fulltext_policy='rss' THEN a.content ELSE NULL::text END AS content,
 a.author, a.published_at, a.fetched_at, a.content_compacted_at,
 a.timeline_at, a.discovered_at, a.backfill, a.backfill_reason, a.current_revision_id,
 f.title AS feed_title, f.language AS feed_language, f.archived_at AS feed_archived_at,
 (f.fulltext_policy='rss') AS fulltext_allowed,
 CASE WHEN f.fulltext_policy='rss' THEN a.search_vector
 ELSE to_tsvector('simple', driftread.bounded_search_text(
 coalesce(a.title,'') || ' ' || coalesce(display.summary,'') || ' ' || coalesce(a.author,''))) END AS search_vector
FROM driftread.articles a JOIN driftread.feeds f ON f.id=a.feed_id
CROSS JOIN LATERAL (SELECT CASE WHEN f.fulltext_policy='rss' THEN a.summary
 ELSE left(driftread.strip_html_for_search(driftread.bounded_search_text(a.summary)),500) END AS summary) display
WHERE f.participation_mode='normal';
REVOKE ALL ON driftread.article_publications FROM PUBLIC, anon, authenticated;
GRANT SELECT ON driftread.article_publications TO service_role;
REVOKE SELECT ON driftread.articles FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION driftread.list_feed_publications(
  p_feed_id         uuid,
  p_user_id         uuid DEFAULT NULL,
  p_cursor_sort_at  timestamptz DEFAULT NULL,
  p_cursor_id       uuid DEFAULT NULL,
  p_limit           int DEFAULT 20
)
RETURNS TABLE(
  id            uuid,
  feed_id       uuid,
  title         text,
  url           text,
  summary       text,
  author        text,
  published_at  timestamptz,
  fetched_at    timestamptz,
  timeline_at   timestamptz,
  discovered_at timestamptz,
  backfill boolean,
  backfill_reason text,
  current_revision_id uuid,
  is_read       boolean,
  is_bookmarked boolean
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
  SELECT
    a.id, a.feed_id, a.title, a.url, a.summary, a.author,
    a.published_at, a.fetched_at, a.timeline_at, a.discovered_at, a.backfill, a.backfill_reason, a.current_revision_id,
    (r.article_id IS NOT NULL) AS is_read,
    (b.article_id IS NOT NULL) AS is_bookmarked
  FROM driftread.article_publications a
  LEFT JOIN driftread.user_article_reads r
    ON r.article_id = a.id AND r.user_id = p_user_id
  LEFT JOIN driftread.user_bookmarks b
    ON b.article_id = a.id AND b.user_id = p_user_id AND b.bookmark_type = 'favorite'
  WHERE a.feed_id = p_feed_id
    AND (
      p_cursor_sort_at IS NULL
      OR a.timeline_at < p_cursor_sort_at
      OR (a.timeline_at = p_cursor_sort_at AND a.id < p_cursor_id)
    )
  ORDER BY a.timeline_at DESC, a.id DESC
  LIMIT LEAST(GREATEST(p_limit, 1), 100)
$$;
REVOKE ALL ON FUNCTION driftread.list_feed_publications(uuid, uuid, timestamptz, uuid, int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_feed_publications(uuid, uuid, timestamptz, uuid, int) TO service_role;

CREATE OR REPLACE FUNCTION driftread.list_reading_publications(
  p_user_id          uuid,
  p_feed_id          uuid DEFAULT NULL,
  p_unread_only      boolean DEFAULT false,
  p_cursor_sort_at   timestamptz DEFAULT NULL,
  p_cursor_id        uuid DEFAULT NULL,
  p_limit            int DEFAULT 30
)
RETURNS TABLE(
  id           uuid,
  feed_id      uuid,
  feed_title   text,
  title        text,
  url          text,
  summary      text,
  author       text,
  published_at timestamptz,
  fetched_at   timestamptz,
  timeline_at  timestamptz,
  discovered_at timestamptz,
  backfill boolean,
  backfill_reason text,
  current_revision_id uuid,
  is_read      boolean,
  read_at      timestamptz
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
  SELECT
    a.id, a.feed_id, f.title, a.title, a.url, a.summary, a.author,
    a.published_at, a.fetched_at, a.timeline_at, a.discovered_at, a.backfill, a.backfill_reason, a.current_revision_id,
    (r.article_id IS NOT NULL) AS is_read,
    r.read_at
  FROM driftread.user_feeds uf
  JOIN driftread.article_publications a ON a.feed_id = uf.feed_id
  JOIN driftread.feeds f ON f.id = a.feed_id
  LEFT JOIN driftread.user_article_reads r
    ON r.article_id = a.id AND r.user_id = p_user_id
  WHERE uf.user_id = p_user_id
    AND uf.muted_at IS NULL
    AND (p_feed_id IS NULL OR a.feed_id = p_feed_id)
    AND (NOT p_unread_only OR r.article_id IS NULL)
    AND (
      p_cursor_sort_at IS NULL
      OR a.timeline_at < p_cursor_sort_at
      OR (a.timeline_at = p_cursor_sort_at AND a.id < p_cursor_id)
    )
  ORDER BY a.timeline_at DESC, a.id DESC
  LIMIT LEAST(GREATEST(p_limit, 1), 100)
$$;
REVOKE ALL ON FUNCTION driftread.list_reading_publications(uuid, uuid, boolean, timestamptz, uuid, int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_reading_publications(uuid, uuid, boolean, timestamptz, uuid, int) TO service_role;

CREATE OR REPLACE FUNCTION driftread.reading_stream_unread_counts(p_user_id uuid)
RETURNS TABLE(
  feed_id       uuid,
  feed_title    text,
  unread_count  bigint
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
  SELECT
    f.id,
    f.title,
    COUNT(a.id) FILTER (WHERE a.id IS NOT NULL AND r.article_id IS NULL)
  FROM driftread.user_feeds uf
  JOIN driftread.feeds f ON f.id = uf.feed_id
  LEFT JOIN driftread.article_publications a ON a.feed_id = f.id
  LEFT JOIN driftread.user_article_reads r
    ON r.article_id = a.id AND r.user_id = p_user_id
  WHERE uf.user_id = p_user_id
    AND uf.muted_at IS NULL
    AND f.participation_mode='normal'
  GROUP BY f.id, f.title
  ORDER BY f.title
$$;

CREATE OR REPLACE FUNCTION driftread.mark_reading_stream_read(
  p_user_id  uuid,
  p_feed_id  uuid DEFAULT NULL,
  p_before   timestamptz DEFAULT NULL
)
RETURNS TABLE(marked bigint)
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  affected bigint;
BEGIN
  INSERT INTO driftread.user_article_reads (user_id, article_id)
  SELECT p_user_id, a.id
  FROM driftread.user_feeds uf
  JOIN driftread.article_publications a ON a.feed_id = uf.feed_id
  WHERE uf.user_id = p_user_id
    AND (p_feed_id IS NULL OR a.feed_id = p_feed_id)
    AND (p_before IS NULL OR a.timeline_at <= p_before)
  ON CONFLICT (user_id, article_id) DO NOTHING;
  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN QUERY SELECT affected;
END;
$$;

CREATE OR REPLACE FUNCTION driftread.search_publications(
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
  timeline_at   timestamptz,
  discovered_at timestamptz,
  backfill boolean,
  backfill_reason text,
  current_revision_id uuid,
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
      a.author, a.published_at, a.fetched_at, a.timeline_at, a.discovered_at, a.backfill, a.backfill_reason, a.current_revision_id,
      (r.article_id IS NOT NULL) AS is_read,
      (b.article_id IS NOT NULL) AS is_bookmarked,
      ts_rank_cd(a.search_vector, query.tsq) AS rank,
      query.tsq AS tsq
    FROM driftread.article_publications a
    JOIN driftread.articles indexed ON indexed.id=a.id
    JOIN driftread.feeds f ON f.id = a.feed_id
    CROSS JOIN query
    LEFT JOIN driftread.user_article_reads r
      ON r.article_id = a.id AND r.user_id = p_user_id
    LEFT JOIN driftread.user_bookmarks b
      ON b.article_id = a.id AND b.user_id = p_user_id AND b.bookmark_type = 'favorite'
    WHERE indexed.search_vector @@ query.tsq
      AND a.search_vector @@ query.tsq
      AND f.archived_at IS NULL
      AND (p_language IS NULL OR f.language = p_language)
  ),
  paged AS (
    SELECT *
    FROM ranked
    WHERE
      p_cursor_rank IS NULL
      OR rank < p_cursor_rank
      OR (rank = p_cursor_rank AND timeline_at < p_cursor_sort_at)
      OR (
        rank = p_cursor_rank AND timeline_at = p_cursor_sort_at
        AND id < p_cursor_id
      )
    ORDER BY rank DESC, timeline_at DESC, id DESC
    LIMIT LEAST(GREATEST(p_limit, 1), 100)
  ),
  enriched AS (
    SELECT *, driftread.strip_html_for_search(driftread.bounded_search_text(content)) AS stripped_content
    FROM paged
  )
  SELECT
    id, feed_id, feed_title, title, url, summary,
    ts_headline(
      'simple',
      CASE
        WHEN to_tsvector('simple', driftread.bounded_search_text(summary)) @@ tsq
          THEN driftread.bounded_search_text(summary)
        WHEN to_tsvector('simple', stripped_content) @@ tsq
          THEN stripped_content
        ELSE driftread.bounded_search_text(coalesce(nullif(summary, ''), stripped_content, ''))
      END,
      tsq,
      'MaxFragments=1,MaxWords=35,MinWords=15,ShortWord=3,HighlightAll=false'
    ) AS snippet,
    author, published_at, fetched_at, timeline_at, discovered_at, backfill, backfill_reason, current_revision_id, is_read, is_bookmarked, rank
  FROM enriched
$$;
REVOKE ALL ON FUNCTION driftread.search_publications(text, uuid, text, real, timestamptz, uuid, int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.search_publications(text, uuid, text, real, timestamptz, uuid, int) TO service_role;
CREATE OR REPLACE FUNCTION driftread.list_bookmark_publications(p_user_id uuid, p_bookmark_type text)
RETURNS TABLE(id uuid, feed_id uuid, title text, url text, summary text, author text, published_at timestamptz, timeline_at timestamptz, discovered_at timestamptz, backfill boolean, backfill_reason text, current_revision_id uuid)
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT a.id,a.feed_id,a.title,a.url,a.summary,a.author,a.published_at,a.timeline_at,a.discovered_at,a.backfill,a.backfill_reason,a.current_revision_id
 FROM driftread.user_bookmarks b JOIN driftread.article_publications a ON a.id=b.article_id
 WHERE b.user_id=p_user_id AND b.bookmark_type=p_bookmark_type
 ORDER BY b.created_at DESC, b.article_id DESC
$$;
REVOKE ALL ON FUNCTION driftread.list_bookmark_publications(uuid,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_bookmark_publications(uuid,text) TO service_role;

CREATE OR REPLACE FUNCTION driftread.list_personal_publications(
 p_user_id uuid, p_start timestamptz DEFAULT NULL, p_end timestamptz DEFAULT NULL,
 p_exclude_backfill boolean DEFAULT false, p_limit int DEFAULT 100)
RETURNS SETOF driftread.article_publications
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT a.id,a.feed_id,a.title,a.url,a.summary,a.content,a.author,a.published_at,a.fetched_at,
 a.content_compacted_at,a.timeline_at,a.discovered_at,a.backfill,a.backfill_reason,a.current_revision_id,
 coalesce(uf.custom_title,a.feed_title),a.feed_language,a.feed_archived_at,a.fulltext_allowed,a.search_vector
 FROM driftread.article_publications a JOIN driftread.user_feeds uf ON uf.feed_id=a.feed_id
 WHERE uf.user_id=p_user_id AND uf.muted_at IS NULL
 AND (p_start IS NULL OR a.timeline_at >= p_start) AND (p_end IS NULL OR a.timeline_at < p_end)
 AND (NOT p_exclude_backfill OR NOT a.backfill)
 ORDER BY a.timeline_at DESC,a.id DESC LIMIT least(greatest(p_limit,1),500)
$$;
REVOKE ALL ON FUNCTION driftread.list_personal_publications(uuid,timestamptz,timestamptz,boolean,int) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_personal_publications(uuid,timestamptz,timestamptz,boolean,int) TO service_role;

-- Preserve the original RETURN contracts for a cleared-ledger full-chain replay.
CREATE OR REPLACE FUNCTION driftread.list_feed_articles(
 p_feed_id uuid, p_user_id uuid DEFAULT NULL, p_cursor_sort_at timestamptz DEFAULT NULL,
 p_cursor_id uuid DEFAULT NULL,p_limit int DEFAULT 20)
RETURNS TABLE(id uuid,feed_id uuid,title text,url text,summary text,author text,
 published_at timestamptz,fetched_at timestamptz,is_read boolean,is_bookmarked boolean)
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT id,feed_id,title,url,summary,author,published_at,fetched_at,is_read,is_bookmarked
 FROM driftread.list_feed_publications(p_feed_id,p_user_id,p_cursor_sort_at,p_cursor_id,p_limit)
$$;
CREATE OR REPLACE FUNCTION driftread.list_reading_stream(
 p_user_id uuid,p_feed_id uuid DEFAULT NULL,p_unread_only boolean DEFAULT false,
 p_cursor_sort_at timestamptz DEFAULT NULL,p_cursor_id uuid DEFAULT NULL,p_limit int DEFAULT 30)
RETURNS TABLE(id uuid,feed_id uuid,feed_title text,title text,url text,summary text,author text,
 published_at timestamptz,fetched_at timestamptz,is_read boolean,read_at timestamptz)
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT id,feed_id,feed_title,title,url,summary,author,published_at,fetched_at,is_read,read_at
 FROM driftread.list_reading_publications(p_user_id,p_feed_id,p_unread_only,p_cursor_sort_at,p_cursor_id,p_limit)
$$;
CREATE OR REPLACE FUNCTION driftread.search_articles(
 p_query text,p_user_id uuid DEFAULT NULL,p_language text DEFAULT NULL,p_cursor_rank real DEFAULT NULL,
 p_cursor_sort_at timestamptz DEFAULT NULL,p_cursor_id uuid DEFAULT NULL,p_limit int DEFAULT 20)
RETURNS TABLE(id uuid,feed_id uuid,feed_title text,title text,url text,summary text,snippet text,
 author text,published_at timestamptz,fetched_at timestamptz,is_read boolean,is_bookmarked boolean,rank real)
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT id,feed_id,feed_title,title,url,summary,snippet,author,published_at,fetched_at,is_read,is_bookmarked,rank
 FROM driftread.search_publications(p_query,p_user_id,p_language,p_cursor_rank,p_cursor_sort_at,p_cursor_id,p_limit)
$$;
REVOKE ALL ON FUNCTION driftread.list_feed_articles(uuid,uuid,timestamptz,uuid,int),
 driftread.list_reading_stream(uuid,uuid,boolean,timestamptz,uuid,int),
 driftread.search_articles(text,uuid,text,real,timestamptz,uuid,int) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_feed_articles(uuid,uuid,timestamptz,uuid,int),
 driftread.list_reading_stream(uuid,uuid,boolean,timestamptz,uuid,int),
 driftread.search_articles(text,uuid,text,real,timestamptz,uuid,int) TO service_role;
