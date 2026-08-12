-- Driftread initial schema

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- RSS sources
CREATE TABLE IF NOT EXISTS driftread.feeds (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title       TEXT NOT NULL,
  url         TEXT UNIQUE NOT NULL,
  description TEXT,
  website_url TEXT,
  language    TEXT,
  category    TEXT,
  tags        TEXT[] NOT NULL DEFAULT '{}',
  article_count  INT NOT NULL DEFAULT 0,
  last_fetched_at TIMESTAMPTZ,
  archived_at    TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS feeds_category_idx ON driftread.feeds (category);
CREATE INDEX IF NOT EXISTS feeds_archived_at_idx ON driftread.feeds (archived_at);

-- Cached articles
CREATE TABLE IF NOT EXISTS driftread.articles (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  feed_id     UUID NOT NULL REFERENCES driftread.feeds(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  url         TEXT UNIQUE NOT NULL,
  summary     TEXT,
  content     TEXT,
  author      TEXT,
  published_at TIMESTAMPTZ,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS articles_feed_id_idx ON driftread.articles (feed_id);
CREATE INDEX IF NOT EXISTS articles_published_at_idx ON driftread.articles (published_at DESC);

-- Auto-update updated_at on feeds
CREATE OR REPLACE FUNCTION driftread.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

-- CREATE TRIGGER has no IF NOT EXISTS, and a raised migration aborts backend
-- startup (main.py's lifespan calls run_migrations before serving). Guard it
-- so re-running this file against a database that already has the trigger —
-- e.g. after a hand-cleared _migrations table — doesn't wedge the container
-- on boot (see migration 006 for the same pattern).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgname = 'feeds_updated_at'
       AND tgrelid = 'driftread.feeds'::regclass
  ) THEN
    CREATE TRIGGER feeds_updated_at
      BEFORE UPDATE ON driftread.feeds
      FOR EACH ROW EXECUTE FUNCTION driftread.set_updated_at();
  END IF;
END $$;
