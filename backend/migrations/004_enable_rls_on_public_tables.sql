-- Enable Row Level Security on public-facing tables.
-- feeds and articles are world-readable (anyone can browse the RSS catalog),
-- but writes are gated to service_role (the backend connects with service_role
-- key and performs its own admin authorization via X-API-Key).

ALTER TABLE feeds    ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;

-- Public read access (anon + authenticated). Archived feeds are still readable;
-- application-level filters in the API exclude them where appropriate.
--
-- CREATE POLICY has no IF NOT EXISTS, and a raised migration aborts backend
-- startup (main.py's lifespan calls run_migrations before serving). Guard it
-- so re-running this file against a database that already has these policies
-- — e.g. after a hand-cleared _migrations table — doesn't wedge the container
-- on boot (same pattern as 001/002/006).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'feeds' AND policyname = 'feeds_public_read'
  ) THEN
    CREATE POLICY feeds_public_read ON feeds
      FOR SELECT TO anon, authenticated USING (TRUE);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'articles' AND policyname = 'articles_public_read'
  ) THEN
    CREATE POLICY articles_public_read ON articles
      FOR SELECT TO anon, authenticated USING (TRUE);
  END IF;
END $$;

-- No INSERT/UPDATE/DELETE policies for anon/authenticated:
-- service_role bypasses RLS, so backend admin endpoints continue to work;
-- direct anon/authenticated writes are rejected by default.
