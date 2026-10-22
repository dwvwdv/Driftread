BEGIN;
-- #114 D/E observability: per-table snapshots and recent ingest activity.
DELETE FROM driftread.article_storage_snapshots;
SET LOCAL ROLE service_role;
DO $$
DECLARE v_stats jsonb; v_names text[]; v_row jsonb;
BEGIN
    v_stats := driftread.table_storage_stats();
    IF jsonb_typeof(v_stats->'tables') <> 'array' OR NOT v_stats ? 'stats_reset'
       OR (v_stats->>'observed_at') IS NULL THEN
        RAISE EXCEPTION 'table snapshot shape';
    END IF;
    SELECT array_agg(t->>'name') INTO v_names FROM jsonb_array_elements(v_stats->'tables') t;
    IF NOT v_names @> ARRAY['articles','article_revisions','article_discoveries','sync_changes',
                             'discovery_targets','background_jobs'] THEN
        RAISE EXCEPTION 'tracked tables missing: %', v_names;
    END IF;
    IF cardinality(v_names) > 31 THEN RAISE EXCEPTION 'table snapshot not bounded'; END IF;
    SELECT t INTO v_row FROM jsonb_array_elements(v_stats->'tables') t WHERE t->>'name'='articles';
    IF (v_row->>'heap_bytes')::bigint < 0 OR (v_row->>'toast_bytes')::bigint < 0
       OR (v_row->>'index_bytes')::bigint <= 0
       OR (v_row->>'heap_bytes')::bigint + (v_row->>'toast_bytes')::bigint + (v_row->>'index_bytes')::bigint
          <> (v_row->>'total_bytes')::bigint
       OR NOT v_row ?& ARRAY['n_live_tup','n_dead_tup','n_tup_ins','n_tup_upd','n_tup_del','n_tup_hot_upd',
                             'last_vacuum','last_autovacuum','vacuum_count','autovacuum_count'] THEN
        RAISE EXCEPTION 'articles sizes/counters: %', v_row;
    END IF;

    -- New day: the aggregate and the table snapshot are saved together.
    IF NOT driftread.record_article_storage_snapshot() THEN RAISE EXCEPTION 'first sample missing'; END IF;
    IF (SELECT jsonb_typeof(tables->'tables') FROM driftread.article_storage_snapshots) <> 'array' THEN
        RAISE EXCEPTION 'table snapshot not saved';
    END IF;
    IF driftread.record_article_storage_snapshot() THEN RAISE EXCEPTION 'duplicate sample'; END IF;
END $$;
RESET ROLE;

-- A day sampled by the previous migration gets its table snapshot exactly once,
-- without rescanning articles.
UPDATE driftread.article_storage_snapshots SET tables = NULL;
CREATE OR REPLACE FUNCTION driftread.article_storage_stats() RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'same day rescanned articles'; END $$;
DO $$
DECLARE v_aggregate jsonb;
BEGIN
    SELECT stats INTO v_aggregate FROM driftread.article_storage_snapshots;
    IF NOT driftread.record_article_storage_snapshot() THEN RAISE EXCEPTION 'table snapshot not backfilled'; END IF;
    IF (SELECT tables FROM driftread.article_storage_snapshots) IS NULL
       OR (SELECT stats FROM driftread.article_storage_snapshots) <> v_aggregate THEN
        RAISE EXCEPTION 'same-day fill changed the aggregate';
    END IF;
    IF driftread.record_article_storage_snapshot() THEN RAISE EXCEPTION 'table snapshot refilled'; END IF;
    BEGIN
        UPDATE driftread.article_storage_snapshots SET tables = '[]'::jsonb;
        RAISE EXCEPTION 'non-object table snapshot accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO driftread.worker_heartbeats(worker_id,hostname,process_id,started_at,heartbeat_at,status,config)
        VALUES ('observability-test','h',1,now(),now(),'running','"x"'::jsonb);
        RAISE EXCEPTION 'non-object worker config accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $$;

-- Recent ingest and revision activity.
SET LOCAL ROLE service_role;
DO $$
DECLARE
    v_busy uuid; v_quiet uuid; v_old uuid; v_result jsonb; v_item jsonb;
    v_hash_a text := repeat('a', 64); v_hash_b text := repeat('b', 64);
BEGIN
    INSERT INTO driftread.feeds(title,url) VALUES ('busy','https://busy.observability.example/rss') RETURNING id INTO v_busy;
    INSERT INTO driftread.feeds(title,url) VALUES ('quiet','https://quiet.observability.example/rss') RETURNING id INTO v_quiet;
    -- Three new items, one of them an old publication (backfill).
    PERFORM driftread.ingest_article_batch(v_busy, jsonb_build_array(
        jsonb_build_object('title','one','url','https://busy.observability.example/1','content_hash',v_hash_a,
                           'published_at',now()),
        jsonb_build_object('title','two','url','https://busy.observability.example/2','content_hash',v_hash_a,
                           'published_at',now()),
        jsonb_build_object('title','old','url','https://busy.observability.example/3','content_hash',v_hash_a,
                           'published_at',now() - interval '30 days')));
    -- Item 1: two metadata-only edits; item 2: one content edit.
    PERFORM driftread.ingest_article_batch(v_busy, jsonb_build_array(
        jsonb_build_object('title','one v2','url','https://busy.observability.example/1','content_hash',v_hash_a),
        jsonb_build_object('title','two','url','https://busy.observability.example/2','content_hash',v_hash_b)));
    PERFORM driftread.ingest_article_batch(v_busy, jsonb_build_array(
        jsonb_build_object('title','one v3','url','https://busy.observability.example/1','content_hash',v_hash_a)));
    -- An article first fetched days ago is not new, but its edit today counts.
    INSERT INTO driftread.articles(feed_id,title,url,content_hash,fetched_at)
    VALUES (v_quiet,'legacy','https://quiet.observability.example/1',v_hash_a,now() - interval '3 days')
    RETURNING id INTO v_old;
    UPDATE driftread.articles SET title='legacy v2' WHERE id=v_old;

    v_result := driftread.source_ingest_activity(24, 10);
    IF (v_result->>'since')::timestamptz <> now() - interval '24 hours' THEN RAISE EXCEPTION 'window: %', v_result; END IF;
    IF jsonb_array_length(v_result->'top_ingest') <> 1 THEN RAISE EXCEPTION 'quiet feed has no new article: %', v_result; END IF;
    v_item := v_result->'top_ingest'->0;
    IF (v_item->>'feed_id')::uuid <> v_busy OR (v_item->>'new_articles')::int <> 3
       OR (v_item->>'backfill_articles')::int <> 1 OR (v_item->>'archived')::boolean THEN
        RAISE EXCEPTION 'ingest counts: %', v_item;
    END IF;
    v_item := v_result->'top_revisions'->0;
    IF (v_item->>'feed_id')::uuid <> v_busy OR (v_item->>'revision_updates')::int <> 3
       OR (v_item->>'revised_articles')::int <> 2 OR (v_item->>'content_changes')::int <> 1
       OR (v_item->>'metadata_only_changes')::int <> 2 THEN
        RAISE EXCEPTION 'busy revisions: %', v_item;
    END IF;
    v_item := v_result->'top_revisions'->1;
    IF (v_item->>'feed_id')::uuid <> v_quiet OR (v_item->>'revision_updates')::int <> 1
       OR (v_item->>'new_articles')::int <> 0 THEN
        RAISE EXCEPTION 'quiet revisions: %', v_item;
    END IF;
    IF jsonb_array_length(driftread.source_ingest_activity(24, 1)->'top_revisions') <> 1 THEN
        RAISE EXCEPTION 'limit ignored';
    END IF;
END $$;
DO $$
BEGIN
    BEGIN
        PERFORM driftread.source_ingest_activity(0, 10);
        RAISE EXCEPTION 'invalid hours accepted';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    BEGIN
        PERFORM driftread.source_ingest_activity(24, 51);
        RAISE EXCEPTION 'invalid limit accepted';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
END $$;
RESET ROLE;

-- Revisions recorded before the window and articles fetched before it are excluded.
ALTER TABLE driftread.article_revisions DISABLE TRIGGER article_revision_immutable;
UPDATE driftread.article_revisions SET recorded_at = now() - interval '25 hours';
UPDATE driftread.articles SET fetched_at = now() - interval '25 hours';
DO $$
DECLARE v_result jsonb := driftread.source_ingest_activity(24, 10);
BEGIN
    IF v_result->'top_ingest' <> '[]'::jsonb OR v_result->'top_revisions' <> '[]'::jsonb THEN
        RAISE EXCEPTION 'old activity counted: %', v_result;
    END IF;
END $$;
ALTER TABLE driftread.article_revisions ENABLE TRIGGER article_revision_immutable;

DO $$
BEGIN
    IF has_function_privilege('anon','driftread.table_storage_stats()','EXECUTE')
       OR has_function_privilege('authenticated','driftread.table_storage_stats()','EXECUTE')
       OR has_function_privilege('anon','driftread.source_ingest_activity(integer,integer)','EXECUTE')
       OR has_function_privilege('authenticated','driftread.source_ingest_activity(integer,integer)','EXECUTE')
       OR NOT has_function_privilege('service_role','driftread.source_ingest_activity(integer,integer)','EXECUTE')
       OR has_table_privilege('anon','driftread.worker_heartbeats','SELECT')
    THEN RAISE EXCEPTION 'observability access leaked'; END IF;
    IF (SELECT prosecdef FROM pg_proc WHERE oid='driftread.source_ingest_activity(integer,integer)'::regprocedure)
       OR (SELECT prosecdef FROM pg_proc WHERE oid='driftread.table_storage_stats()'::regprocedure) THEN
        RAISE EXCEPTION 'observability functions must be SECURITY INVOKER';
    END IF;
END $$;
ROLLBACK;
