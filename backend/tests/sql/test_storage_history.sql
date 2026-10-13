BEGIN;
-- The worker's best-effort recorder must be able to persist storage outcomes.
SET LOCAL ROLE service_role;
DO $$
DECLARE v_run uuid; v_kind text;
BEGIN
    FOREACH v_kind IN ARRAY ARRAY['refresh', 'discovery', 'retention', 'storage'] LOOP
        v_run := gen_random_uuid();
        INSERT INTO driftread.worker_runs(id, worker_id, kind, status, started_at)
        VALUES (v_run, 'storage-history-test', v_kind, 'running', now());
        UPDATE driftread.worker_runs
        SET status='failed', finished_at=now(), error='StorageSampleError'
        WHERE id=v_run;
        IF NOT EXISTS (SELECT 1 FROM driftread.worker_runs
            WHERE id=v_run AND kind=v_kind AND status='failed' AND error='StorageSampleError') THEN
            RAISE EXCEPTION 'worker run failure not persisted';
        END IF;
        UPDATE driftread.worker_runs SET status='succeeded', error=NULL,
            summary='{"recorded":true}'::jsonb WHERE id=v_run;
        IF NOT EXISTS (SELECT 1 FROM driftread.worker_runs
            WHERE id=v_run AND status='succeeded' AND summary='{"recorded":true}'::jsonb) THEN
            RAISE EXCEPTION 'worker run success not persisted';
        END IF;
    END LOOP;
    BEGIN
        INSERT INTO driftread.worker_runs(id, worker_id, kind, status, started_at)
        VALUES (gen_random_uuid(), 'storage-history-test', 'unknown-kind', 'running', now());
        RAISE EXCEPTION 'unknown worker kind accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $$;
RESET ROLE;
DELETE FROM driftread.article_storage_snapshots;
INSERT INTO driftread.article_storage_snapshots(snapshot_date,observed_at,stats)
VALUES ((now() AT TIME ZONE 'UTC')::date - 90, now() - interval '90 days', '{}'),
       ((now() AT TIME ZONE 'UTC')::date - 89, now() - interval '89 days', '{}');
DO $$
DECLARE v_saved jsonb;
BEGIN
    IF NOT driftread.record_article_storage_snapshot() THEN RAISE EXCEPTION 'first sample missing'; END IF;
    IF (SELECT count(*) FROM driftread.article_storage_snapshots) != 2 THEN RAISE EXCEPTION '90 day retention boundary'; END IF;
    SELECT stats INTO v_saved FROM driftread.article_storage_snapshots WHERE snapshot_date=(now() AT TIME ZONE 'UTC')::date;
    IF (v_saved->>'article_count')::bigint != (SELECT count(*) FROM driftread.articles) THEN RAISE EXCEPTION 'article count incorrect'; END IF;
    IF (v_saved->>'total_bytes')::bigint <= 0 THEN RAISE EXCEPTION 'relation size missing'; END IF;
    IF driftread.record_article_storage_snapshot() THEN RAISE EXCEPTION 'duplicate sample'; END IF;
    IF NOT EXISTS(SELECT 1 FROM driftread.article_storage_snapshots WHERE stats=v_saved) THEN RAISE EXCEPTION 'sample overwritten'; END IF;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid='driftread.article_storage_snapshots'::regclass) THEN RAISE EXCEPTION 'RLS disabled'; END IF;
    IF has_table_privilege('anon','driftread.article_storage_snapshots','SELECT')
       OR has_table_privilege('authenticated','driftread.article_storage_snapshots','INSERT')
       OR has_function_privilege('anon','driftread.record_article_storage_snapshot()','EXECUTE')
       OR has_function_privilege('authenticated','driftread.record_article_storage_snapshot()','EXECUTE')
    THEN RAISE EXCEPTION 'public telemetry access'; END IF;
END $$;
-- A repeat attempt must not scan the articles again.
CREATE OR REPLACE FUNCTION driftread.article_storage_stats() RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'same day rescanned articles'; END $$;
SELECT driftread.record_article_storage_snapshot();
ROLLBACK;
