from pathlib import Path

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar


def test_cleanup_workflow_and_replay(pg_database):
    root = Path(__file__).parents[1]
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            migration = (root / 'migrations/20261018033131_admin_cleanup.sql').read_text()
            cur.execute(migration)
            cur.execute(migration)
            cur.execute((root / 'tests/sql/test_admin_cleanup.sql').read_text())


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
