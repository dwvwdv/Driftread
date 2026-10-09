-- Owner-scoped, bounded heat evidence history. Never persists article text or
-- preferences. Cohort/health are observations, not inferred historical health.
CREATE TABLE IF NOT EXISTS driftread.user_heat_snapshots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 snapshot_at timestamptz NOT NULL,
 feed_id uuid,
 unread_only boolean NOT NULL,
 sources jsonb NOT NULL,
 candidates jsonb NOT NULL,
 evidence jsonb NOT NULL,
 repaired_at timestamptz
);
CREATE INDEX IF NOT EXISTS user_heat_snapshots_owner_time_idx
 ON driftread.user_heat_snapshots(user_id,snapshot_at DESC,id DESC);
ALTER TABLE driftread.user_heat_snapshots ENABLE ROW LEVEL SECURITY;
-- Reads go through the backend projection, including for the owner. No direct
-- authenticated SELECT can expose evidence after rights/private revocation.
REVOKE ALL ON driftread.user_heat_snapshots FROM PUBLIC,anon,authenticated;
GRANT ALL ON driftread.user_heat_snapshots TO service_role;

CREATE OR REPLACE FUNCTION driftread.capture_personal_heat(
 p_user_id uuid,p_at timestamptz,p_feed_id uuid DEFAULT NULL,p_unread_only boolean DEFAULT false,p_limit int DEFAULT 500
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE payload jsonb; sid uuid;
BEGIN
 IF p_at < clock_timestamp()-interval '5 minutes' OR p_at > clock_timestamp()+interval '5 minutes' THEN
  RAISE EXCEPTION 'capture requires current observation time' USING ERRCODE='22023';
 END IF;
 -- Serialize captures/retention and repair for the same owner.
 PERFORM pg_advisory_xact_lock(hashtextextended('heat-history:'||p_user_id::text,0));
 payload:=driftread.personal_heat_snapshot(p_user_id,p_at,p_feed_id,p_unread_only,p_limit);
 INSERT INTO driftread.user_heat_snapshots(user_id,snapshot_at,feed_id,unread_only,sources,candidates,evidence)
 VALUES(p_user_id,p_at,p_feed_id,p_unread_only,payload->'sources',
  coalesce((SELECT jsonb_agg(jsonb_build_object('id',c->>'id','group_key',c->>'group_key'))
   FROM jsonb_array_elements(payload->'candidates') c),'[]'::jsonb),payload->'evidence') RETURNING id INTO sid;
 DELETE FROM driftread.user_heat_snapshots WHERE user_id=p_user_id AND (
  snapshot_at < p_at-interval '30 days' OR id IN (
   SELECT id FROM driftread.user_heat_snapshots WHERE user_id=p_user_id
   ORDER BY snapshot_at DESC,id DESC OFFSET 100));
 RETURN payload||jsonb_build_object('snapshot_id',sid,'snapshot_at',p_at);
END $$;

CREATE OR REPLACE FUNCTION driftread.read_personal_heat_history(p_user_id uuid,p_id uuid)
 RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 WITH saved AS MATERIALIZED (
  SELECT * FROM driftread.user_heat_snapshots WHERE user_id=p_user_id AND id=p_id
   AND snapshot_at>=now()-interval '30 days'
 ), allowed_sources AS MATERIALIZED (
  SELECT f.* FROM driftread.feeds f JOIN saved s ON true
  WHERE f.id IN(SELECT (src->>'id')::uuid FROM jsonb_array_elements(s.sources) src)
   AND f.archived_at IS NULL AND (
    (f.participation_mode='normal' AND EXISTS(SELECT 1 FROM driftread.user_feeds uf
      WHERE uf.user_id=p_user_id AND uf.feed_id=f.id AND uf.muted_at IS NULL))
    OR (f.participation_mode='signal_only' AND NOT EXISTS(SELECT 1 FROM driftread.user_feeds uf
      WHERE uf.user_id=p_user_id AND uf.feed_id=f.id AND uf.muted_at IS NOT NULL)))
 ), candidates AS MATERIALIZED (
  SELECT p.id,p.feed_id,p.feed_title,p.title,p.url,p.summary,p.author,p.published_at,p.fetched_at,p.timeline_at,p.discovered_at,p.backfill,p.backfill_reason,p.current_revision_id,
   (r.article_id IS NOT NULL) is_read,r.read_at,c->>'group_key' group_key,f.category,f.tags,f.language
  FROM saved s CROSS JOIN LATERAL jsonb_array_elements(s.candidates) c
  JOIN driftread.article_publications p ON p.id=(c->>'id')::uuid
  JOIN allowed_sources f ON f.id=p.feed_id
  LEFT JOIN driftread.user_article_reads r ON r.article_id=p.id AND r.user_id=p_user_id
  WHERE (NOT s.unread_only OR r.article_id IS NULL)
   AND NOT EXISTS(SELECT 1 FROM driftread.user_feed_feedback fb WHERE fb.user_id=p_user_id AND fb.feed_id=p.feed_id
    AND (fb.feedback_type='disliked' OR (fb.feedback_type='skipped' AND fb.created_at>now()-interval '14 days')))
 ) SELECT jsonb_build_object('snapshot_id',s.id,'snapshot_at',s.snapshot_at,'repaired_at',s.repaired_at,
  'sources',s.sources,'candidates',coalesce((SELECT jsonb_agg(to_jsonb(c)) FROM candidates c),'[]'::jsonb),
  'evidence',coalesce((SELECT jsonb_agg(e) FROM jsonb_array_elements(s.evidence) e
   JOIN allowed_sources f ON f.id=(e->>'feed_id')::uuid
   WHERE e->>'group_key' IN(SELECT group_key FROM candidates)),'[]'::jsonb)) FROM saved s
$$;

-- Recompute only captured groups, with the captured source cohort and mirror
-- identity. Newly ingested source publications may repair old evidence; today's
-- source health, preferences and subscriptions never rewrite past observations.
CREATE OR REPLACE FUNCTION driftread.repair_personal_heat_history(p_user_id uuid,p_limit int DEFAULT 20)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s driftread.user_heat_snapshots; repaired int:=0; new_evidence jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('heat-history:'||p_user_id::text,0));
 FOR s IN SELECT * FROM driftread.user_heat_snapshots WHERE user_id=p_user_id
  AND snapshot_at>=now()-interval '30 days' ORDER BY snapshot_at DESC,id DESC
  LIMIT least(greatest(p_limit,1),20) FOR UPDATE LOOP
  WITH sources AS MATERIALIZED (
   SELECT (src->>'id')::uuid feed_id,
    CASE WHEN src->>'signal_group' IS NULL THEN 'feed:'||(src->>'id')
     ELSE 'group:'||lower(src->>'signal_group') END participant_key
   FROM jsonb_array_elements(s.sources) src
  ), grouped AS (
   SELECT a.feed_id,a.published_at,src.participant_key,
    coalesce((SELECT 'story:'||sf.story_id::text FROM driftread.fact_articles fa
     JOIN driftread.story_facts sf ON sf.fact_id=fa.fact_id JOIN driftread.event_objects story ON story.id=sf.story_id
     WHERE fa.article_id=a.id AND NOT fa.excluded AND NOT sf.excluded AND story.merged_into IS NULL
     ORDER BY sf.story_id LIMIT 1),'url:'||a.canonical_url) group_key
   FROM driftread.articles a JOIN sources src ON src.feed_id=a.feed_id
   WHERE NOT a.backfill AND a.published_at<=s.snapshot_at AND a.published_at>=s.snapshot_at-interval '7 days'
  ), combined AS (
   SELECT (e->>'feed_id')::uuid feed_id,e->>'group_key' group_key,e->>'participant_key' participant_key,
    (e->>'source_time')::timestamptz source_time FROM jsonb_array_elements(s.evidence) e
   UNION ALL
   SELECT feed_id,group_key,participant_key,published_at FROM grouped
    WHERE group_key IN(SELECT c->>'group_key' FROM jsonb_array_elements(s.candidates) c)
  ), latest AS (
   SELECT feed_id,group_key,participant_key,max(source_time) source_time FROM combined
   GROUP BY feed_id,group_key,participant_key
  ) SELECT coalesce(jsonb_agg(to_jsonb(latest)),'[]'::jsonb) INTO new_evidence FROM latest;
  UPDATE driftread.user_heat_snapshots SET evidence=new_evidence,repaired_at=clock_timestamp()
   WHERE id=s.id AND user_id=p_user_id;
  repaired:=repaired+1;
 END LOOP;
 RETURN jsonb_build_object('repaired_count',repaired,'limit',least(greatest(p_limit,1),20));
END $$;

REVOKE ALL ON FUNCTION driftread.capture_personal_heat(uuid,timestamptz,uuid,boolean,int),
 driftread.read_personal_heat_history(uuid,uuid),driftread.repair_personal_heat_history(uuid,int)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.capture_personal_heat(uuid,timestamptz,uuid,boolean,int),
 driftread.read_personal_heat_history(uuid,uuid),driftread.repair_personal_heat_history(uuid,int) TO service_role;
