-- Driftread owns this schema inside the shared Lazyrhythm Supabase project.
CREATE SCHEMA IF NOT EXISTS driftread;

REVOKE ALL ON SCHEMA driftread FROM PUBLIC;
GRANT USAGE ON SCHEMA driftread TO anon, authenticated, service_role;

-- Append Driftread to PostgREST's existing schema list. This project is shared
-- with other applications, so replacing the setting would silently remove
-- their schemas from the Data API.
DO $$
DECLARE
  exposed_schemas text;
BEGIN
  SELECT split_part(setting, '=', 2)
  INTO exposed_schemas
  FROM pg_roles
  CROSS JOIN LATERAL unnest(COALESCE(rolconfig, ARRAY[]::text[])) AS setting
  WHERE rolname = 'authenticator'
    AND setting LIKE 'pgrst.db_schemas=%';

  exposed_schemas := COALESCE(exposed_schemas, 'public, graphql_public');
  IF NOT ('driftread' = ANY(regexp_split_to_array(exposed_schemas, '\s*,\s*'))) THEN
    exposed_schemas := exposed_schemas || ', driftread';
  END IF;

  EXECUTE format(
    'ALTER ROLE authenticator SET pgrst.db_schemas = %L',
    exposed_schemas
  );
END
$$;

