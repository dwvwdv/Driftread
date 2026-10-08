-- A bounded personal snapshot, recomputed from source publication time.
-- First-seen/fetched time never creates heat; unknown source dates contribute
-- no heat. The stable snapshot timestamp lets callers compare/recompute history.
CREATE OR REPLACE FUNCTION driftread.personal_heat_snapshot(
 p_user_id uuid,p_at timestamptz,p_feed_id uuid DEFAULT NULL,p_unread_only boolean DEFAULT false,p_limit int DEFAULT 500
) RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 WITH cohort AS MATERIALIZED (
  SELECT f.* FROM driftread.feeds f
  WHERE f.archived_at IS NULL AND (
    (f.participation_mode='normal' AND EXISTS(SELECT 1 FROM driftread.user_feeds uf
       WHERE uf.feed_id=f.id AND uf.user_id=p_user_id AND uf.muted_at IS NULL))
    OR (f.participation_mode='signal_only' AND NOT EXISTS(SELECT 1 FROM driftread.user_feeds uf
       WHERE uf.feed_id=f.id AND uf.user_id=p_user_id AND uf.muted_at IS NOT NULL))
  )
 ), article_groups AS MATERIALIZED (
  SELECT a.id,a.feed_id,a.published_at,a.timeline_at,a.backfill,a.canonical_url,
   coalesce((SELECT 'story:'||sf.story_id::text FROM driftread.fact_articles fa
      JOIN driftread.story_facts sf ON sf.fact_id=fa.fact_id
      JOIN driftread.event_objects s ON s.id=sf.story_id
      WHERE fa.article_id=a.id AND NOT fa.excluded AND NOT sf.excluded AND s.merged_into IS NULL
      ORDER BY sf.story_id LIMIT 1),'url:'||a.canonical_url) group_key
  FROM driftread.articles a JOIN cohort c ON c.id=a.feed_id
  WHERE a.timeline_at>=p_at-interval '7 days' AND a.timeline_at<=p_at AND NOT a.backfill
 ), candidates AS MATERIALIZED (
  SELECT p.id,p.feed_id,p.feed_title,p.title,p.url,p.summary,p.author,p.published_at,p.fetched_at,p.timeline_at,
   (r.article_id IS NOT NULL) is_read,r.read_at,g.group_key,c.category,c.tags,c.language
  FROM article_groups g JOIN driftread.article_publications p ON p.id=g.id JOIN cohort c ON c.id=p.feed_id
  LEFT JOIN driftread.user_article_reads r ON r.article_id=p.id AND r.user_id=p_user_id
  WHERE (p_feed_id IS NULL OR p.feed_id=p_feed_id) AND (NOT p_unread_only OR r.article_id IS NULL)
   AND NOT EXISTS(SELECT 1 FROM driftread.user_feed_feedback fb WHERE fb.user_id=p_user_id AND fb.feed_id=p.feed_id
     AND (fb.feedback_type='disliked' OR (fb.feedback_type='skipped' AND fb.created_at>p_at-interval '14 days')))
  ORDER BY p.timeline_at DESC,p.id DESC LIMIT least(greatest(p_limit,1),500)
 ), evidence AS (
  SELECT g.group_key,CASE WHEN c.signal_group IS NULL THEN 'feed:'||c.id::text ELSE 'group:'||lower(c.signal_group) END participant_key,
   max(g.published_at) source_time
  FROM article_groups g JOIN cohort c ON c.id=g.feed_id
  WHERE g.published_at IS NOT NULL AND g.published_at<=p_at AND g.published_at>=p_at-interval '7 days'
   AND g.group_key IN(SELECT group_key FROM candidates)
  GROUP BY g.group_key,CASE WHEN c.signal_group IS NULL THEN 'feed:'||c.id::text ELSE 'group:'||lower(c.signal_group) END
 )
 SELECT jsonb_build_object('sources',coalesce((SELECT jsonb_agg(jsonb_build_object('id',c.id,'signal_group',c.signal_group,
  'participation_mode',c.participation_mode,'archived_at',c.archived_at,'last_fetch_at',c.last_fetch_at,
  'last_ok_at',c.last_ok_at,'fetch_interval_minutes',c.fetch_interval_minutes)) FROM cohort c),'[]'::jsonb),
 'candidates',coalesce((SELECT jsonb_agg(to_jsonb(c)) FROM candidates c),'[]'::jsonb),
 'evidence',coalesce((SELECT jsonb_agg(to_jsonb(e)) FROM evidence e),'[]'::jsonb))
$$;
REVOKE ALL ON FUNCTION driftread.personal_heat_snapshot(uuid,timestamptz,uuid,boolean,int) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.personal_heat_snapshot(uuid,timestamptz,uuid,boolean,int) TO service_role;
