-- OAuth credentials are not exposed through the Data API, including service_role.
-- Only the privileged DATABASE_URL used by manage_auth may read these tables.
CREATE TABLE IF NOT EXISTS driftread.review_oauth_clients (
 client_id text PRIMARY KEY,
 client_name text NOT NULL,
 redirect_uris jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 last_used_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS driftread.review_oauth_requests (
 request_hash text PRIMARY KEY,
 client_id text NOT NULL REFERENCES driftread.review_oauth_clients(client_id) ON DELETE CASCADE,
 redirect_uri text NOT NULL,
 state text NOT NULL,
 scope text NOT NULL,
 resource text NOT NULL,
 code_challenge text NOT NULL,
 expires_at timestamptz NOT NULL,
 decided_at timestamptz
);
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
REVOKE ALL ON driftread.review_oauth_clients,driftread.review_oauth_requests,
 driftread.review_oauth_grants,driftread.review_oauth_codes,driftread.review_oauth_tokens
 FROM PUBLIC,anon,authenticated,service_role;
NOTIFY pgrst, 'reload schema';
