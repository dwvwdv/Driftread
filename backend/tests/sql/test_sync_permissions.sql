-- Run inside the isolated integration database. The trigger privilege exception
-- is limited to ledger writes; callers cannot invoke it or the snapshot RPC.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='driftread.record_sync_change()'::regprocedure
               AND prosecdef AND proconfig=ARRAY['search_path=pg_catalog']) THEN
  RAISE EXCEPTION 'sync trigger must pin definer search_path';
 END IF;
 IF has_function_privilege('authenticated','driftread.record_sync_change()','EXECUTE')
 OR has_function_privilege('anon','driftread.record_sync_change()','EXECUTE')
 OR has_function_privilege('authenticated','driftread.personal_sync_snapshot(uuid,bigint,uuid[])','EXECUTE')
 OR has_function_privilege('anon','driftread.personal_publication_search(uuid,text,int)','EXECUTE') THEN
  RAISE EXCEPTION 'private sync functions exposed';
 END IF;
 IF has_table_privilege('authenticated','driftread.sync_changes','SELECT')
 OR has_table_privilege('anon','driftread.sync_clock','UPDATE') THEN
  RAISE EXCEPTION 'private sync ledger exposed';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid IN('driftread.sync_clock'::regclass,'driftread.sync_changes'::regclass)
           AND NOT relrowsecurity) THEN
  RAISE EXCEPTION 'sync ledger missing RLS';
 END IF;
END $$;
