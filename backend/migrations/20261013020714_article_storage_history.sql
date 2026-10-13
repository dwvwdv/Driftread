-- At most one aggregate sample per UTC day. No article or user payload is saved.
CREATE TABLE IF NOT EXISTS driftread.article_storage_snapshots (
    snapshot_date date PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    stats jsonb NOT NULL CHECK (jsonb_typeof(stats) = 'object')
);
ALTER TABLE driftread.article_storage_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON driftread.article_storage_snapshots FROM PUBLIC, anon, authenticated;
GRANT ALL ON driftread.article_storage_snapshots TO service_role;

INSERT INTO driftread.background_job_kinds(kind)
VALUES ('storage') ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION driftread.record_article_storage_snapshot()
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE
    v_now timestamptz;
    v_day date;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('driftread.article_storage_snapshots', 0));
    v_now := clock_timestamp();
    v_day := (v_now AT TIME ZONE 'UTC')::date;
    DELETE FROM driftread.article_storage_snapshots WHERE snapshot_date < v_day - 89;
    IF EXISTS (SELECT 1 FROM driftread.article_storage_snapshots WHERE snapshot_date = v_day) THEN
        RETURN false;
    END IF;
    INSERT INTO driftread.article_storage_snapshots(snapshot_date, observed_at, stats)
    VALUES (v_day, v_now, driftread.article_storage_stats());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION driftread.record_article_storage_snapshot() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.record_article_storage_snapshot() TO service_role;
NOTIFY pgrst, 'reload schema';
