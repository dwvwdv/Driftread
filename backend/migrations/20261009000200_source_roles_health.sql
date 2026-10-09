-- Source roles are objective metadata, not a global editorial tier.
ALTER TABLE driftread.feeds
  ADD COLUMN IF NOT EXISTS first_party boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS participation_mode text NOT NULL DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS signal_group text,
  ADD COLUMN IF NOT EXISTS fulltext_policy text NOT NULL DEFAULT 'rss',
  ADD COLUMN IF NOT EXISTS last_fetch_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_ok_at timestamptz;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='feeds_participation_mode_check' AND conrelid='driftread.feeds'::regclass) THEN
    ALTER TABLE driftread.feeds ADD CONSTRAINT feeds_participation_mode_check CHECK (participation_mode IN ('normal','signal_only','private'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='feeds_fulltext_policy_check' AND conrelid='driftread.feeds'::regclass) THEN
    ALTER TABLE driftread.feeds ADD CONSTRAINT feeds_fulltext_policy_check CHECK (fulltext_policy IN ('rss','summary_only'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='feeds_signal_group_check' AND conrelid='driftread.feeds'::regclass) THEN
    ALTER TABLE driftread.feeds ADD CONSTRAINT feeds_signal_group_check CHECK (signal_group IS NULL OR (length(signal_group) BETWEEN 1 AND 100 AND signal_group=btrim(signal_group)));
  END IF;
END $$;
-- The legacy field records success/304, not failures: retain known evidence.
UPDATE driftread.feeds SET last_ok_at=last_fetched_at WHERE last_ok_at IS NULL AND last_fetched_at IS NOT NULL;
UPDATE driftread.feeds SET last_fetch_at=GREATEST(last_fetched_at,last_failure_at) WHERE last_fetch_at IS NULL;
ALTER POLICY feeds_public_read ON driftread.feeds USING (participation_mode = 'normal');
CREATE INDEX IF NOT EXISTS feeds_normal_sample_key_idx ON driftread.feeds(sample_key) WHERE archived_at IS NULL AND participation_mode='normal';
CREATE OR REPLACE FUNCTION driftread.record_source_fetch(p_feed_id uuid,p_at timestamptz,p_ok boolean)
RETURNS void LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
  UPDATE driftread.feeds SET
    last_fetch_at=GREATEST(last_fetch_at,p_at),
    last_ok_at=CASE WHEN p_ok THEN GREATEST(last_ok_at,p_at) ELSE last_ok_at END
  WHERE id=p_feed_id
$$;
REVOKE ALL ON FUNCTION driftread.record_source_fetch(uuid,timestamptz,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.record_source_fetch(uuid,timestamptz,boolean) TO service_role;
-- Public import cannot overwrite an existing source or expose a private URL.
CREATE OR REPLACE FUNCTION driftread.import_readable_source(p_metadata jsonb)
RETURNS SETOF driftread.feeds LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  INSERT INTO driftread.feeds(title,url,description,website_url,language,next_fetch_at)
  VALUES(p_metadata->>'title',p_metadata->>'url',p_metadata->>'description',p_metadata->>'website_url',p_metadata->>'language',now())
  ON CONFLICT(url) DO NOTHING;
  RETURN QUERY SELECT f.* FROM driftread.feeds f WHERE f.url=p_metadata->>'url' AND f.participation_mode='normal';
END $$;
REVOKE ALL ON FUNCTION driftread.import_readable_source(jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.import_readable_source(jsonb) TO service_role;

CREATE OR REPLACE FUNCTION driftread.list_feed_categories()
RETURNS TABLE(category text)
LANGUAGE sql
SET search_path = pg_catalog
AS $$
  SELECT DISTINCT category
  FROM driftread.feeds
  WHERE archived_at IS NULL AND participation_mode='normal'
    AND category IS NOT NULL
    AND category != ''
  ORDER BY category
$$;

CREATE OR REPLACE FUNCTION driftread.list_feed_languages()
RETURNS TABLE(language text)
LANGUAGE sql
SET search_path = pg_catalog
AS $$
  SELECT DISTINCT language
  FROM driftread.feeds
  WHERE archived_at IS NULL AND participation_mode='normal'
    AND language IS NOT NULL
    AND language != ''
  ORDER BY language
$$;

CREATE OR REPLACE FUNCTION driftread.sample_feed_candidates(
  p_excluded_ids uuid[],
  p_categories   text[],
  p_mode         text,
  p_limit        int
)
RETURNS SETOF driftread.feeds
LANGUAGE sql
SET search_path = pg_catalog
AS $$
  -- `pivot` is referenced as a scalar subquery, not cross-joined into the
  -- FROM clause. A cross join makes the planner treat the comparison as a
  -- post-scan Join Filter instead of an Index Cond (verified with EXPLAIN),
  -- which scans the index from its very start on every call instead of
  -- seeking to the pivot. The scalar-subquery form is pulled out as an
  -- InitPlan and used as a real Index Cond bound.
  WITH pivot AS (
    SELECT random() AS v
  ),
  head AS (
    SELECT f.*
    FROM driftread.feeds f
    WHERE f.archived_at IS NULL AND f.participation_mode='normal'
      AND f.sample_key >= (SELECT v FROM pivot)
      AND (p_excluded_ids IS NULL OR NOT (f.id = ANY(p_excluded_ids)))
      AND (
        p_mode = 'unfiltered'
        OR (p_mode = 'in_categories' AND f.category = ANY(p_categories))
        OR (p_mode = 'not_in_categories' AND NOT (f.category = ANY(p_categories)))
        OR (p_mode = 'uncategorized' AND f.category IS NULL)
      )
    ORDER BY f.sample_key
    LIMIT LEAST(p_limit, 250)
  )
  (SELECT * FROM head)
  UNION ALL
  (SELECT f.*
   FROM driftread.feeds f
   WHERE f.archived_at IS NULL AND f.participation_mode='normal'
     AND f.sample_key < (SELECT v FROM pivot)
     AND (p_excluded_ids IS NULL OR NOT (f.id = ANY(p_excluded_ids)))
     AND (
       p_mode = 'unfiltered'
       OR (p_mode = 'in_categories' AND f.category = ANY(p_categories))
       OR (p_mode = 'not_in_categories' AND NOT (f.category = ANY(p_categories)))
       OR (p_mode = 'uncategorized' AND f.category IS NULL)
     )
   ORDER BY f.sample_key
   LIMIT GREATEST(LEAST(p_limit, 250) - (SELECT count(*) FROM head), 0))
$$;


CREATE OR REPLACE FUNCTION driftread.search_feeds(
  p_query             text,
  p_language          text DEFAULT NULL,
  p_cursor_rank       real DEFAULT NULL,
  p_cursor_created_at timestamptz DEFAULT NULL,
  p_cursor_id         uuid DEFAULT NULL,
  p_limit             int DEFAULT 20
)
RETURNS TABLE(
  id            uuid,
  title         text,
  url           text,
  description   text,
  snippet       text,
  website_url   text,
  language      text,
  category      text,
  tags          text[],
  article_count int,
  created_at    timestamptz,
  rank          real
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
  WITH query AS (
    SELECT websearch_to_tsquery('simple', p_query) AS tsq
  ),
  ranked AS (
    SELECT
      f.id, f.title, f.url, f.description, f.website_url, f.language, f.category,
      f.tags, f.article_count, f.created_at,
      ts_rank_cd(f.search_vector, query.tsq) AS rank,
      query.tsq AS tsq
    FROM driftread.feeds f
    CROSS JOIN query
    WHERE f.search_vector @@ query.tsq
      AND f.archived_at IS NULL AND f.participation_mode='normal'
      AND (p_language IS NULL OR f.language = p_language)
  ),
  paged AS (
    SELECT *
    FROM ranked
    WHERE
      p_cursor_rank IS NULL
      OR rank < p_cursor_rank
      OR (rank = p_cursor_rank AND created_at < p_cursor_created_at)
      OR (rank = p_cursor_rank AND created_at = p_cursor_created_at AND id < p_cursor_id)
    ORDER BY rank DESC, created_at DESC, id DESC
    LIMIT LEAST(GREATEST(p_limit, 1), 100)
  )
  SELECT
    id, title, url, description,
    -- Only one candidate body field here (unlike search_articles), so no
    -- field-matched-vs-headlined mismatch to resolve, and only one row-level
    -- call site (so no "compute once, reuse" concern either) — same
    -- HTML-stripping and bound-before-stripping-not-after as the generated
    -- column (description isn't guaranteed plain text either, see above),
    -- for the same reasons.
    ts_headline(
      'simple',
      driftread.strip_html_for_search(driftread.bounded_search_text(description)),
      tsq,
      'MaxFragments=1,MaxWords=35,MinWords=15,ShortWord=3,HighlightAll=false'
    ) AS snippet,
    website_url, language, category, tags, article_count, created_at, rank
  FROM paged
$$;
