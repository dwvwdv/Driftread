-- The scalar repair RPC is private, invoker-scoped and never a public write API.
DO $$
BEGIN
 IF has_function_privilege('anon','driftread.reconcile_feed_article_count(uuid)','EXECUTE')
 OR has_function_privilege('authenticated','driftread.reconcile_feed_article_count(uuid)','EXECUTE')
 OR NOT has_function_privilege('service_role','driftread.reconcile_feed_article_count(uuid)','EXECUTE') THEN
  RAISE EXCEPTION 'unexpected article count RPC permissions';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='driftread' AND p.proname IN('reconcile_feed_article_count','ingest_article_batch')
  AND p.prosecdef) THEN
  RAISE EXCEPTION 'article count writes must not bypass caller permissions';
 END IF;
END $$;
