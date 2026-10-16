-- Bounded, safe diagnostics for the latest refresh; never store response bodies
-- or raw exceptions here. Historical fetches remain unknown until refreshed.
ALTER TABLE driftread.feeds ADD COLUMN IF NOT EXISTS last_fetch_result jsonb;

-- The ordinary enqueue is idempotent, but an obsolete, unfinished task must
-- not prevent a caller from obtaining work under the current policy. Recovery
-- never mints a paid retry key. Explicit browser retries retain their contract.
CREATE OR REPLACE FUNCTION driftread.request_source_review(
 p_candidate_id uuid,p_retry boolean DEFAULT false,p_paid_retry boolean DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates; s driftread.app_settings; r driftread.source_reviews;
 recovery boolean:=false; had_review boolean:=false;
BEGIN
 SELECT * INTO s FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 IF NOT FOUND OR NOT coalesce((s.value->>'enabled')::boolean,false) THEN
  RETURN jsonb_build_object('error','disabled');
 END IF;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=p_candidate_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 IF c.manual_override IS NOT NULL OR c.status<>'pending' OR driftread.source_review_candidate_blocked(c.id) THEN
  RETURN jsonb_build_object('error','manual_decision');
 END IF;
 SELECT * INTO r FROM driftread.source_reviews WHERE candidate_id=c.id
  ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE;
 had_review:=FOUND;
 IF had_review THEN
  recovery:=r.status='stale'
   OR (r.status IN('requested','preparing','ready','leased') AND r.policy_version<>s.version)
   OR (r.status='failed' AND r.error_code='provider_key_missing'
       AND r.engine<>s.value->>'engine' AND s.value->>'engine'='external_agent');
  IF NOT p_retry AND NOT recovery THEN
   RETURN to_jsonb(r)||jsonb_build_object('request_action','reused');
  END IF;
  IF r.status IN('requested','preparing','ready','leased','awaiting_human') THEN
   UPDATE driftread.source_reviews SET status='stale',updated_at=now() WHERE id=r.id;
  END IF;
 END IF;
 INSERT INTO driftread.source_reviews(candidate_id,engine,model,policy_version,policy,candidate_version,paid_retry_key)
 VALUES(c.id,s.value->>'engine',
  CASE WHEN s.value->>'engine'='external_agent' THEN 'external-agent' ELSE s.value->>'model' END,
  s.version,s.value,c.review_version,
  CASE WHEN coalesce(p_paid_retry,p_retry) THEN gen_random_uuid() ELSE NULL END) RETURNING * INTO r;
 RETURN to_jsonb(r)||jsonb_build_object('request_action',CASE WHEN had_review THEN 'requeued' ELSE 'enqueued' END);
END $$;
REVOKE ALL ON FUNCTION driftread.request_source_review(uuid,boolean,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.request_source_review(uuid,boolean,boolean) TO service_role;

-- Distinguish current pending work from historical attempts; status totals over
-- the entire audit table cannot measure the amount of work left to do.
CREATE OR REPLACE FUNCTION driftread.source_review_queue_status()
 RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 WITH latest AS (
  SELECT DISTINCT ON(r.candidate_id) r.candidate_id,r.status,r.engine,r.policy_version,r.error_code
  FROM driftread.source_reviews r
  JOIN driftread.discovery_candidates c ON c.id=r.candidate_id
  WHERE c.status='pending' AND c.manual_override IS NULL
  ORDER BY r.candidate_id,r.created_at DESC,r.id DESC
 ), totals AS (SELECT status,count(*) n FROM latest GROUP BY status)
 SELECT jsonb_build_object(
  'observed_at',now(),
  'latest_pending',coalesce((SELECT jsonb_object_agg(status,n) FROM totals),'{}'::jsonb),
  'outdated_active',(SELECT count(*) FROM latest WHERE status IN('requested','preparing','ready','leased')
   AND policy_version IS DISTINCT FROM (SELECT version FROM driftread.app_settings WHERE key='source.review')),
  'provider_key_missing',(SELECT count(*) FROM latest WHERE status='failed' AND error_code='provider_key_missing')
 )
$$;
REVOKE ALL ON FUNCTION driftread.source_review_queue_status() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.source_review_queue_status() TO service_role;

-- A complete WordPress starter sample is evidence, not useful article content.
-- Recheck the signature at submit time as well: packets prepared before this
-- deployment have no default_demo hint. The hint is never an authority.
CREATE OR REPLACE FUNCTION driftread.source_review_sample_is_demo(p_sample jsonb)
 RETURNS boolean LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT lower(rtrim(btrim(coalesce(p_sample->>'title','')),'.!！'))='hello world'
 AND lower(regexp_replace(btrim(coalesce(p_sample->>'text','')),'\s+',' ','g')) IN (
  'welcome to wordpress. this is your first post. edit or delete it, then start writing!',
  'welcome to wordpress! this is your first post. edit or delete it to take the first step in your blogging journey.'
 )
$$;
REVOKE ALL ON FUNCTION driftread.source_review_sample_is_demo(jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.source_review_sample_is_demo(jsonb) TO service_role;

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
   driftread.source_review_known_tags(p_result->'tags'));
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

NOTIFY pgrst, 'reload schema';
