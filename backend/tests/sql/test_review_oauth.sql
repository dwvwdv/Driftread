-- Isolated Supabase session substitutes; no Vault or external request.
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS is_anonymous boolean DEFAULT false;
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS banned_until timestamptz;
CREATE TABLE IF NOT EXISTS auth.sessions (
 id uuid PRIMARY KEY, user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
 not_after timestamptz
);
