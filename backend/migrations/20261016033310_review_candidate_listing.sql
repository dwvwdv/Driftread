-- Filter on each candidate's latest review before counting or paginating.
CREATE INDEX IF NOT EXISTS source_reviews_candidate_latest
 ON driftread.source_reviews(candidate_id,created_at DESC,id DESC);

CREATE OR REPLACE FUNCTION driftread.list_review_candidates(
 p_status text DEFAULT 'pending',p_limit integer DEFAULT 20,p_offset integer DEFAULT 0,
 p_min_referrers integer DEFAULT 0
) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=''
AS $$
DECLARE response jsonb;
BEGIN
 IF p_status IS NULL OR p_status NOT IN ('pending','ready','awaiting_human')
  OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 200
  OR p_offset IS NULL OR p_offset<0 OR p_min_referrers IS NULL OR p_min_referrers<0 THEN
  RAISE EXCEPTION 'Invalid review candidate filter or pagination' USING ERRCODE='22023';
 END IF;

 WITH filtered AS MATERIALIZED (
  SELECT c.*,
   CASE WHEN r.id IS NULL THEN NULL ELSE jsonb_build_object(
    'id',r.id,'candidate_id',r.candidate_id,'status',r.status,'engine',r.engine,
    'model',r.model,'result',r.result,'error_code',r.error_code,
    'created_at',r.created_at,'updated_at',r.updated_at
   ) END AS latest_review
  FROM driftread.discovery_candidates c
  LEFT JOIN LATERAL (
   SELECT sr.* FROM driftread.source_reviews sr WHERE sr.candidate_id=c.id
   ORDER BY sr.created_at DESC,sr.id DESC LIMIT 1
  ) r ON true
  WHERE c.status='pending' AND c.manual_override IS NULL
   AND c.referring_feed_count>=p_min_referrers
   AND (p_status='pending'
    OR (p_status='awaiting_human' AND r.status='awaiting_human')
    OR (p_status='ready' AND (r.status='ready' OR (r.status='leased' AND r.lease_expires_at<now()))
     AND r.engine='external_agent'
     AND r.snapshot IS NOT NULL AND r.snapshot_expires_at>now() AND r.available_at<=now()
     AND r.attempts<3
     AND r.candidate_version=c.review_version
     AND EXISTS(SELECT 1 FROM driftread.app_settings s WHERE s.key='source.review'
      AND coalesce((s.value->>'enabled')::boolean,false) AND s.version=r.policy_version)
     AND NOT driftread.source_review_candidate_blocked(c.id)))
 ), page AS (
  SELECT * FROM filtered
  ORDER BY referring_feed_count DESC,discovered_at DESC,id DESC
  LIMIT p_limit OFFSET p_offset
 )
 SELECT jsonb_build_object(
  'items',coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.referring_feed_count DESC,p.discovered_at DESC,p.id DESC) FROM page p),'[]'::jsonb),
  'total',(SELECT count(*) FROM filtered),'limit',p_limit,'offset',p_offset
 ) INTO response;
 RETURN response;
END $$;

REVOKE ALL ON FUNCTION driftread.list_review_candidates(text,integer,integer,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.list_review_candidates(text,integer,integer,integer) TO service_role;

-- A stale browser selection must not resolve a newer or unfinished review.
CREATE OR REPLACE FUNCTION driftread.resolve_source_review(p_review_id uuid,p_decision text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; c driftread.discovery_candidates; latest_id uuid;
BEGIN
 IF p_decision IS NULL OR p_decision NOT IN('accept','reject','defer') THEN RAISE EXCEPTION 'invalid decision'; END IF;
 SELECT candidate_id INTO r.candidate_id FROM driftread.source_reviews WHERE id=p_review_id;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=r.candidate_id FOR UPDATE;
 SELECT * INTO r FROM driftread.source_reviews WHERE id=p_review_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 SELECT id INTO latest_id FROM driftread.source_reviews WHERE candidate_id=c.id
  ORDER BY created_at DESC,id DESC LIMIT 1;
 IF r.status<>'awaiting_human' OR jsonb_typeof(r.result) IS DISTINCT FROM 'object'
  OR r.candidate_version<>c.review_version OR latest_id IS DISTINCT FROM r.id THEN
  RETURN jsonb_build_object('error','conflict');
 END IF;
 IF c.manual_override IS NOT NULL OR c.status<>'pending' OR driftread.source_review_candidate_blocked(c.id) THEN RETURN jsonb_build_object('error','manual_decision'); END IF;
 IF p_decision='accept' THEN
  PERFORM driftread.admit_source_review(c.id,driftread.source_review_known_category(r.result->>'category'),
   driftread.source_review_known_tags(r.result->'tags'));
 ELSIF p_decision='reject' THEN
  UPDATE driftread.discovery_candidates SET status='rejected',reviewed_at=now() WHERE id=c.id;
 ELSE
  UPDATE driftread.discovery_candidates SET status='held',reviewed_at=now() WHERE id=c.id;
 END IF;
 UPDATE driftread.source_reviews SET status=CASE p_decision WHEN 'accept' THEN 'accepted' WHEN 'reject' THEN 'rejected' ELSE 'deferred' END,
  updated_at=now() WHERE id=r.id RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;
REVOKE ALL ON FUNCTION driftread.resolve_source_review(uuid,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.resolve_source_review(uuid,text) TO service_role;

NOTIFY pgrst, 'reload schema';
