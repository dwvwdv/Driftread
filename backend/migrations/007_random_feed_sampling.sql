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
  -- Defense in depth: the only caller (`_sample_feeds` in
  -- routers/recommendations.py) never asks for more than `limit * 5` rows
  -- with `limit` capped at 50 by the API (so <= 250) — clamp here too so a
  -- direct RPC call (see grants below) can't force an unbounded
  -- `ORDER BY random()` pass over the whole table.
  LIMIT LEAST(p_limit, 250)
$$;

-- New functions in the `public` schema get EXECUTE granted to PUBLIC by
-- default, which PostgREST's `anon`/`authenticated` roles inherit — so
-- without this, a caller holding only the browser-visible anon key could
-- invoke this function directly (bypassing the API's rate limiting and its
-- 50-item id/category array caps entirely, `LIMIT` clamp above notwithstanding)
-- and repeatedly drive an `ORDER BY random()` pass over `feeds`. The backend
-- only ever calls this via the service_role client (database.py), same as
-- every other write path in this project, so lock it down the same way.
REVOKE EXECUTE ON FUNCTION sample_feed_candidates(uuid[], text[], text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sample_feed_candidates(uuid[], text[], text, int) TO service_role;
