-- Canonical feed-language buckets + one-time backfill scheduling.
--
-- Existing data predates automatic detection and may contain locale-shaped
-- values (en-US, zh_TW, ...).  Driftread filters/recommends at language rather
-- than locale granularity, so collapse those to the ISO-639 primary language.
-- The worker will fill rows that still have no language from feed metadata or
-- cached feed text on their next fetch.

UPDATE driftread.feeds
SET language = lower(split_part(replace(trim(language), '_', '-'), '-', 1))
WHERE language IS NOT NULL
  AND trim(language) <> '';

UPDATE driftread.feeds
SET language = NULL
WHERE language IS NOT NULL
  AND language !~ '^[a-z]{2,3}$';

-- A conditional 304 has no body to classify. Force exactly the currently
-- unclassified feeds through one unconditional fetch so the new detector gets
-- a payload immediately instead of waiting for a publisher-side change.
UPDATE driftread.feeds
SET next_fetch_at = now(),
    etag = NULL,
    last_modified = NULL
WHERE archived_at IS NULL
  AND language IS NULL;

-- Language equality is used by the public catalogue and reading/recommendation
-- paths.  Keep the index partial because NULL means "not confidently known" and
-- is never selected by a language filter.
CREATE INDEX IF NOT EXISTS feeds_language_idx
  ON driftread.feeds (language)
  WHERE language IS NOT NULL AND archived_at IS NULL;
