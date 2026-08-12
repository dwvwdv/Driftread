-- Driftread user-facing features: subscriptions, reads, bookmarks, preferences.
-- Uses Supabase Auth's auth.users table for user identity.

-- User subscriptions to feeds
CREATE TABLE IF NOT EXISTS driftread.user_feeds (
  user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  feed_id    UUID NOT NULL REFERENCES driftread.feeds(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, feed_id)
);

CREATE INDEX IF NOT EXISTS user_feeds_user_idx ON driftread.user_feeds (user_id);
CREATE INDEX IF NOT EXISTS user_feeds_feed_idx ON driftread.user_feeds (feed_id);

-- Read receipts
CREATE TABLE IF NOT EXISTS driftread.user_article_reads (
  user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  article_id UUID NOT NULL REFERENCES driftread.articles(id) ON DELETE CASCADE,
  read_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, article_id)
);

CREATE INDEX IF NOT EXISTS user_article_reads_user_idx ON driftread.user_article_reads (user_id);

-- Bookmarks (favorite / read_later)
CREATE TABLE IF NOT EXISTS driftread.user_bookmarks (
  user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  article_id    UUID NOT NULL REFERENCES driftread.articles(id) ON DELETE CASCADE,
  bookmark_type TEXT NOT NULL CHECK (bookmark_type IN ('favorite', 'read_later')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, article_id, bookmark_type)
);

CREATE INDEX IF NOT EXISTS user_bookmarks_user_type_idx ON driftread.user_bookmarks (user_id, bookmark_type);

-- Per-user preferences for the recommendation engine
CREATE TABLE IF NOT EXISTS driftread.user_preferences (
  user_id              UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  preferred_categories TEXT[] NOT NULL DEFAULT '{}',
  preferred_languages  TEXT[] NOT NULL DEFAULT '{}',
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- CREATE TRIGGER / CREATE POLICY have no IF NOT EXISTS, and a raised
-- migration aborts backend startup (main.py's lifespan calls run_migrations
-- before serving). Guard both so re-running this file against a database
-- that already has them — e.g. after a hand-cleared _migrations table —
-- doesn't wedge the container on boot (see migration 006 for the same
-- trigger-guard pattern).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgname = 'user_preferences_updated_at'
       AND tgrelid = 'driftread.user_preferences'::regclass
  ) THEN
    CREATE TRIGGER user_preferences_updated_at
      BEFORE UPDATE ON driftread.user_preferences
      FOR EACH ROW EXECUTE FUNCTION driftread.set_updated_at();
  END IF;
END $$;

-- Row Level Security: each user only sees their own rows.
ALTER TABLE driftread.user_feeds          ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.user_article_reads  ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.user_bookmarks      ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.user_preferences    ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'driftread' AND tablename = 'user_feeds' AND policyname = 'user_feeds_owner'
  ) THEN
    CREATE POLICY user_feeds_owner ON driftread.user_feeds
      FOR ALL TO authenticated
      USING (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      )
      WITH CHECK (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'driftread' AND tablename = 'user_article_reads' AND policyname = 'user_article_reads_owner'
  ) THEN
    CREATE POLICY user_article_reads_owner ON driftread.user_article_reads
      FOR ALL TO authenticated
      USING (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      )
      WITH CHECK (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'driftread' AND tablename = 'user_bookmarks' AND policyname = 'user_bookmarks_owner'
  ) THEN
    CREATE POLICY user_bookmarks_owner ON driftread.user_bookmarks
      FOR ALL TO authenticated
      USING (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      )
      WITH CHECK (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'driftread' AND tablename = 'user_preferences' AND policyname = 'user_preferences_owner'
  ) THEN
    CREATE POLICY user_preferences_owner ON driftread.user_preferences
      FOR ALL TO authenticated
      USING (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      )
      WITH CHECK (
        user_id = (SELECT auth.uid())
        AND (SELECT (auth.jwt()->>'is_anonymous')::boolean) IS FALSE
      );
  END IF;
END $$;
