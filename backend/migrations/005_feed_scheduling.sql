-- Feed scheduling: a next_fetch_at-driven due queue with adaptive backoff,
-- plus the conditional-GET validators needed to avoid re-downloading unchanged
-- feed bodies on every poll.

ALTER TABLE driftread.feeds
  ADD COLUMN IF NOT EXISTS etag                   TEXT,
  ADD COLUMN IF NOT EXISTS last_modified          TEXT,
  ADD COLUMN IF NOT EXISTS fetch_interval_minutes INT NOT NULL DEFAULT 60,
  ADD COLUMN IF NOT EXISTS next_fetch_at          TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Drives the due query in services/feed_refresh.py::select_due_feeds, which
-- always filters archived_at IS NULL — so a partial index keeps archived feeds
-- (the auto-archive threshold sends dead sources here permanently) out of the
-- index entirely rather than just out of the result set.
CREATE INDEX IF NOT EXISTS feeds_next_fetch_at_idx
  ON driftread.feeds (next_fetch_at)
  WHERE archived_at IS NULL;

-- articles.url was globally UNIQUE, which is wrong once two feeds syndicate the
-- same article URL: they fight over a single row, and upsert's on_conflict="url"
-- reassigns that row's feed_id to whichever feed refreshed last. Scope it per
-- feed instead. No dedupe pass is needed first — a globally unique url means
-- (feed_id, url) is already unique, so the wider constraint cannot fail.
ALTER TABLE driftread.articles DROP CONSTRAINT IF EXISTS articles_url_key;

-- ADD CONSTRAINT has no IF NOT EXISTS, and a raised migration aborts backend
-- startup (main.py's lifespan calls run_migrations before serving), so guard it
-- rather than let a hand-patched database wedge the container on boot.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'articles_feed_id_url_key'
       AND conrelid = 'driftread.articles'::regclass
  ) THEN
    ALTER TABLE driftread.articles
      ADD CONSTRAINT articles_feed_id_url_key UNIQUE (feed_id, url);
  END IF;
END $$;
