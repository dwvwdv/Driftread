-- Permanent grants/refresh are allowed; access credentials must retain a TTL.
BEGIN;
DO $$
DECLARE
 uid uuid := gen_random_uuid();
 gid uuid := gen_random_uuid();
 client text := 'expiry-fixture-' || gen_random_uuid();
BEGIN
 INSERT INTO auth.users(id) VALUES(uid);
 INSERT INTO driftread.review_oauth_clients(client_id,client_name,redirect_uris)
  VALUES(client,'expiry fixture','[]');
 INSERT INTO driftread.review_oauth_grants
  (id,client_id,owner_user_id,auth_session_id,scope,resource,expires_at)
  VALUES(gid,client,uid,gen_random_uuid(),'source:review offline_access','https://reader.example/api/admin/mcp/',NULL);
 INSERT INTO driftread.review_oauth_tokens(token_hash,grant_id,kind,expires_at)
  VALUES('expiry-refresh-fixture',gid,'refresh',NULL);
 BEGIN
  INSERT INTO driftread.review_oauth_tokens(token_hash,grant_id,kind,expires_at)
   VALUES('expiry-access-fixture',gid,'access',NULL);
  RAISE EXCEPTION 'Access tokens must not accept an unlimited lifetime';
 EXCEPTION WHEN check_violation THEN NULL;
 END;
 INSERT INTO driftread.review_oauth_tokens(token_hash,grant_id,kind,expires_at)
  VALUES('expiry-access-fixture',gid,'access',now()+interval '15 minutes');
 IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid IN
   ('driftread.review_oauth_grants'::regclass,'driftread.review_oauth_tokens'::regclass)) THEN
  RAISE EXCEPTION 'Expiry migration must not expose OAuth grants or credentials';
 END IF;
END $$;
ROLLBACK;
