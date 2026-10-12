-- Isolated test substitutes: no real Vault credential or network request.
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS is_anonymous boolean DEFAULT false;
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS banned_until timestamptz;
CREATE TABLE IF NOT EXISTS auth.sessions (
 id uuid PRIMARY KEY, user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE, not_after timestamptz
);
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
ALTER EXTENSION pgcrypto SET SCHEMA extensions;
CREATE SCHEMA IF NOT EXISTS vault;
CREATE TABLE IF NOT EXISTS vault.decrypted_secrets(name text PRIMARY KEY,decrypted_secret text);
INSERT INTO vault.decrypted_secrets VALUES ('telegram_bot_token','test-bot'),('telegram_chat_id','123456')
 ON CONFLICT(name) DO UPDATE SET decrypted_secret=excluded.decrypted_secret;
CREATE SCHEMA IF NOT EXISTS net;
CREATE TABLE IF NOT EXISTS net.test_requests(id bigserial PRIMARY KEY,url text,body jsonb);
CREATE TABLE IF NOT EXISTS net._http_response(id bigint PRIMARY KEY,status_code integer,content text);
CREATE OR REPLACE FUNCTION net.http_post(url text,body jsonb,headers jsonb,timeout_milliseconds integer)
 RETURNS bigint LANGUAGE plpgsql AS $$
 DECLARE request_id bigint;
 BEGIN
  INSERT INTO net.test_requests(url,body) VALUES(url,body) RETURNING id INTO request_id;
  INSERT INTO net._http_response VALUES(request_id,200,'{"ok":true}');
  RETURN request_id;
 END $$;
