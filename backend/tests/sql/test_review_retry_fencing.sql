-- Retry uses policy -> candidate -> review lock ordering, matching the worker.
-- Independent-connection coverage additionally waits on the worker's candidate
-- lock and checks the latest ID only after its new task becomes visible.
DO $$
DECLARE cid uuid; rid uuid; retry jsonb; original jsonb; task_status text;
BEGIN
 INSERT INTO driftread.app_settings(key,value)
 VALUES('source.review','{"enabled":true,"engine":"external_agent"}')
 ON CONFLICT(key) DO UPDATE SET value=excluded.value;

 FOREACH task_status IN ARRAY ARRAY['requested','preparing','ready','leased','awaiting_human','accepted'] LOOP
  INSERT INTO driftread.discovery_candidates(feed_url)
  VALUES('https://' || gen_random_uuid() || '.example/feed') RETURNING id INTO cid;
  INSERT INTO driftread.source_reviews(candidate_id,status,engine,model,policy_version,policy,
   candidate_version,lease_token,snapshot_hash,snapshot)
  VALUES(cid,task_status,'external_agent','test',1,'{}',1,gen_random_uuid(),'original-hash','{"original":"samples"}')
  RETURNING id INTO rid;
  SELECT to_jsonb(r) INTO original FROM driftread.source_reviews r WHERE id=rid;
  IF driftread.retry_source_review(cid,rid) <> '{"error":"conflict"}'::jsonb THEN
   RAISE EXCEPTION 'retry must refuse nonterminal status %',task_status;
  END IF;
  IF (SELECT to_jsonb(r) FROM driftread.source_reviews r WHERE id=rid) IS DISTINCT FROM original
   OR (SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=cid)<>1 THEN
   RAISE EXCEPTION 'refused retry mutated active task %',task_status;
  END IF;
 END LOOP;

 FOREACH task_status IN ARRAY ARRAY['rejected','deferred','failed','stale','overridden'] LOOP
  INSERT INTO driftread.discovery_candidates(feed_url)
  VALUES('https://' || gen_random_uuid() || '.example/feed') RETURNING id INTO cid;
  INSERT INTO driftread.source_reviews(candidate_id,status,engine,model,policy_version,policy,candidate_version)
  VALUES(cid,task_status,'external_agent','test',1,'{}',1) RETURNING id INTO rid;
  retry := driftread.retry_source_review(cid,rid);
  IF retry->>'status'<>'requested' OR retry->>'id'=rid::text OR retry->>'paid_retry_key' IS NULL THEN
   RAISE EXCEPTION 'matching terminal review must create one explicit paid retry: %',retry;
  END IF;
  IF driftread.retry_source_review(cid,rid) <> '{"error":"conflict"}'::jsonb
   OR (SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=cid)<>2 THEN
   RAISE EXCEPTION 'replayed retry must not supersede the new requested task';
  END IF;
 END LOOP;

 IF has_function_privilege('anon','driftread.retry_source_review(uuid,uuid)','EXECUTE')
  OR has_function_privilege('authenticated','driftread.retry_source_review(uuid,uuid)','EXECUTE')
  OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname='driftread' AND p.proname='retry_source_review' AND p.prosecdef) THEN
  RAISE EXCEPTION 'retry RPC must be service-only security invoker';
 END IF;
END $$;
