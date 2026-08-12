-- Keep alternate RSS endpoints available without importing or rejecting them.
ALTER TABLE driftread.discovery_candidates
  DROP CONSTRAINT IF EXISTS discovery_candidates_status_check;

ALTER TABLE driftread.discovery_candidates
  ADD CONSTRAINT discovery_candidates_status_check
  CHECK (status IN ('pending', 'held', 'approved', 'rejected', 'imported'));

CREATE INDEX IF NOT EXISTS discovery_candidates_held_host_idx
  ON driftread.discovery_candidates (source_host, discovered_at DESC)
  WHERE status = 'held';
