-- Canonical feed-language buckets + one-time backfill scheduling.
--
-- Existing data predates automatic detection and may contain locale-shaped
-- values (en-US, zh_TW, ...). Driftread filters/recommends at language rather
-- than locale granularity, so collapse those to the primary language.

UPDATE driftread.feeds
SET language = lower(split_part(replace(trim(language), '_', '-'), '-', 1))
WHERE language IS NOT NULL
  AND trim(language) <> '';

UPDATE driftread.feeds
SET language = CASE language
  WHEN 'chi' THEN 'zh' WHEN 'zho' THEN 'zh'
  WHEN 'eng' THEN 'en'
  WHEN 'jpn' THEN 'ja'
  WHEN 'kor' THEN 'ko'
  WHEN 'deu' THEN 'de' WHEN 'ger' THEN 'de'
  WHEN 'fra' THEN 'fr' WHEN 'fre' THEN 'fr'
  WHEN 'spa' THEN 'es'
  WHEN 'ita' THEN 'it'
  WHEN 'por' THEN 'pt'
  WHEN 'rus' THEN 'ru'
  WHEN 'ukr' THEN 'uk'
  WHEN 'vie' THEN 'vi'
  WHEN 'tha' THEN 'th'
  WHEN 'ind' THEN 'id'
  ELSE language
END
WHERE language IS NOT NULL;

UPDATE driftread.feeds
SET language = NULL
WHERE language IS NOT NULL
  AND language !~ '^[a-z]{2}$';

-- A conditional 304 has no body to classify. Force exactly the currently
-- unclassified feeds through one unconditional fetch so the new detector gets
-- a payload immediately instead of waiting for a publisher-side change.
UPDATE driftread.feeds
SET next_fetch_at = now(),
    etag = NULL,
    last_modified = NULL
WHERE archived_at IS NULL
  AND language IS NULL;

-- Language equality is used by the public catalogue and recommendation paths.
CREATE INDEX IF NOT EXISTS feeds_language_idx
  ON driftread.feeds (language)
  WHERE language IS NOT NULL AND archived_at IS NULL;

-- The recommendation scorer compares feed languages to
-- user_preferences.preferred_languages by exact string equality, so a saved
-- preference in the old locale/legacy shape (en-US, eng, ...) would silently
-- stop matching feeds that this migration just canonicalized to en. Apply the
-- same canonicalization to preferences, deduplicating per user.
WITH normalized AS (
  SELECT
    up.user_id,
    COALESCE(
      array_agg(DISTINCT canonical.lang) FILTER (WHERE canonical.lang ~ '^[a-z]{2}$'),
      '{}'
    ) AS langs
  FROM driftread.user_preferences up
  LEFT JOIN LATERAL unnest(up.preferred_languages) AS raw_lang ON TRUE
  LEFT JOIN LATERAL (
    SELECT CASE lower(split_part(replace(trim(raw_lang), '_', '-'), '-', 1))
      WHEN 'chi' THEN 'zh' WHEN 'zho' THEN 'zh'
      WHEN 'eng' THEN 'en'
      WHEN 'jpn' THEN 'ja'
      WHEN 'kor' THEN 'ko'
      WHEN 'deu' THEN 'de' WHEN 'ger' THEN 'de'
      WHEN 'fra' THEN 'fr' WHEN 'fre' THEN 'fr'
      WHEN 'spa' THEN 'es'
      WHEN 'ita' THEN 'it'
      WHEN 'por' THEN 'pt'
      WHEN 'rus' THEN 'ru'
      WHEN 'ukr' THEN 'uk'
      WHEN 'vie' THEN 'vi'
      WHEN 'tha' THEN 'th'
      WHEN 'ind' THEN 'id'
      ELSE lower(split_part(replace(trim(raw_lang), '_', '-'), '-', 1))
    END AS lang
  ) canonical ON TRUE
  GROUP BY up.user_id
)
UPDATE driftread.user_preferences up
SET preferred_languages = normalized.langs,
    updated_at = now()
FROM normalized
WHERE up.user_id = normalized.user_id
  AND up.preferred_languages IS DISTINCT FROM normalized.langs;
