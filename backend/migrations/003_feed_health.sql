-- Driftread feed health monitoring.
-- Tracks fetch failures so dead feeds can be surfaced and auto-archived.

ALTER TABLE driftread.feeds
  ADD COLUMN IF NOT EXISTS consecutive_failures INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_failure_at      TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_failure_reason  TEXT,
  ADD COLUMN IF NOT EXISTS health_score         INT NOT NULL DEFAULT 100;

CREATE INDEX IF NOT EXISTS feeds_health_score_idx ON driftread.feeds (health_score);
