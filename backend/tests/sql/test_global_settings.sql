-- Isolated database only, after global settings migration. Rolls back fixtures.
BEGIN;
DO $$
DECLARE initial_value jsonb; initial_seeds jsonb; saved jsonb;
BEGIN
 SELECT value INTO initial_value FROM driftread.app_settings WHERE key='discovery.profiles';
 SELECT jsonb_agg(jsonb_build_object('url', seed_url, 'host',
        regexp_replace(split_part(seed_url, '/', 3), '^www\.', '')))
 INTO initial_seeds
 FROM jsonb_array_elements(initial_value->'profiles') AS profile,
      jsonb_array_elements_text(profile->'seed_urls') AS seed_url
 WHERE (profile->>'enabled')::boolean;
 saved := driftread.save_app_setting('discovery.profiles', initial_value, 1, initial_seeds);
 IF (saved->>'version')::bigint <> 2 OR saved->'value' <> initial_value THEN
  RAISE EXCEPTION 'explicit default apply did not preserve settings';
 END IF;
 IF (SELECT count(*) FROM driftread.discovery_targets WHERE source='seed' AND status='pending') <> 9 THEN
  RAISE EXCEPTION 'unchanged defaults did not enqueue all nine seeds';
 END IF;
 saved := driftread.save_app_setting('discovery.profiles', initial_value, 2, initial_seeds);
 IF (saved->>'version')::bigint <> 3 OR (SELECT count(*) FROM driftread.discovery_targets) <> 9 THEN
  RAISE EXCEPTION 'reapplying defaults duplicated seeds or lost the version check';
 END IF;
END $$;
ROLLBACK;

BEGIN;
DO $$
DECLARE result jsonb; original_version bigint;
BEGIN
 SELECT version INTO original_version FROM driftread.app_settings WHERE key='discovery.profiles';
 IF original_version<>1 THEN RAISE EXCEPTION 'expected default settings row'; END IF;
 INSERT INTO driftread.discovery_targets(url,host,source,status) VALUES
 ('https://rejected.example/','rejected.example','seed','rejected'),
 ('https://blocked.example/','blocked.example','seed','blocked'),
 ('https://done.example/','done.example','seed','done'),
 ('https://organic.example/','organic.example','article_link','pending');
 result:=driftread.save_app_setting('discovery.profiles','{"profiles":[]}',1,
 '[{"url":"https://new.example/","host":"new.example"},{"url":"https://www.rejected.example/","host":"rejected.example"},{"url":"https://blocked.example/","host":"blocked.example"},{"url":"https://done.example/","host":"done.example"},{"url":"https://organic.example/","host":"organic.example"}]');
 IF (result->>'version')::bigint<>2 OR result->'value'<>'{"profiles":[]}'::jsonb THEN RAISE EXCEPTION 'setting update/version failed'; END IF;
 IF (SELECT count(*) FROM driftread.discovery_targets)<>5 THEN RAISE EXCEPTION 'seed save duplicated host'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.discovery_targets WHERE host='new.example' AND source='seed' AND status='pending') THEN RAISE EXCEPTION 'new seed not queued'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.discovery_targets WHERE host='rejected.example' AND status='rejected') THEN RAISE EXCEPTION 'rejection reset'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.discovery_targets WHERE host='blocked.example' AND status='blocked') THEN RAISE EXCEPTION 'robots block reset'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.discovery_targets WHERE host='done.example' AND status='done') THEN RAISE EXCEPTION 'terminal seed reset'; END IF;
 IF NOT EXISTS(SELECT 1 FROM driftread.discovery_targets WHERE host='organic.example' AND source='article_link') THEN RAISE EXCEPTION 'referral provenance changed'; END IF;
 result:=driftread.save_app_setting('discovery.profiles','{"profiles":[]}',1,'[{"url":"https://stale.example/","host":"stale.example"}]');
 IF result IS NOT NULL OR EXISTS(SELECT 1 FROM driftread.discovery_targets WHERE host='stale.example') THEN RAISE EXCEPTION 'stale save mutated setting or frontier'; END IF;
 result:=driftread.save_app_setting('discovery.profiles','{"profiles":[]}',0,'[]');
 IF result IS NOT NULL THEN RAISE EXCEPTION 'creation overwrote existing settings'; END IF;
 result:=driftread.save_app_setting('fixture.other','{"enabled":false}',0,'[]');
 IF (result->>'version')::int<>1 THEN RAISE EXCEPTION 'generic setting insert failed'; END IF;
 ALTER TABLE driftread.discovery_targets ADD CONSTRAINT fixture_reject_url CHECK(url<>'https://failure.example/');
 BEGIN
  PERFORM driftread.save_app_setting('fixture.failure','{}',0,'[{"url":"https://failure.example/","host":"failure.example"}]');
  RAISE EXCEPTION 'expected seed constraint failure';
 EXCEPTION WHEN check_violation THEN NULL;
 END;
 IF EXISTS(SELECT 1 FROM driftread.app_settings WHERE key='fixture.failure') THEN RAISE EXCEPTION 'seed failure left partial settings'; END IF;
 IF has_table_privilege('anon','driftread.app_settings','SELECT') OR has_table_privilege('authenticated','driftread.app_settings','UPDATE') THEN RAISE EXCEPTION 'settings exposed'; END IF;
 IF has_function_privilege('anon','driftread.save_app_setting(text,jsonb,bigint,jsonb)','EXECUTE') THEN RAISE EXCEPTION 'settings RPC exposed'; END IF;
 UPDATE driftread.app_settings SET value='{"enabled":true}',version=100 WHERE key='fixture.other';
 IF (SELECT version FROM driftread.app_settings WHERE key='fixture.other')<>2 THEN RAISE EXCEPTION 'direct table edit bypassed version trigger'; END IF;
END $$;
ROLLBACK;
