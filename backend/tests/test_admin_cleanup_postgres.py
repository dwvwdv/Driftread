from pathlib import Path

import psycopg2
import pytest
from psycopg2.extensions import make_dsn
from unittest.mock import Mock

from tests.test_postgres_lifecycle import pg_database, connection, scalar
from services.cleanup import CleanupBatch, apply_batch


def test_cleanup_workflow_and_replay(pg_database):
    root = Path(__file__).parents[1]
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            migration = (root / 'migrations/20261018033131_admin_cleanup.sql').read_text()
            cur.execute(migration)
            cur.execute(migration)
            cur.execute((root / 'tests/sql/test_admin_cleanup.sql').read_text())


def test_direct_delete_outlives_short_session_timeout_and_keeps_rpc_guards(pg_database, monkeypatch):
    with connection(pg_database) as setup:
        feed = scalar(setup, "INSERT INTO driftread.feeds(title,url) VALUES('slow cleanup','https://slow-cleanup.invalid/rss') RETURNING id")
        article = scalar(setup, "INSERT INTO driftread.articles(feed_id,title,url) VALUES(%s,'slow','https://slow-cleanup.invalid/post') RETURNING id", (feed,))
        with setup.cursor() as cur:
            cur.execute("""CREATE FUNCTION driftread.test_slow_cleanup() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.2); RETURN OLD; END $$;
                CREATE TRIGGER test_slow_cleanup BEFORE DELETE ON driftread.feeds
                FOR EACH ROW EXECUTE FUNCTION driftread.test_slow_cleanup();""")
        setup.commit()
    try:
        monkeypatch.setenv('DATABASE_URL', make_dsn(pg_database, options='-c statement_timeout=100ms'))
        body = CleanupBatch(kind='feeds', ids=[feed], action='delete', dry_run=False, confirm_delete=True)
        # The direct adapter still enforces staging in the original service-only RPC.
        assert apply_batch(Mock(spec=['rpc']), body)['items'][0]['error'] == 'stage_first'
        with connection(pg_database, 'service_role') as admin:
            scalar(admin, "SELECT driftread.apply_cleanup_batch('feeds',ARRAY[%s]::uuid[],'stage',false)", (feed,))
            admin.commit()
            with admin.cursor() as cur:
                cur.execute("SET statement_timeout='100ms'")
            with pytest.raises(psycopg2.errors.QueryCanceled):
                scalar(admin, "SELECT driftread.apply_cleanup_batch('feeds',ARRAY[%s]::uuid[],'delete',false,true)", (feed,))
        with connection(pg_database) as observer:
            assert scalar(observer, 'SELECT count(*) FROM driftread.articles WHERE id=%s', (article,)) == 1
        report = apply_batch(Mock(spec=['rpc']), body)
        assert report['items'][0]['ok']
        assert report['items'][0]['impact']['articles'] == 1
        with connection(pg_database) as observer:
            assert scalar(observer, 'SELECT count(*) FROM driftread.feeds WHERE id=%s', (feed,)) == 0
            assert scalar(observer, 'SELECT count(*) FROM driftread.articles WHERE id=%s', (article,)) == 0
            assert scalar(observer, 'SELECT count(*) FROM driftread.article_revisions WHERE article_id=%s', (article,)) == 0
    finally:
        with connection(pg_database) as teardown:
            with teardown.cursor() as cur:
                cur.execute('DROP TRIGGER test_slow_cleanup ON driftread.feeds; DROP FUNCTION driftread.test_slow_cleanup()')
            teardown.commit()


def test_cleanup_indexes_replay_and_cover_cascade_keys(pg_database):
    migration = Path(__file__).parents[1] / 'migrations/20261020132503_cleanup_cascade_indexes.sql'
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute(migration.read_text())
            cur.execute(migration.read_text())
        for table, column in [('discovery_targets', 'source_feed_id'),
                              ('discovery_candidates', 'feed_id'), ('article_discoveries', 'feed_id'),
                              ('user_feeds', 'feed_id'), ('user_article_reads', 'article_id'),
                              ('user_bookmarks', 'article_id'), ('user_feed_feedback', 'feed_id')]:
            assert scalar(conn, """SELECT EXISTS(SELECT 1 FROM pg_index i
                JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=i.indkey[0]
                WHERE i.indrelid=%s::regclass AND a.attname=%s AND i.indisvalid
                 AND i.indpred IS NULL)""", (f'driftread.{table}', column))


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_cleanup_is_not_public(pg_database, role):
    statements = [
        "SELECT driftread.list_cleanup_items('feeds')",
        "SELECT driftread.apply_cleanup_batch('feeds',ARRAY[gen_random_uuid()],'stage')",
        "SELECT driftread.remove_discovery_targets(ARRAY[gen_random_uuid()])",
        "SELECT * FROM driftread.article_cleanup_tombstones",
    ]
    for statement in statements:
        with connection(pg_database, role) as conn:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                scalar(conn, statement)


def test_service_role_mutation_and_late_probe_fencing(pg_database):
    from concurrent.futures import ThreadPoolExecutor
    from tests.test_postgres_lifecycle import assert_blocked
    with connection(pg_database) as setup:
        target = scalar(setup, "INSERT INTO driftread.discovery_targets(url,host) VALUES('https://cancel-race.invalid/','cancel-race.invalid') RETURNING id")
        feed = scalar(setup, "INSERT INTO driftread.feeds(title,url) VALUES('service cleanup','https://service-cleanup.invalid/rss') RETURNING id")
        article = scalar(setup, "INSERT INTO driftread.articles(feed_id,title,url) VALUES(%s,'cleanup','https://service-cleanup.invalid/post') RETURNING id", (feed,))
        setup.commit()
    with connection(pg_database, 'service_role') as admin:
        assert scalar(admin, "SELECT driftread.apply_cleanup_batch('articles',ARRAY[%s]::uuid[],'stage',false)", (article,))['items'][0]['ok']
        assert scalar(admin, "SELECT driftread.apply_cleanup_batch('articles',ARRAY[%s]::uuid[],'delete',false,true)", (article,))['items'][0]['ok']
        admin.commit()
    with connection(pg_database, 'service_role') as admin, connection(pg_database, 'service_role') as worker, connection(pg_database) as observer:
        scalar(admin, 'SELECT driftread.remove_discovery_targets(ARRAY[%s]::uuid[])', (target,))
        def late_result():
            with worker.cursor() as cur:
                cur.execute("INSERT INTO driftread.discovery_candidates(target_id,feed_url) VALUES(%s,'https://cancel-race.invalid/rss') RETURNING id", (target,))
                result = cur.fetchone()
            worker.commit()
            return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(late_result)
            try:
                assert_blocked(observer, worker.get_backend_pid(), future)
            finally:
                admin.commit()
            assert future.result(timeout=5) is None
        assert scalar(observer, 'SELECT count(*) FROM driftread.discovery_candidates WHERE target_id=%s', (target,)) == 0


def test_article_delete_serializes_with_ingestion(pg_database):
    from concurrent.futures import ThreadPoolExecutor
    from tests.test_postgres_lifecycle import assert_blocked, seed_article, ingest
    with connection(pg_database) as setup:
        feed, article, url, _ = seed_article(setup)
        scalar(setup, "SELECT driftread.apply_cleanup_batch('articles',ARRAY[%s]::uuid[],'stage',false)", (article,))
        setup.commit()
    with connection(pg_database, 'service_role') as admin, connection(pg_database, 'service_role') as worker, connection(pg_database) as observer:
        scalar(admin, "SELECT driftread.apply_cleanup_batch('articles',ARRAY[%s]::uuid[],'delete',false,true)", (article,))
        def late_ingestion():
            count = ingest(worker, feed, url, 'updated body', 'b' * 64)
            worker.commit()
            return count
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(late_ingestion)
            try:
                assert_blocked(observer, worker.get_backend_pid(), future)
            finally:
                admin.commit()
            assert future.result(timeout=5) == 0
        assert scalar(observer, 'SELECT count(*) FROM driftread.articles WHERE feed_id=%s', (feed,)) == 0
        assert scalar(observer, 'SELECT article_count FROM driftread.feeds WHERE id=%s', (feed,)) == 0


def test_remove_waits_for_inflight_candidate_then_reports_completed(pg_database):
    from concurrent.futures import ThreadPoolExecutor
    from tests.test_postgres_lifecycle import assert_blocked
    with connection(pg_database) as setup:
        target = scalar(setup, "INSERT INTO driftread.discovery_targets(url,host) VALUES('https://finding-first.invalid/','finding-first.invalid') RETURNING id")
        setup.commit()
    with connection(pg_database, 'service_role') as worker, connection(pg_database, 'service_role') as admin, connection(pg_database) as observer:
        with worker.cursor() as cur:
            cur.execute("INSERT INTO driftread.discovery_candidates(target_id,feed_url) VALUES(%s,'https://finding-first.invalid/rss')", (target,))
        def remove():
            result = scalar(admin, 'SELECT driftread.remove_discovery_targets(ARRAY[%s]::uuid[])', (target,))
            admin.commit()
            return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(remove)
            try:
                assert_blocked(observer, admin.get_backend_pid(), task)
            finally:
                worker.commit()
            assert task.result(timeout=5)['items'][0]['error'] == 'target_completed'
        assert scalar(observer, 'SELECT status FROM driftread.discovery_targets WHERE id=%s', (target,)) == 'pending'
