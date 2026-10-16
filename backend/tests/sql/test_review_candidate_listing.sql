-- Executed as service_role in an isolated integration database; rolls back.
DO $$
DECLARE ids uuid[]; packet jsonb; i integer; cid uuid; setting_version bigint;
BEGIN
 INSERT INTO driftread.app_settings(key,value) VALUES('source.review','{"enabled":true}')
 ON CONFLICT(key) DO UPDATE SET value=driftread.app_settings.value || '{"enabled":true}'::jsonb
 RETURNING version INTO setting_version;
 FOR i IN 1..10 LOOP
  INSERT INTO driftread.discovery_candidates(feed_url,title,status,manual_override,referring_feed_count)
  VALUES('https://' || gen_random_uuid() || '.example/feed','candidate',
   CASE WHEN i=4 THEN 'held' ELSE 'pending' END,
   CASE WHEN i=5 THEN 'hold' ELSE NULL END,10000-i) RETURNING id INTO cid;
  ids := array_append(ids,cid);
 END LOOP;
 INSERT INTO driftread.source_reviews(id,candidate_id,status,engine,model,policy_version,policy,
  candidate_version,snapshot,snapshot_hash,snapshot_expires_at,lease_token,result,created_at)
 SELECT ('00000000-0000-0000-0000-' || lpad(n::text,12,'0'))::uuid,
  ids[CASE WHEN n IN(1,2) THEN 2 WHEN n IN(3,4) THEN 3 ELSE n-1 END],
  CASE WHEN n IN(1,4) THEN 'rejected' WHEN n>=8 THEN 'ready' ELSE 'awaiting_human' END,
  CASE WHEN n=10 THEN 'jev' ELSE 'external_agent' END,'test',setting_version,'{"private":"policy"}',
  CASE WHEN n=11 THEN 0 ELSE 1 END,'{"private":"snapshot"}','hash',
  CASE WHEN n=9 THEN now()-interval '1 day' ELSE now()+interval '1 day' END,
  gen_random_uuid(),'{"decision":"accept"}',now()
 FROM generate_series(1,11) n;

 packet := driftread.list_review_candidates('awaiting_human',1,0,9900);
 IF packet->>'total'<>'2' OR jsonb_array_length(packet->'items')<>1
  OR packet#>>'{items,0,id}'<>ids[2]::text THEN
  RAISE EXCEPTION 'latest-review filter must run before count/pagination: %',packet;
 END IF;
 IF packet#>>'{items,0,latest_review,id}'<>'00000000-0000-0000-0000-000000000002' THEN
  RAISE EXCEPTION 'same-created_at reviews must use descending id';
 END IF;
 IF (packet#>'{items,0,latest_review}') ?| ARRAY['snapshot','snapshot_hash','policy','lease_token','lease_expires_at'] THEN
  RAISE EXCEPTION 'candidate listing leaked private review fields';
 END IF;
 packet := driftread.list_review_candidates('awaiting_human',1,1,9900);
 IF packet->>'total'<>'2' OR packet#>>'{items,0,id}'<>ids[6]::text THEN
  RAISE EXCEPTION 'awaiting_human second page or count is incorrect: %',packet;
 END IF;
 packet := driftread.list_review_candidates('awaiting_human',20,20,9900);
 IF packet->>'total'<>'2' OR packet->'items'<>'[]'::jsonb THEN
  RAISE EXCEPTION 'empty pages must retain the exact total';
 END IF;
 packet := driftread.list_review_candidates('awaiting_human',20,0,9995);
 IF packet->>'total'<>'1' THEN RAISE EXCEPTION 'min_referrers must affect count and rows'; END IF;
 packet := driftread.list_review_candidates('pending',20,0,9900);
 IF packet->>'total'<>'8' OR packet#>>'{items,0,id}'<>ids[1]::text
  OR packet#>'{items,0,latest_review}'<>'null'::jsonb THEN
  RAISE EXCEPTION 'pending must include unreviewed and exclude held/overridden candidates: %',packet;
 END IF;
 packet := driftread.list_review_candidates('ready',20,0,9900);
 IF packet->>'total'<>'1' OR packet#>>'{items,0,id}'<>ids[7]::text THEN
  RAISE EXCEPTION 'ready must exclude expired, Jev, and outdated candidate versions: %',packet;
 END IF;
 UPDATE driftread.source_reviews SET status='leased',lease_expires_at=now()+interval '1 minute'
 WHERE candidate_id=ids[7];
 IF driftread.list_review_candidates('ready',20,0,9900)->>'total'<>'0' THEN
  RAISE EXCEPTION 'ready must exclude another reviewer current lease';
 END IF;
 UPDATE driftread.source_reviews SET lease_expires_at=now()-interval '1 minute'
 WHERE candidate_id=ids[7];
 IF driftread.list_review_candidates('ready',20,0,9900)->>'total'<>'1' THEN
  RAISE EXCEPTION 'ready must include an expired lease that can be reclaimed';
 END IF;
 UPDATE driftread.source_reviews SET policy_version=0 WHERE candidate_id=ids[7];
 IF driftread.list_review_candidates('ready',20,0,9900)->>'total'<>'0' THEN
  RAISE EXCEPTION 'ready must exclude outdated policy versions';
 END IF;
 UPDATE driftread.source_reviews SET policy_version=setting_version,attempts=3 WHERE candidate_id=ids[7];
 IF driftread.list_review_candidates('ready',20,0,9900)->>'total'<>'0' THEN
  RAISE EXCEPTION 'ready must exclude exhausted leases';
 END IF;
 UPDATE driftread.source_reviews SET attempts=0 WHERE candidate_id=ids[7];
 UPDATE driftread.app_settings SET value=value || '{"enabled":false}'::jsonb WHERE key='source.review';
 IF driftread.list_review_candidates('ready',20,0,9900)->>'total'<>'0' THEN
  RAISE EXCEPTION 'ready must honor the review enabled setting';
 END IF;
END $$;

DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='driftread' AND p.proname='list_review_candidates' AND p.prosecdef) THEN
  RAISE EXCEPTION 'review listing must be security invoker';
 END IF;
 IF has_function_privilege('anon','driftread.list_review_candidates(text,integer,integer,integer)','EXECUTE')
  OR has_function_privilege('authenticated','driftread.list_review_candidates(text,integer,integer,integer)','EXECUTE') THEN
  RAISE EXCEPTION 'review listing must not be publicly executable';
 END IF;
END $$;
