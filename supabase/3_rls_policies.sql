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
