-- routers/recommendations.py's candidate pool used plain `.limit(n)` with no
-- `.order()`, so on a catalog bigger than the fetch size it always pulled
-- PostgREST's default-ordered first N rows for every call, not a random
-- sample of the whole table — the in-memory `random.shuffle()` downstream
-- only reshuffles whichever N rows happened to be fetched, it can never
-- surface a row outside that fixed head. PostgREST's query builder has no
-- `ORDER BY random()`, so this has to be a database function.
--
-- One function serves all four pool shapes `_fetch_candidate_pool` needs
-- (unfiltered / category-in / category-not-in / category-is-null) rather
-- than four near-duplicate ones — `p_mode` is compared with `=` against a
-- fixed set of literals the Python caller controls, never concatenated into
-- the query text, so this doesn't reopen the PostgREST filter-injection
-- class SECURITY.md #14 fixed.
CREATE OR REPLACE FUNCTION sample_feed_candidates(
  p_excluded_ids uuid[],
  p_categories   text[],
  p_mode         text,
  p_limit        int
)
RETURNS SETOF feeds
LANGUAGE sql
AS $$
  SELECT *
  FROM feeds
  WHERE archived_at IS NULL
    AND (p_excluded_ids IS NULL OR NOT (id = ANY(p_excluded_ids)))
    AND (
      p_mode = 'unfiltered'
      OR (p_mode = 'in_categories' AND category = ANY(p_categories))
      -- category = ANY(...) evaluates to NULL (filtered out) when category
      -- IS NULL, same as PostgREST's `not_.in_` compiling to SQL NOT IN —
      -- matches the exploratory "known-but-different category" pool, which
      -- deliberately excludes both matching and uncategorized feeds (the
      -- latter has its own mode below).
      OR (p_mode = 'not_in_categories' AND NOT (category = ANY(p_categories)))
      OR (p_mode = 'uncategorized' AND category IS NULL)
    )
  ORDER BY random()
  LIMIT p_limit
$$;
