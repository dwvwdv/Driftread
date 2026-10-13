-- Review shares durable leases, singleton scheduling and the API watchdog.
-- Registering its kind neither enables review nor enqueues outbound work.
INSERT INTO driftread.background_job_kinds(kind,max_concurrency)
VALUES ('source_review',1) ON CONFLICT(kind) DO NOTHING;

ALTER TABLE driftread.worker_runs DROP CONSTRAINT IF EXISTS worker_runs_kind_check;
ALTER TABLE driftread.worker_runs ADD CONSTRAINT worker_runs_kind_check
CHECK(kind IN ('refresh','discovery','retention','source_review'));

NOTIFY pgrst,'reload schema';
