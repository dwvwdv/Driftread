DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='driftread' AND p.proname IN('judge_source_review','resolve_source_review') AND p.prosecdef) THEN
  RAISE EXCEPTION 'human decision functions must remain security invoker';
 END IF;
 IF has_function_privilege('anon','driftread.judge_source_review(uuid,text,text)','EXECUTE')
  OR has_function_privilege('authenticated','driftread.judge_source_review(uuid,text,text)','EXECUTE')
  OR NOT has_function_privilege('service_role','driftread.judge_source_review(uuid,text,text)','EXECUTE') THEN
  RAISE EXCEPTION 'judgment must be service-only';
 END IF;
 IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid='driftread.source_reviews'::regclass) THEN
  RAISE EXCEPTION 'source_reviews must retain RLS';
 END IF;
END $$;
