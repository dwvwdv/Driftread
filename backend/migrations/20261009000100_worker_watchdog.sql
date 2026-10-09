-- Durable incidents observed by the API process; no outbound notifications.
ALTER TABLE driftread.worker_runs DROP CONSTRAINT IF EXISTS worker_runs_status_check;
ALTER TABLE driftread.worker_runs ADD CONSTRAINT worker_runs_status_check
 CHECK(status IN ('running','succeeded','partial','failed','cancelled','interrupted'));
CREATE TABLE IF NOT EXISTS driftread.worker_alerts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 worker_id text NOT NULL,
 kind text NOT NULL CHECK(kind='worker_stale'),
 created_at timestamptz NOT NULL DEFAULT now(),
 resolved_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS worker_alerts_active_idx ON driftread.worker_alerts(worker_id,kind)
 WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS worker_alerts_created_idx ON driftread.worker_alerts(created_at DESC);
ALTER TABLE driftread.worker_alerts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.worker_alerts FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON driftread.worker_alerts TO service_role;

CREATE OR REPLACE FUNCTION driftread.watchdog_worker_operations()
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,driftread AS $$
DECLARE v_alerts integer:=0; v_recovered integer:=0; v_interrupted integer:=0; v_reconciled integer:=0;
BEGIN
 IF NOT pg_try_advisory_xact_lock(78102639) THEN
  RETURN jsonb_build_object('new_alerts',0,'recovered',0,'interrupted',0,'reconciled',0);
 END IF;
 WITH stale AS (
  SELECT w.worker_id FROM driftread.worker_heartbeats w
  WHERE w.status='running' AND w.heartbeat_at<clock_timestamp()-interval '90 seconds'
   AND NOT EXISTS(SELECT 1 FROM driftread.worker_alerts a WHERE a.worker_id=w.worker_id AND a.resolved_at IS NULL)
  ORDER BY w.heartbeat_at LIMIT 100 FOR UPDATE SKIP LOCKED
 ) INSERT INTO driftread.worker_alerts(worker_id,kind)
 SELECT worker_id,'worker_stale' FROM stale ON CONFLICT(worker_id,kind) WHERE resolved_at IS NULL DO NOTHING;
 GET DIAGNOSTICS v_alerts=ROW_COUNT;
 WITH recovered AS (
  SELECT a.id FROM driftread.worker_alerts a JOIN driftread.worker_heartbeats w USING(worker_id)
  WHERE a.resolved_at IS NULL AND (w.status='stopped' OR w.heartbeat_at>=clock_timestamp()-interval '90 seconds')
  ORDER BY a.created_at LIMIT 100 FOR UPDATE OF a SKIP LOCKED
 ) UPDATE driftread.worker_alerts SET resolved_at=clock_timestamp() WHERE id IN(SELECT id FROM recovered);
 GET DIAGNOSTICS v_recovered=ROW_COUNT;
 WITH interrupted AS (
  SELECT r.id FROM driftread.worker_runs r WHERE r.status='running'
   AND (r.started_at<clock_timestamp()-interval '90 seconds' OR EXISTS(
    SELECT 1 FROM driftread.worker_heartbeats w WHERE w.worker_id=r.worker_id
     AND (w.status='stopped' OR w.heartbeat_at<clock_timestamp()-interval '90 seconds')))
   AND NOT EXISTS(
   SELECT 1 FROM driftread.worker_heartbeats w WHERE w.worker_id=r.worker_id AND w.status='running'
     AND w.heartbeat_at>=clock_timestamp()-interval '90 seconds')
  ORDER BY r.started_at LIMIT 1000 FOR UPDATE SKIP LOCKED
 ) UPDATE driftread.worker_runs SET status='interrupted',finished_at=clock_timestamp(),error='WorkerHeartbeatLost'
 WHERE id IN(SELECT id FROM interrupted);
 GET DIAGNOSTICS v_interrupted=ROW_COUNT;
 v_reconciled:=driftread.reconcile_background_jobs(100);
 DELETE FROM driftread.background_jobs WHERE id IN (
  SELECT id FROM driftread.background_jobs WHERE status='succeeded'
   AND finished_at<clock_timestamp()-interval '30 days' ORDER BY finished_at LIMIT 1000 FOR UPDATE SKIP LOCKED);
 DELETE FROM driftread.worker_alerts WHERE id IN (
  SELECT id FROM driftread.worker_alerts WHERE resolved_at<clock_timestamp()-interval '30 days'
   ORDER BY resolved_at LIMIT 1000 FOR UPDATE SKIP LOCKED);
 RETURN jsonb_build_object('new_alerts',v_alerts,'recovered',v_recovered,'interrupted',v_interrupted,'reconciled',v_reconciled);
END $$;
REVOKE ALL ON FUNCTION driftread.watchdog_worker_operations() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION driftread.watchdog_worker_operations() TO service_role;
NOTIFY pgrst,'reload schema';
