-- OAuth credentials are not exposed through the Data API, including service_role.
-- Only the privileged DATABASE_URL used by manage_auth may read these tables.
-- One durable key authenticates stateless public DCR IDs. Preserve it in
-- backups: client registrations intentionally outlive all token/grant TTLs.
CREATE TABLE IF NOT EXISTS driftread.review_oauth_registration_key (
 singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
 signing_key bytea NOT NULL CHECK (octet_length(signing_key)=32)
);
CREATE TABLE IF NOT EXISTS driftread.review_oauth_clients (
 client_id text PRIMARY KEY,
 client_name text NOT NULL,
 redirect_uris jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 last_used_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS driftread.review_oauth_requests (
 request_hash text PRIMARY KEY,
 client_id text NOT NULL,
 client_name text NOT NULL,
 redirect_uris jsonb NOT NULL,
 redirect_uri text NOT NULL,
 state text NOT NULL,
 scope text NOT NULL,
 resource text NOT NULL,
 code_challenge text NOT NULL,
 expires_at timestamptz NOT NULL,
 decided_at timestamptz
);
-- Safe if this not-yet-merged migration is rerun over its previous version.
ALTER TABLE driftread.review_oauth_requests DROP CONSTRAINT IF EXISTS review_oauth_requests_client_id_fkey;
ALTER TABLE driftread.review_oauth_requests ADD COLUMN IF NOT EXISTS client_name text;
ALTER TABLE driftread.review_oauth_requests ADD COLUMN IF NOT EXISTS redirect_uris jsonb;
UPDATE driftread.review_oauth_requests r SET client_name=c.client_name,redirect_uris=c.redirect_uris
 FROM driftread.review_oauth_clients c WHERE r.client_id=c.client_id
 AND (r.client_name IS NULL OR r.redirect_uris IS NULL);
ALTER TABLE driftread.review_oauth_requests ALTER COLUMN client_name SET NOT NULL;
ALTER TABLE driftread.review_oauth_requests ALTER COLUMN redirect_uris SET NOT NULL;
CREATE INDEX IF NOT EXISTS review_oauth_requests_expiry_idx ON driftread.review_oauth_requests(expires_at);
CREATE TABLE IF NOT EXISTS driftread.review_oauth_grants (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 client_id text NOT NULL REFERENCES driftread.review_oauth_clients(client_id),
 owner_user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 auth_session_id uuid NOT NULL,
 scope text NOT NULL,
 resource text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 last_used_at timestamptz
);
CREATE INDEX IF NOT EXISTS review_oauth_grants_owner_idx ON driftread.review_oauth_grants(owner_user_id,auth_session_id);
CREATE INDEX IF NOT EXISTS review_oauth_grants_expiry_idx ON driftread.review_oauth_grants(expires_at);
CREATE TABLE IF NOT EXISTS driftread.review_oauth_codes (
 code_hash text PRIMARY KEY,
 grant_id uuid NOT NULL REFERENCES driftread.review_oauth_grants(id) ON DELETE CASCADE,
 redirect_uri text NOT NULL,
 code_challenge text NOT NULL,
 expires_at timestamptz NOT NULL,
 consumed_at timestamptz
);
CREATE INDEX IF NOT EXISTS review_oauth_codes_grant_idx ON driftread.review_oauth_codes(grant_id);
CREATE TABLE IF NOT EXISTS driftread.review_oauth_tokens (
 token_hash text PRIMARY KEY,
 grant_id uuid NOT NULL REFERENCES driftread.review_oauth_grants(id) ON DELETE CASCADE,
 kind text NOT NULL CHECK (kind IN ('access','refresh')),
 expires_at timestamptz NOT NULL,
 consumed_at timestamptz
);
CREATE INDEX IF NOT EXISTS review_oauth_tokens_grant_idx ON driftread.review_oauth_tokens(grant_id);
ALTER TABLE driftread.review_oauth_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.review_oauth_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.review_oauth_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.review_oauth_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.review_oauth_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.review_oauth_registration_key ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.review_oauth_clients,driftread.review_oauth_requests,
 driftread.review_oauth_grants,driftread.review_oauth_codes,driftread.review_oauth_tokens,
 driftread.review_oauth_registration_key
 FROM PUBLIC,anon,authenticated,service_role;
NOTIFY pgrst, 'reload schema';
