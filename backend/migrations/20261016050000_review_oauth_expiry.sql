-- NULL means an explicitly owner-managed grant/refresh with no fixed expiry.
-- Access credentials remain short lived; every request checks the current grant.
ALTER TABLE driftread.review_oauth_grants ALTER COLUMN expires_at DROP NOT NULL;
ALTER TABLE driftread.review_oauth_tokens ALTER COLUMN expires_at DROP NOT NULL;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint
  WHERE conrelid='driftread.review_oauth_tokens'::regclass AND conname='review_oauth_access_expiry_required') THEN
  ALTER TABLE driftread.review_oauth_tokens ADD CONSTRAINT review_oauth_access_expiry_required
   CHECK (kind<>'access' OR expires_at IS NOT NULL);
 END IF;
END $$;
CREATE INDEX IF NOT EXISTS review_oauth_grants_revoked_idx
 ON driftread.review_oauth_grants(revoked_at) WHERE revoked_at IS NOT NULL;
NOTIFY pgrst, 'reload schema';
