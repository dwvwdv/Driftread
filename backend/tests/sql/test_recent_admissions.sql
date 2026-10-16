DO $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='driftread' AND c.relname='feed_admissions' AND c.relrowsecurity) THEN
  RAISE EXCEPTION 'admission ledger must enforce RLS';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='driftread' AND tablename='feed_admissions') THEN
  RAISE EXCEPTION 'admission ledger has no public policies';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
  WHERE n.nspname='driftread' AND p.proname IN('admit_source_review','list_recent_feed_admissions') AND p.prosecdef) THEN
  RAISE EXCEPTION 'admission RPCs must be security invoker';
 END IF;
END $$;
