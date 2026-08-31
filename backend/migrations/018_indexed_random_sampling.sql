-- sample_feed_candidates (migrations 007, 010) sorted every matching row by
-- `random()` before taking `LIMIT` — a full scan-and-sort of the pool on
-- every call regardless of how small `p_limit` is. This was flagged as a
-- scaling risk in TODO.md and in routers/recommendations.py's own comments
-- once the catalog outgrows a few thousand rows, since the endpoint is
-- public/unauthenticated and can trigger up to three such passes per call.
--
-- Store one random key per feed instead, indexed, and turn each call into an
-- indexed range scan from a random pivot: rows are read in `sample_key`
-- order starting just past a fresh random threshold, so `LIMIT` short-
-- circuits the scan instead of touching every matching row. If the pivot
-- lands near the end of the key space and the head slice comes up short, a
-- second bounded scan wraps around to the start to fill the rest of
-- `p_limit`. Both scans stay index range scans regardless of table size.
ALTER TABLE driftread.feeds
  ADD COLUMN IF NOT EXISTS sample_key DOUBLE PRECISION NOT NULL DEFAULT random();

CREATE INDEX IF NOT EXISTS feeds_sample_key_idx
  ON driftread.feeds (sample_key)
  WHERE archived_at IS NULL;

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
    WHERE f.archived_at IS NULL
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
   WHERE f.archived_at IS NULL
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
