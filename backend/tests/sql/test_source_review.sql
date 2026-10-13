DO $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='driftread' AND c.relname='source_reviews' AND c.relrowsecurity) THEN
  RAISE EXCEPTION 'source review table must enforce RLS';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='driftread' AND tablename IN('source_reviews','analysis_results')) THEN
  RAISE EXCEPTION 'private analysis has no public policies';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
  WHERE n.nspname='driftread' AND p.proname IN('request_source_review','submit_source_review','admit_source_review','resolve_source_review') AND p.prosecdef) THEN
  RAISE EXCEPTION 'review RPCs must be security invoker';
 END IF;
 IF has_table_privilege('service_role','driftread.analysis_results','UPDATE') THEN
  RAISE EXCEPTION 'analysis results are append-only';
 END IF;
END $$;
