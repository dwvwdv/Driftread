-- Preferences UI (TODO.md P1 "建立偏好設定 UI") needs a language vocabulary to
-- offer alongside GET /feeds/categories. Same db-side dedup pattern as
-- list_feed_categories (migration 011): PostgREST's anon/authenticated roles
-- get no EXECUTE grant, the backend only ever calls this via the service_role
-- client (database.py).
CREATE OR REPLACE FUNCTION driftread.list_feed_languages()
RETURNS TABLE(language text)
LANGUAGE sql
SET search_path = pg_catalog
AS $$
  SELECT DISTINCT language
  FROM driftread.feeds
  WHERE archived_at IS NULL
    AND language IS NOT NULL
    AND language != ''
  ORDER BY language
$$;

REVOKE ALL ON FUNCTION driftread.list_feed_languages() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_feed_languages() TO service_role;
