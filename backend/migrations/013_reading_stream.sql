-- 我的閱讀流：跨所有已訂閱來源聚合的文章時間流，帶未讀狀態、未讀計數與批次已讀。
--
-- No new read-state table: `user_article_reads` (migration 002) already is one —
-- a row's presence *is* "read", same as GET /me/reads already relies on. This
-- migration only adds the query-side plumbing the aggregated stream needs:
-- two DB functions (a keyset-paginated join across every subscribed feed's
-- articles, and a per-feed unread rollup) plus one that performs a scoped
-- "mark all as read" as a single INSERT ... SELECT rather than round-tripping
-- article ids through Python first.
--
-- Same lockdown pattern as `sample_feed_candidates` / `list_feed_categories`:
-- SECURITY INVOKER (default, no SECURITY DEFINER anywhere in this schema —
-- see Phase 0's RLS audit in TODO.md), EXECUTE revoked from PUBLIC/anon/
-- authenticated and granted only to service_role, because the backend only
-- ever calls these through the service_role client (database.py) and passes
-- the caller's user id explicitly — this project's user isolation is
-- enforced in the application layer, not via RLS + a user-scoped JWT client
-- (also tracked as not-yet-done in TODO.md's Phase 0).

-- Articles without a parsed `published_at` (feeds that omit a date, or rows
-- from before some upstream fix) would otherwise sort inconsistently under
-- Postgres's default `DESC` = `NULLS FIRST`, and a keyset cursor can't skip
-- past a NULL cleanly either. Both functions below order by
-- COALESCE(published_at, fetched_at) instead: every article sorts by a
-- real timestamp, undated ones fall back to when we fetched them, and the
-- keyset comparison never has to special-case NULL.

CREATE OR REPLACE FUNCTION driftread.list_reading_stream(
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
  is_read      boolean,
  read_at      timestamptz
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
  SELECT
    a.id, a.feed_id, f.title, a.title, a.url, a.summary, a.author,
    a.published_at, a.fetched_at,
    (r.article_id IS NOT NULL) AS is_read,
    r.read_at
  FROM driftread.user_feeds uf
  JOIN driftread.articles a ON a.feed_id = uf.feed_id
  JOIN driftread.feeds f ON f.id = a.feed_id
  LEFT JOIN driftread.user_article_reads r
    ON r.article_id = a.id AND r.user_id = p_user_id
  WHERE uf.user_id = p_user_id
    AND (p_feed_id IS NULL OR a.feed_id = p_feed_id)
    AND (NOT p_unread_only OR r.article_id IS NULL)
    AND (
      p_cursor_sort_at IS NULL
      OR COALESCE(a.published_at, a.fetched_at) < p_cursor_sort_at
      OR (COALESCE(a.published_at, a.fetched_at) = p_cursor_sort_at AND a.id < p_cursor_id)
    )
  ORDER BY COALESCE(a.published_at, a.fetched_at) DESC, a.id DESC
  -- Same defense-in-depth clamp as sample_feed_candidates: the only caller
  -- (routers/me.py) already validates `limit` with `Query(..., le=100)`, but
  -- a direct RPC call (blocked by the grants below, belt-and-braces anyway)
  -- shouldn't be able to ask for an unbounded result set.
  LIMIT LEAST(GREATEST(p_limit, 1), 100)
$$;

-- Per-feed unread rollup for every feed the user is subscribed to, including
-- feeds with zero unread (a LEFT JOIN, not an anti-join over articles alone)
-- so "已讀完" sources still show a 0 rather than disappearing from the list.
-- `routers/me.py` sums these for the total unread count rather than issuing a
-- second query — every subscribed feed appears exactly once here.
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
  LEFT JOIN driftread.articles a ON a.feed_id = f.id
  LEFT JOIN driftread.user_article_reads r
    ON r.article_id = a.id AND r.user_id = p_user_id
  WHERE uf.user_id = p_user_id
  GROUP BY f.id, f.title
  ORDER BY f.title
$$;

-- Scoped "mark all as read": every subscribed article, optionally narrowed to
-- one feed and/or to articles at-or-before a given timestamp. A single
-- INSERT ... SELECT ... ON CONFLICT DO NOTHING rather than the API layer
-- selecting matching ids and re-upserting them one request at a time — the
-- scope can span an entire feed's history, and this keeps that a single
-- statement instead of an unbounded round trip.
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
  JOIN driftread.articles a ON a.feed_id = uf.feed_id
  WHERE uf.user_id = p_user_id
    AND (p_feed_id IS NULL OR a.feed_id = p_feed_id)
    AND (p_before IS NULL OR COALESCE(a.published_at, a.fetched_at) <= p_before)
  ON CONFLICT (user_id, article_id) DO NOTHING;
  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN QUERY SELECT affected;
END;
$$;

REVOKE ALL ON FUNCTION driftread.list_reading_stream(uuid, uuid, boolean, timestamptz, uuid, int)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_reading_stream(uuid, uuid, boolean, timestamptz, uuid, int)
  TO service_role;

REVOKE ALL ON FUNCTION driftread.reading_stream_unread_counts(uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.reading_stream_unread_counts(uuid)
  TO service_role;

REVOKE ALL ON FUNCTION driftread.mark_reading_stream_read(uuid, uuid, timestamptz)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.mark_reading_stream_read(uuid, uuid, timestamptz)
  TO service_role;

-- Query-pattern indexes ------------------------------------------------------
--
-- `articles(feed_id, published_at DESC)` already exists (migration 012) and
-- covers the per-feed-scoped half of `list_reading_stream`'s join+sort; the
-- unscoped (all-subscriptions) stream and the unread rollup instead drive off
-- `user_feeds` to enumerate the subscribed feed set first. The existing
-- `user_feeds` primary key `(user_id, feed_id)` already makes that an index-only
-- lookup, and `user_article_reads`'s primary key `(user_id, article_id)` already
-- makes the LEFT JOIN to read-state an index lookup too — both composite and
-- already covering these new queries, so nothing new is needed for either
-- side of that join.
--
-- What's missing is an index usable when `p_before` narrows a scoped mark-all
-- or a future date-scoped read of the stream: neither existing articles index
-- is keyed on `fetched_at`, so a request that lands on the COALESCE(published_at,
-- fetched_at) IS NULL fallback for many undated rows would fall back to a scan.
CREATE INDEX IF NOT EXISTS articles_feed_id_fetched_at_idx
  ON driftread.articles (feed_id, fetched_at DESC);
