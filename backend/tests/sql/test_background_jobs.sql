BEGIN;
SET LOCAL ROLE service_role;
DO $$
DECLARE v_id uuid; v_worker uuid:=gen_random_uuid(); v_job driftread.background_jobs; v_next uuid;
BEGIN
 v_id:=driftread.enqueue_background_job('refresh','{}','sql-fixture',now(),2,60);
 IF driftread.enqueue_background_job('refresh','{}','sql-fixture')<>v_id THEN RAISE EXCEPTION 'singleton duplicated'; END IF;
 SELECT * INTO v_job FROM driftread.claim_background_job('refresh',v_worker);
 IF v_job.id<>v_id OR v_job.attempts<>1 THEN RAISE EXCEPTION 'claim failed'; END IF;
 IF NOT driftread.renew_background_job(v_id,v_job.lease_token) THEN RAISE EXCEPTION 'renew failed'; END IF;
 IF driftread.finish_background_job(v_id,gen_random_uuid(),true) THEN RAISE EXCEPTION 'foreign ack accepted'; END IF;
 IF NOT driftread.finish_background_job(v_id,v_job.lease_token,true) THEN RAISE EXCEPTION 'ack failed'; END IF;
 SELECT id INTO v_next FROM driftread.background_jobs WHERE singleton_key='sql-fixture' AND status='queued';
 IF v_next IS NULL OR v_next=v_id THEN RAISE EXCEPTION 'next periodic occurrence missing'; END IF;
 IF EXISTS(SELECT 1 FROM driftread.claim_background_job('refresh',v_worker)) THEN RAISE EXCEPTION 'future job claimed early'; END IF;
END $$;
RESET ROLE;
DO $$
BEGIN
 IF has_table_privilege('anon','driftread.background_jobs','SELECT') OR
    has_table_privilege('authenticated','driftread.background_jobs','UPDATE') OR
    has_function_privilege('anon','driftread.claim_background_job(text,uuid,integer)','EXECUTE') OR
    has_function_privilege('authenticated','driftread.watchdog_worker_operations()','EXECUTE') THEN
  RAISE EXCEPTION 'private queue/monitoring exposed';
 END IF;
END $$;
ROLLBACK;
