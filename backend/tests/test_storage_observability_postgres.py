"""#114 D/E: per-table MVCC snapshots and recent ingest activity on real SQL."""
from pathlib import Path

from tests.test_postgres_lifecycle import pg_database, connection, scalar

MIGRATION = Path(__file__).parents[1] / 'migrations' / '20261022020000_storage_observability.sql'


def test_observability_sql_replay_and_fixture(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            # Ledger-cleared replay must not fail on columns/constraints/indexes.
            cur.execute(MIGRATION.read_text())
            cur.execute(MIGRATION.read_text())
        conn.commit()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql' / 'test_storage_observability.sql').read_text())


def test_activity_window_is_served_by_the_recorded_at_index(pg_database):
    # Never a full article_revisions scan without a time bound: the window
    # predicate must be able to use the new index.
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute('SET LOCAL enable_seqscan = off')
            cur.execute("""EXPLAIN SELECT r.article_id FROM driftread.article_revisions r
                WHERE r.recorded_at >= now() - interval '24 hours'""")
            plan = '\n'.join(row[0] for row in cur.fetchall())
        assert 'article_revisions_recorded_at_idx' in plan


def test_latest_run_lookup_uses_kind_index(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute('SET LOCAL enable_seqscan = off')
            cur.execute("""EXPLAIN SELECT * FROM driftread.worker_runs WHERE kind = 'retention'
                ORDER BY started_at DESC LIMIT 1""")
            plan = '\n'.join(row[0] for row in cur.fetchall())
        assert 'worker_runs_kind_started_idx' in plan


def test_service_role_reads_table_counters(pg_database):
    with connection(pg_database, 'service_role') as conn:
        names = scalar(conn, """SELECT jsonb_agg(t->'name')
            FROM jsonb_array_elements(driftread.table_storage_stats()->'tables') t""")
        assert {'articles', 'article_revisions', 'sync_changes'} <= set(names)
        assert scalar(conn, 'SELECT driftread.source_ingest_activity(24, 10, 500, 200, 3.0, 30) ? %s', ('top_revisions',))


def test_rpc_payloads_match_the_api_models(pg_database):
    from services.ingest_activity import classify
    from services.table_storage import TableSnapshot, interval
    with connection(pg_database, 'service_role') as conn:
        first = TableSnapshot.model_validate(scalar(conn, 'SELECT driftread.table_storage_stats()'))
        conn.commit()
        later = TableSnapshot.model_validate(scalar(conn, 'SELECT driftread.table_storage_stats()'))
        delta = interval(first, later)
        assert {t.name for t in delta.tables} >= {'articles', 'background_jobs'}
        assert all(t.comparable for t in delta.tables if t.name == 'articles')
        activity = scalar(conn, 'SELECT driftread.source_ingest_activity(24, 10, 500, 200, 3.0, 30)')
        for item in activity['top_ingest'] + activity['top_revisions'] + activity['flagged']:
            classify(item)
