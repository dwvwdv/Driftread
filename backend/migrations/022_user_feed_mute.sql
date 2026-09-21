-- TODO.md P2「資料夾與來源控制」：支援來源靜音／暫停，不必取消訂閱。
--
-- Same shape as migration 021's custom_title: a property of the subscription
-- (driftread.user_feeds), not of the feed itself — muting is one reader's
-- decision to stop seeing a source in their own reading stream, it must not
-- affect other subscribers or the public catalog.
--
-- A nullable timestamp rather than a bare boolean, matching feeds.archived_at
-- (migration 002) already used for the same "off without deleting" shape
-- elsewhere in this schema: NULL means active, non-NULL records *when* the
-- reader muted it, which is free provenance a boolean would have discarded
-- and mirrors the existing archived_at convention instead of introducing a
-- second one.
ALTER TABLE driftread.user_feeds
  ADD COLUMN IF NOT EXISTS muted_at TIMESTAMPTZ;

-- Muted subscriptions must disappear from the reading stream and its unread
-- counts (the entire point of muting is fewer things to read without
-- unsubscribing), but must still be returned by GET /me/feeds — "我的訂閱"
-- is a source-management view, not a reading view, so a muted feed staying
-- listed there (with a way to unmute) is intentional, not an oversight.
-- Only these two functions read user_feeds for reading-scoped queries;
-- list_subscriptions (routers/me.py) queries the table directly and is
-- unaffected by this migration.
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
    AND uf.muted_at IS NULL
    AND (p_feed_id IS NULL OR a.feed_id = p_feed_id)
    AND (NOT p_unread_only OR r.article_id IS NULL)
    AND (
      p_cursor_sort_at IS NULL
      OR COALESCE(a.published_at, a.fetched_at) < p_cursor_sort_at
      OR (COALESCE(a.published_at, a.fetched_at) = p_cursor_sort_at AND a.id < p_cursor_id)
    )
  ORDER BY COALESCE(a.published_at, a.fetched_at) DESC, a.id DESC
  LIMIT LEAST(GREATEST(p_limit, 1), 100)
$$;

-- Muted feeds are dropped entirely, including from the per-feed rollup, so
-- they don't show up as a filter chip with a 0 badge that then reveals no
-- articles at all when picked (list_reading_stream would return nothing for
-- a muted feed_id) — same "gone from the reading view" contract as the
-- unified stream above, applied consistently to its filter list.
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
    AND uf.muted_at IS NULL
  GROUP BY f.id, f.title
  ORDER BY f.title
$$;

-- mark_reading_stream_read is deliberately left untouched: "mark everything
-- read" acting on a muted feed's backlog too (if the caller explicitly
-- scopes to it via p_feed_id, or it's swept in by an unscoped mark-all) is
-- not a reading-stream *view* concern, and muting is reversible — a reader
-- who unmutes a feed later shouldn't find its entire backlog was silently
-- exempted from every mark-all-read they ran while it was muted.

-- CREATE OR REPLACE preserves each function's existing grants (signature is
-- unchanged), so the REVOKE/GRANT pair from migration 015 doesn't need to be
-- repeated here.
