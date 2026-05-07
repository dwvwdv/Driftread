-- Driftread initial schema

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- RSS sources
CREATE TABLE IF NOT EXISTS feeds (
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

CREATE INDEX IF NOT EXISTS feeds_category_idx ON feeds (category);
CREATE INDEX IF NOT EXISTS feeds_archived_at_idx ON feeds (archived_at);

-- Cached articles
CREATE TABLE IF NOT EXISTS articles (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  feed_id     UUID NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  url         TEXT UNIQUE NOT NULL,
  summary     TEXT,
  content     TEXT,
  author      TEXT,
  published_at TIMESTAMPTZ,
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS articles_feed_id_idx ON articles (feed_id);
CREATE INDEX IF NOT EXISTS articles_published_at_idx ON articles (published_at DESC);

-- Auto-update updated_at on feeds
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

CREATE TRIGGER feeds_updated_at
  BEFORE UPDATE ON feeds
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
