DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM information_schema.columns
  WHERE table_schema='driftread' AND table_name='feeds' AND column_name='last_fetch_result') THEN
  RAISE EXCEPTION 'refresh diagnostics column missing';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='driftread' AND p.proname='source_review_queue_status' AND p.prosecdef) THEN
  RAISE EXCEPTION 'queue diagnostics must use invoker privileges';
 END IF;
 IF NOT (driftread.source_review_queue_status() ? 'latest_pending') THEN
  RAISE EXCEPTION 'queue diagnostics contract missing';
 END IF;
END $$;
