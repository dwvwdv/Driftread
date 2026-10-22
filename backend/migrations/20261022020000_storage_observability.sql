-- #114 D/E: read-only observability. Nothing here deletes data, changes
-- retention, throttles sources or touches search_vector. Design and limits:
-- docs/CRAWLER_OPERATIONS.md「每表容量與 MVCC 觀測」「來源入庫量與版本異常」.

-- 1. Per-table heap/TOAST/index sizes and cumulative pg_stat counters, saved
--    beside the existing daily aggregate (same advisory lock, UTC-day dedupe
--    and 90-day retention). Old rows keep NULL: history is not reconstructed.
ALTER TABLE driftread.article_storage_snapshots ADD COLUMN IF NOT EXISTS tables jsonb;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='driftread.article_storage_snapshots'::regclass
          AND conname='article_storage_snapshots_tables_check') THEN
        ALTER TABLE driftread.article_storage_snapshots ADD CONSTRAINT article_storage_snapshots_tables_check
            CHECK (tables IS NULL OR jsonb_typeof(tables) = 'object');
    END IF;
END $$;

-- Catalog/statistics only: never scans table contents. The largest 25 tables
-- plus the tables #114 tracks explicitly, so adjacent samples stay comparable.
CREATE OR REPLACE FUNCTION driftread.table_storage_stats()
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $$
    WITH rel AS (
        SELECT c.oid, c.relname, c.reltoastrelid,
               pg_total_relation_size(c.oid) AS total_bytes,
               pg_table_size(c.oid) AS table_bytes,
               pg_indexes_size(c.oid) AS index_bytes,
               CASE WHEN c.reltoastrelid = 0 THEN 0
                    ELSE pg_total_relation_size(c.reltoastrelid) END AS toast_bytes
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'driftread' AND c.relkind = 'r'
    ), picked AS (
        (SELECT * FROM rel ORDER BY total_bytes DESC, relname LIMIT 25)
        UNION
        SELECT * FROM rel WHERE relname IN ('articles', 'article_revisions', 'article_discoveries',
            'sync_changes', 'discovery_targets', 'background_jobs')
    )
    SELECT jsonb_build_object(
        'observed_at', clock_timestamp(),
        -- Database-wide reset time; NULL means never reset since cluster init.
        'stats_reset', (SELECT d.stats_reset FROM pg_stat_database d WHERE d.datname = current_database()),
        'tables', coalesce((SELECT jsonb_agg(jsonb_build_object(
            'name', p.relname,
            'heap_bytes', p.table_bytes - p.toast_bytes,
            'toast_bytes', p.toast_bytes,
            'index_bytes', p.index_bytes,
            'total_bytes', p.total_bytes,
            'n_live_tup', s.n_live_tup,
            'n_dead_tup', s.n_dead_tup,
            'n_tup_ins', s.n_tup_ins,
            'n_tup_upd', s.n_tup_upd,
            'n_tup_del', s.n_tup_del,
            'n_tup_hot_upd', s.n_tup_hot_upd,
            'last_vacuum', s.last_vacuum,
            'last_autovacuum', s.last_autovacuum,
            'vacuum_count', s.vacuum_count,
            'autovacuum_count', s.autovacuum_count
        ) ORDER BY p.total_bytes DESC, p.relname)
        FROM picked p LEFT JOIN pg_stat_user_tables s ON s.relid = p.oid), '[]'::jsonb))
$$;
REVOKE ALL ON FUNCTION driftread.table_storage_stats() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.table_storage_stats() TO service_role;

-- Same contract as before, plus the table snapshot. A day sampled before this
-- migration gets its table snapshot filled once (catalog read, no article scan).
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
        UPDATE driftread.article_storage_snapshots SET tables = driftread.table_storage_stats()
        WHERE snapshot_date = v_day AND tables IS NULL;
        RETURN FOUND;
    END IF;
    INSERT INTO driftread.article_storage_snapshots(snapshot_date, observed_at, stats, tables)
    VALUES (v_day, v_now, driftread.article_storage_stats(), driftread.table_storage_stats());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION driftread.record_article_storage_snapshot() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.record_article_storage_snapshot() TO service_role;

-- 2. Recent ingest and revision activity per source, for display/alerts only.
--    article_revisions is insert-only and capped at 10 rows per article, so a
--    recorded_at index adds no HOT/update cost; it bounds the scan to the window.
CREATE INDEX IF NOT EXISTS article_revisions_recorded_at_idx
    ON driftread.article_revisions(recorded_at);

-- Every inserted article records revision 1 at insert time and later
-- revisions are newer, so a new article always has a surviving revision in
-- the window: new-article counts are exact even when pruning removed rev 1.
-- Update counts are a lower bound once one article exceeds the 10-version cap.
-- The alert thresholds come from the caller (backend/services/ingest_activity.py
-- keeps the only copy) and are applied to every source before the top lists are
-- truncated, so a per-article churn source outside both top lists still shows.
CREATE OR REPLACE FUNCTION driftread.source_ingest_activity(
    p_hours integer, p_limit integer,
    p_alert_new_articles integer, p_alert_revision_updates integer,
    p_alert_per_article numeric, p_alert_min_updates integer)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE
    v_now timestamptz := now();
    v_since timestamptz;
BEGIN
    IF p_hours IS NULL OR p_hours NOT BETWEEN 1 AND 168 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'invalid ingest activity window' USING ERRCODE = '22023';
    END IF;
    IF coalesce(p_alert_new_articles, 0) < 1 OR coalesce(p_alert_revision_updates, 0) < 1
       OR coalesce(p_alert_per_article, 0) <= 0 OR coalesce(p_alert_min_updates, 0) < 1 THEN
        RAISE EXCEPTION 'invalid ingest activity thresholds' USING ERRCODE = '22023';
    END IF;
    v_since := v_now - make_interval(hours => p_hours);
    RETURN (
        WITH win AS (
            SELECT r.article_id, r.revision_number, a.feed_id, a.backfill,
                   a.fetched_at >= v_since AS is_new,
                   p.article_id IS NOT NULL AS has_previous,
                   p.content_hash IS DISTINCT FROM r.content_hash AS content_changed
            FROM driftread.article_revisions r
            JOIN driftread.articles a ON a.id = r.article_id
            LEFT JOIN driftread.article_revisions p
              ON p.article_id = r.article_id AND p.revision_number = r.revision_number - 1
            WHERE r.recorded_at >= v_since
        ), per_feed AS (
            SELECT feed_id,
                   count(DISTINCT article_id) FILTER (WHERE is_new) AS new_articles,
                   count(DISTINCT article_id) FILTER (WHERE is_new AND backfill) AS backfill_articles,
                   count(*) FILTER (WHERE revision_number > 1) AS revision_updates,
                   count(DISTINCT article_id) FILTER (WHERE revision_number > 1) AS revised_articles,
                   count(*) FILTER (WHERE revision_number > 1 AND has_previous AND content_changed) AS content_changes,
                   count(*) FILTER (WHERE revision_number > 1 AND has_previous AND NOT content_changed) AS metadata_only_changes
            FROM win GROUP BY feed_id
        ), ranked AS (
            SELECT jsonb_build_object('feed_id', f.id, 'title', f.title, 'url', f.url,
                       'archived', f.archived_at IS NOT NULL,
                       'new_articles', s.new_articles, 'backfill_articles', s.backfill_articles,
                       'revision_updates', s.revision_updates, 'revised_articles', s.revised_articles,
                       'content_changes', s.content_changes,
                       'metadata_only_changes', s.metadata_only_changes) AS item,
                   s.new_articles, s.revision_updates, f.id,
                   (s.new_articles >= p_alert_new_articles
                    OR s.revision_updates >= p_alert_revision_updates
                    OR (s.revision_updates >= p_alert_min_updates
                        AND s.revision_updates >= p_alert_per_article * s.revised_articles)) AS flagged
            FROM per_feed s JOIN driftread.feeds f ON f.id = s.feed_id
        )
        SELECT jsonb_build_object(
            'observed_at', v_now,
            'since', v_since,
            'top_ingest', coalesce((SELECT jsonb_agg(item ORDER BY new_articles DESC, id) FROM (
                SELECT item, new_articles, id FROM ranked WHERE new_articles > 0
                ORDER BY new_articles DESC, id LIMIT p_limit) t), '[]'::jsonb),
            'top_revisions', coalesce((SELECT jsonb_agg(item ORDER BY revision_updates DESC, id) FROM (
                SELECT item, revision_updates, id FROM ranked WHERE revision_updates > 0
                ORDER BY revision_updates DESC, id LIMIT p_limit) t), '[]'::jsonb),
            'flagged_total', (SELECT count(*) FROM ranked WHERE flagged),
            'flagged', coalesce((SELECT jsonb_agg(item ORDER BY new_articles DESC, revision_updates DESC, id) FROM (
                SELECT item, new_articles, revision_updates, id FROM ranked WHERE flagged
                ORDER BY new_articles DESC, revision_updates DESC, id LIMIT p_limit) t), '[]'::jsonb))
    );
END $$;
REVOKE ALL ON FUNCTION driftread.source_ingest_activity(integer, integer, integer, integer, numeric, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION driftread.source_ingest_activity(integer, integer, integer, integer, numeric, integer)
    TO service_role;

-- 3. The worker reports the scheduler configuration it actually parsed, so the
--    admin can see ARTICLE_RETENTION_ENABLED etc. without reading the API's env.
ALTER TABLE driftread.worker_heartbeats ADD COLUMN IF NOT EXISTS config jsonb;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='driftread.worker_heartbeats'::regclass
          AND conname='worker_heartbeats_config_check') THEN
        ALTER TABLE driftread.worker_heartbeats ADD CONSTRAINT worker_heartbeats_config_check
            CHECK (config IS NULL OR jsonb_typeof(config) = 'object');
    END IF;
END $$;
-- Latest run per kind; a daily retention run would otherwise fall out of the
-- recent window behind frequent refresh runs.
CREATE INDEX IF NOT EXISTS worker_runs_kind_started_idx
    ON driftread.worker_runs(kind, started_at DESC);

NOTIFY pgrst, 'reload schema';
