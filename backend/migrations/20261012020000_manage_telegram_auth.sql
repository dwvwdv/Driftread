-- OTP state is server-only. Vault credentials stay in the database, shared with
-- seamless_track; neither token nor recipient is accepted from the browser.
CREATE TABLE IF NOT EXISTS driftread.manage_login_state (
    user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    challenge_id uuid UNIQUE,
    auth_session_id uuid,
    code_hash text,
    expires_at timestamptz,
    last_sent_at timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    locked_until timestamptz,
    telegram_request_id bigint
);
CREATE TABLE IF NOT EXISTS driftread.manage_sessions (
    token_hash text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    auth_session_id uuid NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS manage_sessions_user_idx ON driftread.manage_sessions(user_id);
ALTER TABLE driftread.manage_login_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE driftread.manage_sessions ENABLE ROW LEVEL SECURITY;
-- Migration 010 default privileges grant service_role ALL. These tables use a
-- privileged direct PostgreSQL connection, and must not be exposed by PostgREST.
REVOKE ALL ON driftread.manage_login_state, driftread.manage_sessions
    FROM PUBLIC, anon, authenticated, service_role;
NOTIFY pgrst, 'reload schema';
