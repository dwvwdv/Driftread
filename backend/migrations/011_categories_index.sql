-- GET /feeds/categories used to `select("category")` for every non-archived
-- feed row and dedupe with a Python set() — the payload and dedup work both
-- grow linearly with the feed catalog even though the caller only ever wants
-- the distinct list. Push the dedup into SQL, where `feeds_category_idx`
-- already supports it. Same lockdown pattern as `sample_feed_candidates`
-- (migration 007/010): the backend only ever calls this via the service_role
-- client (database.py), so PostgREST's anon/authenticated roles get no
-- EXECUTE grant.
CREATE OR REPLACE FUNCTION driftread.list_feed_categories()
RETURNS TABLE(category text)
LANGUAGE sql
SET search_path = pg_catalog
AS $$
  SELECT DISTINCT category
  FROM driftread.feeds
  WHERE archived_at IS NULL
    AND category IS NOT NULL
    AND category != ''
  ORDER BY category
$$;

REVOKE ALL ON FUNCTION driftread.list_feed_categories() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_feed_categories() TO service_role;

-- GET /feeds/{feed_id} filters articles by feed_id and orders by
-- published_at DESC (routers/feeds.py get_feed). The existing single-column
-- articles_feed_id_idx and articles_published_at_idx each cover half of that
-- query; a composite index lets it be satisfied with one index scan instead
-- of an index scan plus a separate sort as each feed's article count grows.
CREATE INDEX IF NOT EXISTS articles_feed_id_published_at_idx
  ON driftread.articles (feed_id, published_at DESC);
