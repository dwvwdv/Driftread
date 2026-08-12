-- Driftread owns this schema inside the shared Lazyrhythm Supabase project.
CREATE SCHEMA IF NOT EXISTS driftread;

REVOKE ALL ON SCHEMA driftread FROM PUBLIC;
GRANT USAGE ON SCHEMA driftread TO anon, authenticated, service_role;

-- Expose the app schema through PostgREST while retaining Supabase's defaults.
-- This database-level setting intentionally becomes the source of truth instead
-- of the Dashboard's Exposed Schemas field.
ALTER ROLE authenticator SET pgrst.db_schemas = 'public, graphql_public, driftread';

