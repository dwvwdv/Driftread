-- Read-only, repeatable snapshot. Run with psql and ON_ERROR_STOP enabled.
-- No fetching, requeueing, paid requests or user data are involved.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;

WITH counts AS (
 SELECT feed_id,count(*) AS actual FROM driftread.articles GROUP BY feed_id
), sources AS (
 SELECT f.*,coalesce(c.actual,0) AS actual
 FROM driftread.feeds f LEFT JOIN counts c ON c.feed_id=f.id
)
SELECT now() AS observed_at,count(*) AS feeds,
 count(*) FILTER(WHERE article_count=0) AS zero_article_sources,
 count(*) FILTER(WHERE article_count<>actual) AS count_mismatches,
 sum(article_count) AS cached_articles,sum(actual) AS actual_articles
FROM sources;

-- Show every mismatch, including nonzero caches; the zero-only list misses them.
WITH counts AS (
 SELECT feed_id,count(*) AS actual FROM driftread.articles GROUP BY feed_id
)
SELECT f.id,f.title,f.article_count,coalesce(c.actual,0) AS actual,
 f.last_fetch_result,f.next_fetch_at
FROM driftread.feeds f LEFT JOIN counts c ON c.feed_id=f.id
WHERE f.article_count<>coalesce(c.actual,0) ORDER BY f.title,f.id;

SELECT id,title,url,article_count,archived_at,last_fetch_at,last_ok_at,
 last_fetched_at,next_fetch_at,admitted_at,last_fetch_result,
 CASE
  WHEN archived_at IS NOT NULL AND last_failure_reason LIKE 'Excluded before admission:%' THEN 'excluded'
  WHEN archived_at IS NOT NULL THEN 'archived_other'
  WHEN last_failure_reason IS NOT NULL THEN 'failure'
  WHEN last_fetch_at IS NULL AND last_fetched_at IS NULL THEN 'never_attempted'
  ELSE 'zero_without_historical_reason'
 END AS diagnostic_group
FROM driftread.feeds WHERE article_count=0 ORDER BY archived_at NULLS FIRST,title,id;

WITH latest AS (
 SELECT DISTINCT ON(candidate_id) candidate_id,status,engine,policy_version,error_code
 FROM driftread.source_reviews ORDER BY candidate_id,created_at DESC,id DESC
)
SELECT r.status,r.engine,r.policy_version,r.error_code,c.status AS candidate_status,count(*)
FROM latest r JOIN driftread.discovery_candidates c ON c.id=r.candidate_id
GROUP BY 1,2,3,4,5 ORDER BY count(*) DESC;

COMMIT;
