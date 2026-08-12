-- Driftread owns this schema inside the shared Lazyrhythm Supabase project.
CREATE SCHEMA IF NOT EXISTS driftread;

REVOKE ALL ON SCHEMA driftread FROM PUBLIC;
GRANT USAGE ON SCHEMA driftread TO anon, authenticated, service_role;

-- Append Driftread to PostgREST's existing schema list. This project is shared
-- with other applications, so replacing the setting would silently remove
-- their schemas from the Data API.
DO $$
DECLARE
  exposed_schemas text;
BEGIN
  SELECT split_part(setting, '=', 2)
  INTO exposed_schemas
  FROM pg_roles
  CROSS JOIN LATERAL unnest(COALESCE(rolconfig, ARRAY[]::text[])) AS setting
  WHERE rolname = 'authenticator'
    AND setting LIKE 'pgrst.db_schemas=%';

  exposed_schemas := COALESCE(exposed_schemas, 'public, graphql_public');
  IF NOT ('driftread' = ANY(regexp_split_to_array(exposed_schemas, '\s*,\s*'))) THEN
    exposed_schemas := exposed_schemas || ', driftread';
  END IF;

  EXECUTE format(
    'ALTER ROLE authenticator SET pgrst.db_schemas = %L',
    exposed_schemas
  );
END
$$;

-- Move the existing Driftread objects without copying rows or changing OIDs.
-- ALTER ... SET SCHEMA keeps foreign keys, indexes, triggers and policies intact.
DO $$
DECLARE
  object_name text;
BEGIN
  FOREACH object_name IN ARRAY ARRAY[
    '_migrations',
    'feeds',
    'articles',
    'user_feeds',
    'user_article_reads',
    'user_bookmarks',
    'user_preferences',
    'discovery_targets',
    'discovery_target_referrers',
    'discovery_candidates',
    'discovery_sources'
  ]
  LOOP
    IF to_regclass(format('public.%I', object_name)) IS NOT NULL
       AND to_regclass(format('driftread.%I', object_name)) IS NULL THEN
      EXECUTE format('ALTER TABLE public.%I SET SCHEMA driftread', object_name);
    END IF;
  END LOOP;
END
$$;

-- Keep old backend images safe during a rolling deployment. They still look
-- up public._migrations; this auto-updatable view points them at the real,
-- private ledger so they never replay migrations against the emptied public
-- schema. Remove the view after all deployments use driftread._migrations.
DO $$
BEGIN
  IF to_regclass('public._migrations') IS NULL THEN
    CREATE VIEW public._migrations
      WITH (security_invoker = true)
      AS SELECT filename, applied_at FROM driftread._migrations;
  END IF;
END
$$;

REVOKE ALL ON TABLE public._migrations FROM PUBLIC, anon, authenticated;

-- Recreate functions with fully-qualified references and a fixed search_path.
CREATE OR REPLACE FUNCTION driftread.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION driftread.discovery_sync_referrer_count()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
  affected uuid := COALESCE(NEW.target_id, OLD.target_id);
  total integer;
BEGIN
  SELECT COUNT(*) INTO total
  FROM driftread.discovery_target_referrers
  WHERE target_id = affected;

  UPDATE driftread.discovery_targets
  SET referring_feed_count = total
  WHERE id = affected AND referring_feed_count <> total;

  UPDATE driftread.discovery_candidates
  SET referring_feed_count = total
  WHERE target_id = affected
    AND status = 'pending'
    AND referring_feed_count <> total;

  RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION driftread.sample_feed_candidates(
  p_excluded_ids uuid[],
  p_categories text[],
  p_mode text,
  p_limit integer
)
RETURNS SETOF driftread.feeds
LANGUAGE sql
SET search_path = pg_catalog
AS $$
  SELECT *
  FROM driftread.feeds
  WHERE archived_at IS NULL
    AND (p_excluded_ids IS NULL OR NOT (id = ANY(p_excluded_ids)))
    AND (
      p_mode = 'unfiltered'
      OR (p_mode = 'in_categories' AND category = ANY(p_categories))
      OR (p_mode = 'not_in_categories' AND NOT (category = ANY(p_categories)))
      OR (p_mode = 'uncategorized' AND category IS NULL)
    )
  ORDER BY random()
  LIMIT LEAST(p_limit, 250)
$$;

-- Trigger dependencies prevent ALTER FUNCTION ... SET SCHEMA. Once the new
-- schema functions exist, retarget every trigger, then remove the old copies.
DROP TRIGGER IF EXISTS feeds_updated_at ON driftread.feeds;
CREATE TRIGGER feeds_updated_at
  BEFORE UPDATE ON driftread.feeds
  FOR EACH ROW EXECUTE FUNCTION driftread.set_updated_at();

DROP TRIGGER IF EXISTS user_preferences_updated_at ON driftread.user_preferences;
CREATE TRIGGER user_preferences_updated_at
  BEFORE UPDATE ON driftread.user_preferences
  FOR EACH ROW EXECUTE FUNCTION driftread.set_updated_at();

DROP TRIGGER IF EXISTS discovery_targets_updated_at ON driftread.discovery_targets;
CREATE TRIGGER discovery_targets_updated_at
  BEFORE UPDATE ON driftread.discovery_targets
  FOR EACH ROW EXECUTE FUNCTION driftread.set_updated_at();

DROP TRIGGER IF EXISTS discovery_candidates_updated_at ON driftread.discovery_candidates;
CREATE TRIGGER discovery_candidates_updated_at
  BEFORE UPDATE ON driftread.discovery_candidates
  FOR EACH ROW EXECUTE FUNCTION driftread.set_updated_at();

DROP TRIGGER IF EXISTS discovery_sources_updated_at ON driftread.discovery_sources;
CREATE TRIGGER discovery_sources_updated_at
  BEFORE UPDATE ON driftread.discovery_sources
  FOR EACH ROW EXECUTE FUNCTION driftread.set_updated_at();

DROP TRIGGER IF EXISTS discovery_target_referrers_sync_count
  ON driftread.discovery_target_referrers;
CREATE TRIGGER discovery_target_referrers_sync_count
  AFTER INSERT OR DELETE ON driftread.discovery_target_referrers
  FOR EACH ROW EXECUTE FUNCTION driftread.discovery_sync_referrer_count();

DROP FUNCTION IF EXISTS public.sample_feed_candidates(uuid[], text[], text, integer);
DROP FUNCTION IF EXISTS public.discovery_sync_referrer_count();
DROP FUNCTION IF EXISTS public.set_updated_at();

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';

-- Every table in the exposed driftread schema has RLS enabled, including the
-- private migration ledger as defense in depth.
ALTER TABLE driftread._migrations                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.feeds                       ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.articles                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.user_feeds                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.user_article_reads          ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.user_bookmarks              ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.user_preferences            ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.discovery_targets           ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.discovery_target_referrers  ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.discovery_candidates        ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.discovery_sources           ENABLE ROW LEVEL SECURITY;

-- Rebuild owner policies with init-plan auth.uid() calls. This preserves the
-- access model while avoiding one function call per scanned row.
ALTER POLICY user_feeds_owner ON driftread.user_feeds
  USING (
    user_id = (SELECT auth.uid())
    AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
  )
  WITH CHECK (
    user_id = (SELECT auth.uid())
    AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
  );

ALTER POLICY user_article_reads_owner ON driftread.user_article_reads
  USING (
    user_id = (SELECT auth.uid())
    AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
  )
  WITH CHECK (
    user_id = (SELECT auth.uid())
    AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
  );

ALTER POLICY user_bookmarks_owner ON driftread.user_bookmarks
  USING (
    user_id = (SELECT auth.uid())
    AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
  )
  WITH CHECK (
    user_id = (SELECT auth.uid())
    AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
  );

ALTER POLICY user_preferences_owner ON driftread.user_preferences
  USING (
    user_id = (SELECT auth.uid())
    AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
  )
  WITH CHECK (
    user_id = (SELECT auth.uid())
    AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
  );

-- Object privileges are explicit and minimal. RLS controls rows only after
-- these table-level gates have allowed the operation.
REVOKE ALL ON ALL TABLES IN SCHEMA driftread FROM PUBLIC, anon, authenticated;
GRANT SELECT ON driftread.feeds, driftread.articles TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON
  driftread.user_feeds,
  driftread.user_article_reads,
  driftread.user_bookmarks,
  driftread.user_preferences
TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA driftread TO service_role;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA driftread FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION
  driftread.sample_feed_candidates(uuid[], text[], text, integer)
TO service_role;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA driftread TO service_role;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA driftread FROM PUBLIC, anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA driftread TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA driftread
  REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA driftread
  REVOKE ALL ON FUNCTIONS FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA driftread
  REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA driftread
  GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA driftread
  GRANT ALL ON FUNCTIONS TO service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA driftread
  GRANT ALL ON SEQUENCES TO service_role;
