DO $$
DECLARE version_before bigint; saved jsonb;
BEGIN
 ASSERT (SELECT value FROM driftread.app_settings WHERE key='discovery.control')='{"enabled":true}'::jsonb;
 SELECT version INTO version_before FROM driftread.app_settings WHERE key='discovery.control';
 saved:=driftread.save_app_setting('discovery.control','{"enabled":false}',version_before);
 ASSERT saved->'value'='{"enabled":false}'::jsonb;
 ASSERT NOT driftread.discovery_runtime_enabled();
 ASSERT driftread.save_app_setting('discovery.control','{"enabled":true}',version_before) IS NULL, 'stale version resumed pipeline';
 ASSERT NOT driftread.discovery_runtime_enabled();
 saved:=driftread.save_app_setting('discovery.control','{"enabled":true}',version_before+1);
 ASSERT driftread.discovery_runtime_enabled();
END $$;
