-- Content-based source identity used before a new feed stores articles.
-- Candidates are active readable sources sharing canonical item URLs with the
-- new feed; recent_overlap counts how many of the candidate's newest articles
-- are in the sample, so a category feed is not mistaken for a mirror of the
-- main feed. Thresholds live in services/source_identity.py.
CREATE OR REPLACE FUNCTION driftread.find_mirror_sources(
  p_canonical_urls text[],
  p_exclude_feed_id uuid DEFAULT NULL
)
RETURNS TABLE(feed_id uuid, feed_url text, title text, overlap integer, recent_overlap integer)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
  WITH sample AS (
    SELECT DISTINCT u
    FROM unnest(p_canonical_urls[1:100]) AS u
    WHERE u IS NOT NULL AND u <> ''
  ), hits AS (
    SELECT a.feed_id, count(DISTINCT a.canonical_url)::integer AS overlap
    FROM driftread.articles a
    JOIN sample s ON s.u = a.canonical_url
    WHERE a.feed_id IS DISTINCT FROM p_exclude_feed_id
    GROUP BY a.feed_id
  ), ranked AS (
    SELECT f.id, f.url, f.title, f.created_at, h.overlap
    FROM hits h
    JOIN driftread.feeds f ON f.id = h.feed_id
    WHERE f.archived_at IS NULL AND f.participation_mode = 'normal'
    ORDER BY h.overlap DESC, f.created_at, f.id
    LIMIT 5
  )
  SELECT r.id, r.url, r.title, r.overlap, (
    SELECT count(DISTINCT recent.canonical_url)::integer
    FROM (
      SELECT a.canonical_url
      FROM driftread.articles a
      WHERE a.feed_id = r.id
      ORDER BY a.timeline_at DESC, a.id DESC
      LIMIT (SELECT count(*) FROM sample)
    ) recent
    JOIN sample s ON s.u = recent.canonical_url
  )
  FROM ranked r
  ORDER BY r.overlap DESC, r.created_at, r.id
$$;
REVOKE ALL ON FUNCTION driftread.find_mirror_sources(text[], uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.find_mirror_sources(text[], uuid) TO service_role;
NOTIFY pgrst, 'reload schema';
