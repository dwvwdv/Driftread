-- Discovery controls exploration only; source.review controls review and AI admission.
ALTER TABLE driftread.source_reviews ADD COLUMN IF NOT EXISTS gate_reason text;

CREATE OR REPLACE FUNCTION driftread.admit_source_review(p_candidate_id uuid,p_category text,p_tags text[],p_method text,p_engine text,p_review_id uuid)
 RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates; f uuid; aliases text[];
BEGIN
 IF p_method IS NULL OR p_method NOT IN('ai_auto','human_agree','human_disagree','manual','referrer_auto','unknown')
  OR (p_engine IS NOT NULL AND p_engine NOT IN('external_agent','jev')) THEN
  RAISE EXCEPTION 'Invalid admission provenance' USING ERRCODE='22023';
 END IF;
 IF p_method='referrer_auto' AND NOT driftread.discovery_runtime_enabled() THEN RETURN NULL; END IF;
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
  gate_reason=CASE WHEN outcome='awaiting_human' THEN 'human_review_required'
   WHEN outcome='deferred' THEN 'policy_deferred' ELSE NULL END,
  error_code=CASE WHEN outcome='deferred' AND jsonb_array_length(r.snapshot->'samples')>0
   AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(r.snapshot->'samples') x
    WHERE NOT driftread.source_review_sample_is_demo(x)) THEN 'default_demo_content' ELSE NULL END,updated_at=now() WHERE id=r.id RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;

REVOKE ALL ON FUNCTION driftread.admit_source_review(uuid,text,text[],text,text,uuid),
 driftread.submit_source_review(uuid,text,uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.admit_source_review(uuid,text,text[],text,text,uuid),
 driftread.submit_source_review(uuid,text,uuid,text,jsonb) TO service_role;

-- Explicit, version-fenced reconciliation. Upgrade itself never releases old holds.
CREATE OR REPLACE FUNCTION driftread.reconcile_source_review(
 p_review_id uuid,p_policy_version integer,p_candidate_version integer,p_dry_run boolean DEFAULT true)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; c driftread.discovery_candidates; s driftread.app_settings;
 reason text; eligible boolean:=false;
BEGIN
 SELECT * INTO s FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 SELECT candidate_id INTO r.candidate_id FROM driftread.source_reviews WHERE id=p_review_id;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=r.candidate_id FOR UPDATE;
 SELECT * INTO r FROM driftread.source_reviews WHERE id=p_review_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 reason:=CASE
  WHEN r.gate_reason='reconciled_discovery_pause' THEN 'already_reconciled'
  WHEN r.status<>'awaiting_human' THEN 'not_awaiting_human'
  WHEN r.gate_reason IS NOT NULL THEN r.gate_reason
  WHEN r.policy->'skip_human_review'='false'::jsonb THEN 'human_review_required'
  WHEN r.policy->'skip_human_review'='true'::jsonb AND r.result->>'decision'='accept'
   AND r.updated_at >= (SELECT applied_at FROM driftread._migrations WHERE filename='20261018034910_discovery_control.sql')
   AND r.updated_at < (SELECT applied_at FROM driftread._migrations WHERE filename='20261020080000_review_independence.sql')
   THEN 'discovery_paused_legacy'
  ELSE 'legacy_unknown' END;
 IF reason='discovery_paused_legacy' THEN
  eligible:=s.version=p_policy_version AND s.version=r.policy_version
   AND c.review_version=p_candidate_version AND c.review_version=r.candidate_version
   AND coalesce(s.value->'enabled'='true'::jsonb,false)
   AND coalesce(s.value->'skip_human_review'='true'::jsonb,false)
   AND c.status='pending' AND c.manual_override IS NULL AND r.human_review IS NULL
   AND r.id=(SELECT id FROM driftread.source_reviews WHERE candidate_id=c.id ORDER BY created_at DESC,id DESC LIMIT 1)
   AND NOT driftread.source_review_candidate_blocked(c.id)
   AND r.snapshot_expires_at>now()
   AND EXISTS(SELECT 1 FROM driftread.analysis_results WHERE review_id=r.id AND output=r.result)
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(r.snapshot->'samples') x WHERE length(x->>'text')>=100 AND NOT driftread.source_review_sample_is_demo(x))
   AND (r.result->>'confidence')::numeric >= (s.value->>'min_confidence')::numeric
   AND (r.result->'dimensions'->>'quality')::numeric >= (s.value->>'min_quality')::numeric
   AND (r.result->'dimensions'->>'relevance')::numeric >= 0.5
   AND (r.result->'dimensions'->>'spam_probability')::numeric <= (s.value->>'max_spam_probability')::numeric;
 END IF;
 IF eligible AND p_dry_run IS FALSE THEN
  PERFORM set_config('driftread.review_internal','1',true);
  PERFORM driftread.admit_source_review(c.id,driftread.source_review_known_category(r.result->>'category'),
   driftread.source_review_known_tags(r.result->'tags'),'ai_auto',r.engine,r.id);
  PERFORM set_config('driftread.review_internal','',true);
  UPDATE driftread.source_reviews SET status='accepted',gate_reason='reconciled_discovery_pause',updated_at=now()
   WHERE id=r.id RETURNING * INTO r;
 END IF;
 RETURN jsonb_build_object('review_id',r.id,'candidate_id',c.id,'reason',reason,
  'eligible',coalesce(eligible,false),'dry_run',p_dry_run,'status',r.status,
  'policy_version',s.version,'candidate_version',c.review_version);
END $$;
REVOKE ALL ON FUNCTION driftread.reconcile_source_review(uuid,integer,integer,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.reconcile_source_review(uuid,integer,integer,boolean) TO service_role;
NOTIFY pgrst, 'reload schema';
