DO $$
BEGIN
 ASSERT EXISTS(SELECT 1 FROM information_schema.columns
  WHERE table_schema='driftread' AND table_name='source_reviews' AND column_name='gate_reason');
 ASSERT NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='driftread' AND p.proname IN('reconcile_source_review','submit_source_review','admit_source_review')
  AND p.prosecdef);
 ASSERT NOT driftread.discovery_runtime_enabled();
END $$;
