from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.test_postgres_lifecycle import pg_database, connection, scalar, assert_blocked


def test_snapshot_sql_and_replay(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            migration = Path(__file__).parents[1] / 'migrations' / '20261013115249_article_storage_history.sql'
            cur.execute(migration.read_text())
            cur.execute(migration.read_text())
        conn.commit()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql' / 'test_storage_history.sql').read_text())


def test_concurrent_sampler_saves_only_one_sample(pg_database):
    with connection(pg_database) as setup, connection(pg_database, 'service_role') as first, connection(pg_database, 'service_role') as second:
        with setup.cursor() as cur:
            cur.execute('DELETE FROM driftread.article_storage_snapshots')
        setup.commit()
        assert scalar(first, 'SELECT driftread.record_article_storage_snapshot()') is True
        pid = second.get_backend_pid()
        def sample():
            result = scalar(second, 'SELECT driftread.record_article_storage_snapshot()')
            second.commit()
            return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(sample)
            try:
                assert_blocked(setup, pid, future)
            finally:
                first.commit()
            assert future.result(timeout=5) is False
        assert scalar(setup, 'SELECT count(*) FROM driftread.article_storage_snapshots') == 1
