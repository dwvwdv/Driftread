-- Review tasks own packet identity; no preview is inserted into public articles.
ALTER TABLE driftread.discovery_candidates ADD COLUMN IF NOT EXISTS review_version bigint NOT NULL DEFAULT 1;
ALTER TABLE driftread.discovery_candidates ADD COLUMN IF NOT EXISTS manual_override text;
CREATE TABLE IF NOT EXISTS driftread.source_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), candidate_id uuid NOT NULL REFERENCES driftread.discovery_candidates(id) ON DELETE CASCADE,
 status text NOT NULL DEFAULT 'requested' CHECK(status IN('requested','preparing','ready','leased','awaiting_human','accepted','rejected','deferred','failed','stale','overridden')),
 engine text NOT NULL CHECK(engine IN('external_agent','jev')), model text NOT NULL,
 policy_version bigint NOT NULL, policy jsonb NOT NULL, candidate_version bigint NOT NULL,
 snapshot jsonb, snapshot_hash text, snapshot_expires_at timestamptz,
 reviewer_id text, lease_token uuid, lease_expires_at timestamptz,
 result jsonb, error_code text, attempts integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE driftread.source_reviews ADD COLUMN IF NOT EXISTS available_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE driftread.source_reviews ADD COLUMN IF NOT EXISTS failure_count integer NOT NULL DEFAULT 0;
ALTER TABLE driftread.source_reviews ADD COLUMN IF NOT EXISTS paid_retry_key uuid;
CREATE UNIQUE INDEX IF NOT EXISTS source_review_active_candidate ON driftread.source_reviews(candidate_id)
 WHERE status IN('requested','preparing','ready','leased','awaiting_human');
CREATE INDEX IF NOT EXISTS source_review_ready ON driftread.source_reviews(status,created_at);
CREATE TABLE IF NOT EXISTS driftread.analysis_results (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), capability text NOT NULL, subject_id uuid NOT NULL,
 review_id uuid REFERENCES driftread.source_reviews(id) ON DELETE CASCADE,
 engine text NOT NULL, model text NOT NULL, input_hash text NOT NULL, policy_version bigint NOT NULL,
 output jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(review_id)
);
ALTER TABLE driftread.source_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.analysis_results ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.source_reviews,driftread.analysis_results FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON driftread.source_reviews TO service_role;
REVOKE ALL ON driftread.analysis_results FROM service_role;
GRANT SELECT,INSERT,DELETE ON driftread.analysis_results TO service_role;
CREATE OR REPLACE FUNCTION driftread.source_review_candidate_fence() RETURNS trigger
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.status IS DISTINCT FROM OLD.status OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
 OR NEW.feed_url IS DISTINCT FROM OLD.feed_url OR NEW.title IS DISTINCT FROM OLD.title
 OR NEW.website_url IS DISTINCT FROM OLD.website_url THEN
  NEW.review_version:=OLD.review_version+1;
  IF current_setting('driftread.review_internal',true) IS DISTINCT FROM '1'
  AND (NEW.status IS DISTINCT FROM OLD.status OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at) THEN
   NEW.manual_override:=CASE WHEN NEW.status IN('approved','imported') THEN 'approve'
    WHEN NEW.status='rejected' THEN 'reject' WHEN NEW.status='held' THEN 'hold' ELSE OLD.manual_override END;
  END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS source_review_candidate_fence ON driftread.discovery_candidates;
CREATE TRIGGER source_review_candidate_fence BEFORE UPDATE ON driftread.discovery_candidates
 FOR EACH ROW EXECUTE FUNCTION driftread.source_review_candidate_fence();

CREATE OR REPLACE FUNCTION driftread.source_review_candidate_blocked(p_candidate_id uuid)
 RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT NOT EXISTS(SELECT 1 FROM driftread.discovery_candidates c WHERE c.id=p_candidate_id)
 OR EXISTS(SELECT 1 FROM driftread.discovery_candidates c
  WHERE c.id=p_candidate_id AND (
   EXISTS(SELECT 1 FROM driftread.discovery_targets t WHERE (t.id=c.target_id OR t.host=c.source_host) AND t.status IN('rejected','blocked'))
   OR EXISTS(SELECT 1 FROM driftread.discovery_candidates a WHERE a.id<>c.id AND a.status IN('held','rejected')
    AND (a.feed_url=c.feed_url OR (c.feed_url ~* '/(feed|rss|atom)/?$' AND a.feed_url IN(rtrim(c.feed_url,'/'),rtrim(c.feed_url,'/')||'/'))))
  ));
$$;
REVOKE ALL ON FUNCTION driftread.source_review_candidate_blocked(uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.source_review_candidate_blocked(uuid) TO service_role;

CREATE OR REPLACE FUNCTION driftread.request_source_review(p_candidate_id uuid,p_retry boolean DEFAULT false)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates; s driftread.app_settings; r driftread.source_reviews;
BEGIN
 SELECT * INTO s FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 IF NOT FOUND OR NOT coalesce((s.value->>'enabled')::boolean,false) THEN RETURN jsonb_build_object('error','disabled'); END IF;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=p_candidate_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 IF c.manual_override IS NOT NULL OR c.status<>'pending' OR driftread.source_review_candidate_blocked(c.id) THEN RETURN jsonb_build_object('error','manual_decision'); END IF;
 SELECT * INTO r FROM driftread.source_reviews WHERE candidate_id=c.id ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE;
 IF FOUND THEN
  IF r.status IN('requested','preparing','ready','leased','awaiting_human') THEN
   IF NOT p_retry THEN RETURN to_jsonb(r); END IF;
   UPDATE driftread.source_reviews SET status='stale',updated_at=now() WHERE id=r.id;
  ELSIF NOT p_retry THEN RETURN to_jsonb(r);
  END IF;
 END IF;
 INSERT INTO driftread.source_reviews(candidate_id,engine,model,policy_version,policy,candidate_version,paid_retry_key)
 VALUES(c.id,s.value->>'engine',CASE WHEN s.value->>'engine'='external_agent' THEN 'external-agent' ELSE s.value->>'model' END,s.version,s.value,c.review_version,
  CASE WHEN p_retry THEN gen_random_uuid() ELSE NULL END) RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;

CREATE OR REPLACE FUNCTION driftread.claim_source_review(p_reviewer_id text,p_preparing boolean DEFAULT false,p_review_id uuid DEFAULT NULL)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; c driftread.discovery_candidates; s driftread.app_settings; target_status text; n integer;
BEGIN
 SELECT * INTO s FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 IF NOT FOUND OR NOT coalesce((s.value->>'enabled')::boolean,false) THEN RETURN NULL; END IF;
 IF p_reviewer_id NOT IN('external_agent','jev','preparer') OR (p_preparing AND p_reviewer_id<>'preparer') THEN RAISE EXCEPTION 'invalid reviewer'; END IF;
 target_status:=CASE WHEN p_preparing THEN 'preparing' ELSE 'leased' END;
 FOR n IN 1..100 LOOP
  -- Lock candidate before task, matching submit/prepare/manual admission.
  SELECT dc.* INTO c FROM driftread.discovery_candidates dc JOIN driftread.source_reviews sr ON sr.candidate_id=dc.id
   WHERE (p_review_id IS NULL OR sr.id=p_review_id) AND sr.available_at<=now()
   AND ((p_preparing AND (sr.status='requested' OR (sr.status='preparing' AND sr.lease_expires_at<now())))
    OR (NOT p_preparing AND sr.engine=p_reviewer_id AND (sr.status='ready' OR (sr.status='leased' AND sr.lease_expires_at<now()))))
   ORDER BY sr.created_at,sr.id LIMIT 1 FOR UPDATE OF dc SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO r FROM driftread.source_reviews WHERE candidate_id=c.id
   AND (p_review_id IS NULL OR id=p_review_id) AND status IN('requested','preparing','ready','leased')
   ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE;
  IF c.review_version<>r.candidate_version OR c.manual_override IS NOT NULL OR c.status<>'pending'
  OR driftread.source_review_candidate_blocked(c.id) THEN
   UPDATE driftread.source_reviews SET status='overridden',updated_at=now() WHERE id=r.id; CONTINUE;
  END IF;
  IF r.policy_version<>s.version OR (NOT p_preparing AND (r.snapshot IS NULL OR r.snapshot_expires_at<=now())) THEN
   UPDATE driftread.source_reviews SET status='stale',updated_at=now() WHERE id=r.id; CONTINUE;
  END IF;
  IF r.attempts>=3 THEN
   UPDATE driftread.source_reviews SET status='failed',error_code='lease_exhausted',updated_at=now() WHERE id=r.id; CONTINUE;
  END IF;
  UPDATE driftread.source_reviews SET status=target_status,reviewer_id=p_reviewer_id,
   lease_token=gen_random_uuid(),lease_expires_at=now()+interval '10 minutes',attempts=attempts+1,updated_at=now()
   WHERE id=r.id RETURNING * INTO r;
  RETURN to_jsonb(r);
 END LOOP;
 RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION driftread.prepare_source_review(p_review_id uuid,p_token uuid,p_snapshot jsonb,p_hash text,p_error text DEFAULT NULL)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; c driftread.discovery_candidates; s driftread.app_settings;
BEGIN
 -- Consistent lock order settings -> candidate -> task across all mutations.
 SELECT * INTO s FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 SELECT candidate_id INTO r.candidate_id FROM driftread.source_reviews WHERE id=p_review_id;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=r.candidate_id FOR UPDATE;
 SELECT * INTO r FROM driftread.source_reviews WHERE id=p_review_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 IF r.status<>'preparing' OR r.lease_token IS DISTINCT FROM p_token OR r.lease_expires_at<=now() THEN RETURN jsonb_build_object('error','lease'); END IF;
 IF c.review_version<>r.candidate_version OR c.manual_override IS NOT NULL OR c.status<>'pending' OR driftread.source_review_candidate_blocked(c.id) THEN
  UPDATE driftread.source_reviews SET status='overridden',updated_at=now() WHERE id=r.id RETURNING * INTO r;
 ELSIF s.version IS DISTINCT FROM r.policy_version OR NOT coalesce((s.value->>'enabled')::boolean,false) THEN
  UPDATE driftread.source_reviews SET status='stale',updated_at=now() WHERE id=r.id RETURNING * INTO r;
 ELSIF p_error IS NOT NULL THEN
  UPDATE driftread.source_reviews SET status=CASE WHEN failure_count+1<3 THEN 'requested' ELSE 'failed' END,
   failure_count=failure_count+1,error_code=left(p_error,100),available_at=now()+make_interval(secs=>300*(failure_count+1)),
   lease_token=NULL,lease_expires_at=NULL,reviewer_id=NULL,updated_at=now() WHERE id=r.id RETURNING * INTO r;
 ELSE
  IF jsonb_typeof(p_snapshot) IS DISTINCT FROM 'object' OR octet_length(p_snapshot::text)>100000
  OR p_hash !~ '^[a-f0-9]{64}$' OR jsonb_typeof(p_snapshot->'samples') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_snapshot->'samples')>10 THEN RAISE EXCEPTION 'invalid snapshot'; END IF;
  UPDATE driftread.source_reviews SET status='ready',snapshot=p_snapshot,snapshot_hash=p_hash,
   snapshot_expires_at=now()+interval '30 days',reviewer_id=NULL,lease_token=NULL,lease_expires_at=NULL,attempts=0,updated_at=now()
   WHERE id=r.id RETURNING * INTO r;
 END IF;
 RETURN to_jsonb(r);
END $$;

-- Atomic catalog write used by both machine and human resolutions.
CREATE OR REPLACE FUNCTION driftread.admit_source_review(p_candidate_id uuid,p_category text,p_tags text[])
 RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates; f uuid; aliases text[];
BEGIN
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
  IF f IS NULL THEN SELECT id INTO f FROM driftread.feeds WHERE url=c.feed_url; END IF;
 END IF;
 UPDATE driftread.discovery_candidates SET status='imported',feed_id=f,reviewed_at=now() WHERE id=c.id;
 RETURN f;
END $$;

CREATE OR REPLACE FUNCTION driftread.submit_source_review(p_review_id uuid,p_reviewer_id text,p_token uuid,p_hash text,p_result jsonb)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; c driftread.discovery_candidates; s driftread.app_settings; outcome text; tag_array text[];
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
 IF outcome='accepted' AND (NOT EXISTS(SELECT 1 FROM jsonb_array_elements(r.snapshot->'samples') x WHERE length(x->>'text')>=100)
 OR (p_result->>'confidence')::numeric < (r.policy->>'min_confidence')::numeric
 OR (p_result->'dimensions'->>'quality')::numeric < (r.policy->>'min_quality')::numeric
 OR (p_result->'dimensions'->>'relevance')::numeric < 0.5
 OR (p_result->'dimensions'->>'spam_probability')::numeric > (r.policy->>'max_spam_probability')::numeric) THEN outcome:='deferred'; END IF;
 IF outcome IN('accepted','rejected') AND NOT coalesce((s.value->>'skip_human_review')::boolean,false) THEN outcome:='awaiting_human'; END IF;
 PERFORM set_config('driftread.review_internal','1',true);
 IF outcome='accepted' THEN
  SELECT coalesce(array_agg(value),'{}') INTO tag_array FROM jsonb_array_elements_text(coalesce(p_result->'tags','[]'));
  PERFORM driftread.admit_source_review(c.id,p_result->>'category',tag_array);
 END IF;
 PERFORM set_config('driftread.review_internal','',true);
 -- Model rejects remain reviewable pending candidates; never block a host.
 INSERT INTO driftread.analysis_results(capability,subject_id,review_id,engine,model,input_hash,policy_version,output)
 VALUES('source_review',c.id,r.id,r.engine,r.model,p_hash,r.policy_version,p_result);
 UPDATE driftread.source_reviews SET status=outcome,result=p_result,updated_at=now() WHERE id=r.id RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;

CREATE OR REPLACE FUNCTION driftread.resolve_source_review(p_review_id uuid,p_decision text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; c driftread.discovery_candidates; tag_array text[];
BEGIN
 IF p_decision NOT IN('accept','reject','defer') THEN RAISE EXCEPTION 'invalid decision'; END IF;
 SELECT candidate_id INTO r.candidate_id FROM driftread.source_reviews WHERE id=p_review_id;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=r.candidate_id FOR UPDATE;
 SELECT * INTO r FROM driftread.source_reviews WHERE id=p_review_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 IF c.manual_override IS NOT NULL OR c.status<>'pending' OR driftread.source_review_candidate_blocked(c.id) THEN RETURN jsonb_build_object('error','manual_decision'); END IF;
 IF p_decision='accept' THEN
  SELECT coalesce(array_agg(value),'{}') INTO tag_array FROM jsonb_array_elements_text(coalesce(r.result->'tags','[]'));
  PERFORM driftread.admit_source_review(c.id,r.result->>'category',tag_array);
 ELSIF p_decision='reject' THEN
  UPDATE driftread.discovery_candidates SET status='rejected',reviewed_at=now() WHERE id=c.id;
 ELSE
  UPDATE driftread.discovery_candidates SET status='held',reviewed_at=now() WHERE id=c.id;
 END IF;
 UPDATE driftread.source_reviews SET status=CASE p_decision WHEN 'accept' THEN 'accepted' WHEN 'reject' THEN 'rejected' ELSE 'deferred' END,
 updated_at=now() WHERE id=r.id RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;

CREATE OR REPLACE FUNCTION driftread.prune_source_reviews() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE changed integer; removed integer;
BEGIN
 UPDATE driftread.source_reviews SET status='stale',updated_at=now() WHERE id IN(
  SELECT r.id FROM driftread.source_reviews r WHERE r.status IN('requested','preparing','ready','leased')
  AND (r.snapshot_expires_at<=now() OR r.policy_version IS DISTINCT FROM
   (SELECT version FROM driftread.app_settings WHERE key='source.review')) LIMIT 100);
 UPDATE driftread.source_reviews SET snapshot=NULL WHERE id IN(
  SELECT id FROM driftread.source_reviews WHERE snapshot_expires_at<=now() AND snapshot IS NOT NULL ORDER BY snapshot_expires_at LIMIT 100);
 GET DIAGNOSTICS changed=ROW_COUNT;
 DELETE FROM driftread.source_reviews WHERE id IN(
  SELECT id FROM (SELECT id,status,created_at,row_number() OVER(PARTITION BY candidate_id ORDER BY created_at DESC,id DESC) n
   FROM driftread.source_reviews) x WHERE n>10 AND created_at<now()-interval '30 days'
  AND status NOT IN('requested','preparing','ready','leased','awaiting_human') LIMIT 100);
 GET DIAGNOSTICS removed=ROW_COUNT;
 RETURN changed+removed;
END $$;

CREATE OR REPLACE FUNCTION driftread.request_due_source_reviews(p_limit integer DEFAULT 10)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE cid uuid; n integer:=0; result jsonb; s driftread.app_settings;
BEGIN
 SELECT * INTO s FROM driftread.app_settings WHERE key='source.review' FOR SHARE;
 IF NOT FOUND OR NOT coalesce((s.value->>'enabled')::boolean,false) THEN RETURN jsonb_build_object('requested',0); END IF;
 FOR cid IN SELECT c.id FROM driftread.discovery_candidates c
 LEFT JOIN LATERAL (SELECT r.status FROM driftread.source_reviews r WHERE r.candidate_id=c.id
  ORDER BY r.created_at DESC,r.id DESC LIMIT 1) latest ON true
 WHERE c.status='pending' AND c.manual_override IS NULL AND NOT driftread.source_review_candidate_blocked(c.id)
 AND (latest.status IS NULL OR latest.status='stale')
 ORDER BY c.discovered_at,c.id LIMIT greatest(0,least(p_limit,10)) FOR UPDATE OF c SKIP LOCKED LOOP
  result:=driftread.request_source_review(cid,true);
  IF result->>'error' IS NULL THEN n:=n+1; END IF;
 END LOOP;
 RETURN jsonb_build_object('requested',n);
END $$;

CREATE OR REPLACE FUNCTION driftread.fail_source_review(p_review_id uuid,p_token uuid,p_error text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r driftread.source_reviews; retryable boolean;
BEGIN
 SELECT * INTO r FROM driftread.source_reviews WHERE id=p_review_id FOR UPDATE;
 IF NOT FOUND OR r.status<>'leased' OR r.lease_token IS DISTINCT FROM p_token OR r.lease_expires_at<=now() THEN RETURN NULL; END IF;
 retryable:=p_error IN('provider_failed','provider_http_retryable','provider_outcome_unknown','request_in_progress','request_fenced','provider_unavailable');
 UPDATE driftread.source_reviews SET
  status=CASE WHEN p_error='budget_exceeded' OR (retryable AND failure_count+1<3) THEN 'ready' ELSE 'failed' END,
  available_at=CASE WHEN p_error='budget_exceeded' THEN
   now()+interval '5 minutes'
   ELSE now()+make_interval(secs=>300*(failure_count+1)) END,
  failure_count=failure_count+CASE WHEN p_error='budget_exceeded' THEN 0 ELSE 1 END,
  attempts=CASE WHEN p_error='budget_exceeded' THEN 0 ELSE attempts END,
  error_code=left(p_error,100),lease_token=NULL,lease_expires_at=NULL,reviewer_id=NULL,updated_at=now()
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
 f:=driftread.admit_source_review(c.id,coalesce(p_category,c.approved_category),coalesce(p_tags,c.approved_tags));
 PERFORM set_config('driftread.review_internal','',true);
 SELECT to_jsonb(x) INTO feed FROM driftread.feeds x WHERE id=f;
 RETURN feed;
END $$;
REVOKE ALL ON FUNCTION driftread.request_due_source_reviews(integer),driftread.fail_source_review(uuid,uuid,text),
 driftread.promote_discovery_candidate(uuid,text,text[],boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.request_due_source_reviews(integer),driftread.fail_source_review(uuid,uuid,text),
 driftread.promote_discovery_candidate(uuid,text,text[],boolean) TO service_role;

-- A manual candidate action cannot silently undo a completed admission. If
-- admission committed first, source visibility must be managed from feeds.
CREATE OR REPLACE FUNCTION driftread.decide_discovery_candidate(
 p_candidate_id uuid,p_decision text,p_expected_version bigint,p_note text DEFAULT NULL,p_block_host boolean DEFAULT false)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE c driftread.discovery_candidates; source_host text; aliases text[];
BEGIN
 IF p_decision NOT IN('held','rejected') OR p_expected_version IS NULL OR p_expected_version<1 THEN
  RAISE EXCEPTION 'invalid candidate decision';
 END IF;
 SELECT * INTO c FROM driftread.discovery_candidates WHERE id=p_candidate_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
 aliases:=ARRAY[c.feed_url];
 IF c.feed_url ~* '/(feed|rss|atom)/?$' THEN aliases:=ARRAY[rtrim(c.feed_url,'/'),rtrim(c.feed_url,'/')||'/']; END IF;
 IF c.status='imported' OR c.feed_id IS NOT NULL OR EXISTS(SELECT 1 FROM driftread.feeds f WHERE f.url=ANY(aliases)) THEN
  RETURN jsonb_build_object('error','candidate_already_imported');
 END IF;
 IF c.review_version<>p_expected_version THEN RETURN jsonb_build_object('error','candidate_changed'); END IF;
 -- This is a human action: keep the override trigger enabled.
 UPDATE driftread.discovery_candidates SET status=p_decision,review_note=left(p_note,500),reviewed_at=clock_timestamp()
  WHERE id=c.id RETURNING * INTO c;
 IF p_decision='rejected' AND p_block_host THEN
  source_host:=c.source_host;
  IF source_host IS NULL AND c.target_id IS NOT NULL THEN
   SELECT host INTO source_host FROM driftread.discovery_targets WHERE id=c.target_id;
  END IF;
  IF source_host IS NOT NULL THEN UPDATE driftread.discovery_targets SET status='rejected' WHERE host=source_host; END IF;
 END IF;
 RETURN to_jsonb(c);
END $$;
REVOKE ALL ON FUNCTION driftread.decide_discovery_candidate(uuid,text,bigint,text,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.decide_discovery_candidate(uuid,text,bigint,text,boolean) TO service_role;

-- Explicit zero-policy service-only execution; no SECURITY DEFINER application RPC.
REVOKE ALL ON FUNCTION driftread.source_review_candidate_fence(),driftread.request_source_review(uuid,boolean),
 driftread.claim_source_review(text,boolean,uuid),driftread.prepare_source_review(uuid,uuid,jsonb,text,text),
 driftread.admit_source_review(uuid,text,text[]),driftread.submit_source_review(uuid,text,uuid,text,jsonb),
 driftread.resolve_source_review(uuid,text),driftread.prune_source_reviews() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.source_review_candidate_fence(),driftread.request_source_review(uuid,boolean),
 driftread.claim_source_review(text,boolean,uuid),driftread.prepare_source_review(uuid,uuid,jsonb,text,text),
 driftread.admit_source_review(uuid,text,text[]),driftread.submit_source_review(uuid,text,uuid,text,jsonb),
 driftread.resolve_source_review(uuid,text),driftread.prune_source_reviews() TO service_role;
NOTIFY pgrst,'reload schema';
