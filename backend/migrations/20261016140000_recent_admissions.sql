-- Catalog admission provenance is independent of feeds.admitted_at (the later
-- first-fetch quality gate). Do not infer historical provenance from settings,
-- rediscovered candidates or review rows that may later be pruned.
CREATE TABLE IF NOT EXISTS driftread.feed_admissions (
 feed_id uuid PRIMARY KEY REFERENCES driftread.feeds(id) ON DELETE CASCADE,
 admitted_at timestamptz NOT NULL,
 method text NOT NULL CHECK(method IN('ai_auto','human_agree','human_disagree','manual','referrer_auto','unknown')),
 engine text CHECK(engine IN('external_agent','jev')),
 -- Evidence IDs deliberately have no FK: pruning reviews/candidates must not
 -- erase or rewrite the original admission method.
 candidate_id uuid,
 review_id uuid
);
ALTER TABLE driftread.feed_admissions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.feed_admissions FROM PUBLIC,anon,authenticated;
GRANT ALL ON driftread.feed_admissions TO service_role;
CREATE INDEX IF NOT EXISTS feeds_created_at_id_idx ON driftread.feeds(created_at DESC,id DESC);



CREATE OR REPLACE FUNCTION driftread.admit_source_review(p_candidate_id uuid,p_category text,p_tags text[],p_method text,p_engine text,p_review_id uuid)
 RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates; f uuid; aliases text[];
BEGIN
 IF p_method IS NULL OR p_method NOT IN('ai_auto','human_agree','human_disagree','manual','referrer_auto','unknown')
  OR (p_engine IS NOT NULL AND p_engine NOT IN('external_agent','jev')) THEN
  RAISE EXCEPTION 'Invalid admission provenance' USING ERRCODE='22023';
 END IF;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=p_candidate_id FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 aliases:=ARRAY[c.feed_url];
 IF c.feed_url ~* '/(feed|rss|atom)/?$' THEN aliases:=ARRAY[rtrim(c.feed_url,'/'),rtrim(c.feed_url,'/')||'/']; END IF;
 IF EXISTS(SELECT 1 FROM driftread.discovery_candidates WHERE id<>c.id AND feed_url=ANY(aliases) AND status IN('held','rejected')) THEN
  RAISE EXCEPTION 'candidate alias blocked';
 END IF;
 SELECT id INTO f FROM driftread.feeds WHERE url=ANY(aliases) ORDER BY url LIMIT 1;
 IF f IS NULL THEN
  INSERT INTO driftread.feeds(title,url,website_url,category,tags,next_fetch_at)
  VALUES(coalesce(nullif(c.title,''),c.source_host,c.feed_url),c.feed_url,c.website_url,p_category,coalesce(p_tags,'{}'),now())
  ON CONFLICT(url) DO NOTHING RETURNING id INTO f;
  IF f IS NULL THEN
   SELECT id INTO f FROM driftread.feeds WHERE url=c.feed_url;
  ELSE
   INSERT INTO driftread.feed_admissions(feed_id,admitted_at,method,engine,candidate_id,review_id)
   SELECT f,created_at,p_method,p_engine,c.id,p_review_id FROM driftread.feeds WHERE id=f;
  END IF;
 END IF;
 UPDATE driftread.discovery_candidates SET status='imported',feed_id=f,reviewed_at=now() WHERE id=c.id;
 RETURN f;
END $$;

-- Compatibility for older service callers; current entry points are explicit.
CREATE OR REPLACE FUNCTION driftread.admit_source_review(p_candidate_id uuid,p_category text,p_tags text[])
 RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates;
BEGIN
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=p_candidate_id FOR UPDATE;
 RETURN driftread.admit_source_review(p_candidate_id,p_category,p_tags,
  CASE WHEN c.manual_override='approve' THEN 'manual' ELSE 'referrer_auto' END,NULL,NULL);
END $$;


CREATE OR REPLACE FUNCTION driftread.submit_source_review(p_review_id uuid,p_reviewer_id text,p_token uuid,p_hash text,p_result jsonb)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; c driftread.discovery_candidates; s driftread.app_settings; outcome text;
BEGIN
 SELECT * INTO s FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 SELECT candidate_id INTO r.candidate_id FROM driftread.source_reviews WHERE id=p_review_id;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=r.candidate_id FOR UPDATE;
 SELECT * INTO r FROM driftread.source_reviews WHERE id=p_review_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 IF r.reviewer_id IS DISTINCT FROM p_reviewer_id OR r.lease_token IS DISTINCT FROM p_token OR r.snapshot_hash IS DISTINCT FROM p_hash THEN
  RETURN jsonb_build_object('error','lease'); END IF;
 IF r.result IS NOT NULL THEN
  IF r.result=p_result THEN RETURN to_jsonb(r); END IF;
  RETURN jsonb_build_object('error','already_submitted');
 END IF;
 IF r.status<>'leased' OR r.lease_expires_at<=now() OR r.snapshot_expires_at<=now() THEN RETURN jsonb_build_object('error','lease'); END IF;
 IF c.review_version<>r.candidate_version OR c.manual_override IS NOT NULL OR c.status<>'pending' OR driftread.source_review_candidate_blocked(c.id) THEN
  UPDATE driftread.source_reviews SET status='overridden',updated_at=now() WHERE id=r.id RETURNING * INTO r; RETURN to_jsonb(r);
 END IF;
 IF s.version IS DISTINCT FROM r.policy_version OR NOT coalesce((s.value->>'enabled')::boolean,false) THEN
  UPDATE driftread.source_reviews SET status='stale',updated_at=now() WHERE id=r.id RETURNING * INTO r; RETURN to_jsonb(r);
 END IF;
 IF jsonb_typeof(p_result) IS DISTINCT FROM 'object' OR octet_length(p_result::text)>32768
 OR coalesce(p_result->>'decision','') NOT IN('accept','reject','abstain')
 OR jsonb_typeof(p_result->'confidence') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_result->'dimensions'->'quality') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_result->'dimensions'->'spam_probability') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_result->'dimensions'->'relevance') IS DISTINCT FROM 'number'
 OR (p_result->>'confidence')::numeric NOT BETWEEN 0 AND 1
 OR (p_result->'dimensions'->>'quality')::numeric NOT BETWEEN 0 AND 1
 OR (p_result->'dimensions'->>'spam_probability')::numeric NOT BETWEEN 0 AND 1
 OR (p_result->'dimensions'->>'relevance')::numeric NOT BETWEEN 0 AND 1
 THEN RAISE EXCEPTION 'invalid result'; END IF;
 outcome:=CASE p_result->>'decision' WHEN 'accept' THEN 'accepted' WHEN 'reject' THEN 'rejected' ELSE 'deferred' END;
 IF outcome='accepted' AND (NOT EXISTS(SELECT 1 FROM jsonb_array_elements(r.snapshot->'samples') x WHERE length(x->>'text')>=100 AND NOT driftread.source_review_sample_is_demo(x))
 OR (p_result->>'confidence')::numeric < (r.policy->>'min_confidence')::numeric
 OR (p_result->'dimensions'->>'quality')::numeric < (r.policy->>'min_quality')::numeric
 OR (p_result->'dimensions'->>'relevance')::numeric < 0.5
 OR (p_result->'dimensions'->>'spam_probability')::numeric > (r.policy->>'max_spam_probability')::numeric) THEN outcome:='deferred'; END IF;
 IF outcome IN('accepted','rejected') AND NOT coalesce((s.value->>'skip_human_review')::boolean,false) THEN outcome:='awaiting_human'; END IF;
 PERFORM set_config('driftread.review_internal','1',true);
 IF outcome='accepted' THEN
  PERFORM driftread.admit_source_review(c.id,driftread.source_review_known_category(p_result->>'category'),
   driftread.source_review_known_tags(p_result->'tags'),'ai_auto',r.engine,r.id);
 END IF;
 PERFORM set_config('driftread.review_internal','',true);
 -- Model rejects remain reviewable pending candidates; never block a host.
 INSERT INTO driftread.analysis_results(capability,subject_id,review_id,engine,model,input_hash,policy_version,output)
 VALUES('source_review',c.id,r.id,r.engine,r.model,p_hash,r.policy_version,p_result);
 UPDATE driftread.source_reviews SET status=outcome,result=p_result,
  error_code=CASE WHEN outcome='deferred' AND jsonb_array_length(r.snapshot->'samples')>0
   AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(r.snapshot->'samples') x
    WHERE NOT driftread.source_review_sample_is_demo(x)) THEN 'default_demo_content' ELSE NULL END,updated_at=now() WHERE id=r.id RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;

CREATE OR REPLACE FUNCTION driftread.judge_source_review(
 p_review_id uuid,p_assessment text,p_decision text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; c driftread.discovery_candidates; latest_id uuid;
 ai_decision text; final_decision text;
BEGIN
 IF p_assessment IS NULL OR p_assessment NOT IN('agree','disagree')
  OR (p_decision IS NOT NULL AND p_decision NOT IN('accept','reject','defer'))
  OR (p_assessment='agree' AND p_decision IS NOT NULL) THEN
  RETURN jsonb_build_object('error','invalid_judgment');
 END IF;
 -- Match request, submission and legacy resolution: candidate, then review.
 SELECT candidate_id INTO r.candidate_id FROM driftread.source_reviews WHERE id=p_review_id;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=r.candidate_id FOR UPDATE;
 SELECT * INTO r FROM driftread.source_reviews WHERE id=p_review_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 SELECT id INTO latest_id FROM driftread.source_reviews WHERE candidate_id=c.id
  ORDER BY created_at DESC,id DESC LIMIT 1;
 IF r.status NOT IN('awaiting_human','rejected','deferred')
  OR jsonb_typeof(r.result) IS DISTINCT FROM 'object'
  OR coalesce(r.result->>'decision','') NOT IN('accept','reject','abstain')
  OR r.human_review IS NOT NULL OR r.candidate_version<>c.review_version
  OR latest_id IS DISTINCT FROM r.id THEN
  RETURN jsonb_build_object('error','conflict');
 END IF;
 IF c.manual_override IS NOT NULL OR c.status<>'pending' OR driftread.source_review_candidate_blocked(c.id) THEN
  RETURN jsonb_build_object('error','manual_decision');
 END IF;
 ai_decision:=CASE r.result->>'decision' WHEN 'abstain' THEN 'defer' ELSE r.result->>'decision' END;
 IF p_assessment='agree' THEN
  -- A model recommendation that failed policy gates is not permission to admit.
  IF ai_decision='accept' AND r.status='deferred' THEN
   RETURN jsonb_build_object('error','policy_deferred');
  END IF;
  final_decision:=ai_decision;
 ELSE
  final_decision:=coalesce(p_decision,CASE ai_decision WHEN 'accept' THEN 'reject' WHEN 'reject' THEN 'accept' END);
  IF final_decision IS NULL OR final_decision=ai_decision THEN
   RETURN jsonb_build_object('error','invalid_judgment');
  END IF;
 END IF;
 IF final_decision='accept' THEN
  PERFORM driftread.admit_source_review(c.id,driftread.source_review_known_category(r.result->>'category'),
   driftread.source_review_known_tags(r.result->'tags'),
   CASE p_assessment WHEN 'agree' THEN 'human_agree' ELSE 'human_disagree' END,r.engine,r.id);
 ELSIF final_decision='reject' THEN
  UPDATE driftread.discovery_candidates SET status='rejected',reviewed_at=now() WHERE id=c.id;
 ELSE
  UPDATE driftread.discovery_candidates SET status='held',reviewed_at=now() WHERE id=c.id;
 END IF;
 UPDATE driftread.source_reviews SET
  status=CASE final_decision WHEN 'accept' THEN 'accepted' WHEN 'reject' THEN 'rejected' ELSE 'deferred' END,
  human_review=jsonb_build_object('assessment',p_assessment,'ai_decision',r.result->>'decision',
   'decision',final_decision,'decided_at',now()),updated_at=now()
 WHERE id=r.id RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;

CREATE OR REPLACE FUNCTION driftread.promote_discovery_candidate(p_candidate_id uuid,p_category text DEFAULT NULL,p_tags text[] DEFAULT NULL,p_manual boolean DEFAULT false)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates; s driftread.app_settings; f uuid; feed jsonb;
BEGIN
 SELECT * INTO s FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=p_candidate_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 IF c.status='rejected' THEN RETURN jsonb_build_object('error','already_rejected'); END IF;
 IF c.status='held' AND NOT p_manual THEN RETURN NULL; END IF;
 IF NOT p_manual AND c.status<>'approved' AND coalesce((s.value->>'enabled')::boolean,false) THEN RETURN NULL; END IF;
 IF p_manual THEN
  UPDATE driftread.discovery_candidates SET status='approved',reviewed_at=now(),approved_category=p_category,
  approved_tags=coalesce(p_tags,'{}') WHERE id=c.id;
 ELSE
  PERFORM set_config('driftread.review_internal','1',true);
 END IF;
 f:=driftread.admit_source_review(c.id,coalesce(p_category,c.approved_category),coalesce(p_tags,c.approved_tags),
  CASE WHEN p_manual OR c.manual_override='approve' THEN 'manual' ELSE 'referrer_auto' END,NULL,NULL);
 PERFORM set_config('driftread.review_internal','',true);
 SELECT to_jsonb(x) INTO feed FROM driftread.feeds x WHERE id=f;
 RETURN feed;
END $$;

-- Limit the feed page before the join; one bounded query for all provenance.
CREATE OR REPLACE FUNCTION driftread.list_recent_feed_admissions(p_limit integer DEFAULT 10)
 RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE response jsonb;
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50 THEN
  RAISE EXCEPTION 'Invalid recent admission limit' USING ERRCODE='22023';
 END IF;
 WITH recent AS MATERIALIZED (
  SELECT id,title,url,website_url,created_at FROM driftread.feeds
  ORDER BY created_at DESC,id DESC LIMIT p_limit
 ), rows AS (
  SELECT f.id AS feed_id,f.title,f.url AS feed_url,f.website_url,f.created_at AS admitted_at,
   coalesce(a.method,'unknown') AS method,a.engine,a.candidate_id,a.review_id
  FROM recent f LEFT JOIN driftread.feed_admissions a ON a.feed_id=f.id
 )
 SELECT jsonb_build_object('items',coalesce(jsonb_agg(to_jsonb(r) ORDER BY r.admitted_at DESC,r.feed_id DESC),'[]'::jsonb),
  'limit',p_limit) INTO response FROM rows r;
 RETURN response;
END $$;
REVOKE ALL ON FUNCTION driftread.admit_source_review(uuid,text,text[],text,text,uuid),
 driftread.admit_source_review(uuid,text,text[]),driftread.submit_source_review(uuid,text,uuid,text,jsonb),
 driftread.judge_source_review(uuid,text,text),driftread.promote_discovery_candidate(uuid,text,text[],boolean),
 driftread.list_recent_feed_admissions(integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.admit_source_review(uuid,text,text[],text,text,uuid),
 driftread.admit_source_review(uuid,text,text[]),driftread.submit_source_review(uuid,text,uuid,text,jsonb),
 driftread.judge_source_review(uuid,text,text),driftread.promote_discovery_candidate(uuid,text,text[],boolean),
 driftread.list_recent_feed_admissions(integer) TO service_role;

NOTIFY pgrst, 'reload schema';
