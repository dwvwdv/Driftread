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

