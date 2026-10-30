"""Whole-row TTL against real PostgreSQL, including independent lock races."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

import psycopg2
import pytest

from tests.test_postgres_lifecycle import connection, pg_database, scalar

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "migrations/20261030010000_article_ttl.sql"


@pytest.fixture(autouse=True)
def clean_ttl_data(pg_database):
    """Each module owns a disposable local DB; leave other fixture data alone."""
    yield
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM driftread.feeds WHERE url LIKE 'https://ttl-%'")
            cur.execute("DELETE FROM driftread.worker_runs WHERE kind='article_ttl'")
            cur.execute("UPDATE driftread.app_settings SET value='{\"enabled\":false,\"days\":14}' WHERE key='article.ttl'")
        conn.commit()


def _seed(conn, label, rows=3, undated=False):
    feed = scalar(conn, "INSERT INTO driftread.feeds(title,url) VALUES(%s,%s) RETURNING id",
                  (label, f"https://ttl-{label}.invalid/rss"))
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO driftread.articles(feed_id,title,url,published_at,discovered_at,content_hash)
            SELECT %s,'old '||i,%s||i,
             CASE WHEN %s THEN NULL ELSE now()-interval '30 days'-make_interval(hours=>i) END,
             now()-interval '30 days'-make_interval(hours=>i),repeat('a',64)
            FROM generate_series(1,%s) i""", (feed, f"https://ttl-{label}.invalid/", undated, rows))
        cur.execute("SELECT driftread.reconcile_feed_article_count(%s)", (feed,))
    conn.commit()
    return feed


def _enforce(conn, limit=500, dry_run=False):
    return scalar(conn, "SELECT driftread.enforce_article_ttl(14,%s,%s)", (limit, dry_run))


def test_ttl_rules_and_migration_replay(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute(MIGRATION.read_text())
            cur.execute(MIGRATION.read_text())
            cur.execute((ROOT / "tests/sql/test_article_ttl.sql").read_text())


def test_replay_preserves_operator_enabled_value(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.app_settings SET value='{\"enabled\":true,\"days\":21}' WHERE key='article.ttl'")
            cur.execute(MIGRATION.read_text())
        assert scalar(conn, "SELECT value FROM driftread.app_settings WHERE key='article.ttl'") == {"enabled": True, "days": 21}


def test_refreshing_feed_skipped_and_catches_up_next_batch(pg_database):
    with connection(pg_database) as setup:
        feed = _seed(setup, "busy")
    with connection(pg_database) as refresh, connection(pg_database, "service_role") as worker:
        scalar(refresh, "SELECT id FROM driftread.feeds WHERE id=%s FOR UPDATE", (feed,))
        report = _enforce(worker)
        worker.commit()
        assert report["skipped_feeds"] == 1 and report["deleted"] == 0
        refresh.rollback()
        assert _enforce(worker, limit=2)["deleted"] == 2
        worker.commit()
        assert _enforce(worker)["deleted"] == 1
        worker.commit()


@pytest.mark.parametrize("kind", ["favorite", "read_later", "curation", "staging"])
def test_in_flight_protection_keeps_article(pg_database, kind):
    with connection(pg_database) as setup:
        feed = _seed(setup, "race-" + kind, rows=2)
        article = scalar(setup, "SELECT id FROM driftread.articles WHERE feed_id=%s ORDER BY timeline_at LIMIT 1", (feed,))
        owner = scalar(setup, "SELECT id FROM auth.users LIMIT 1")
        fact = scalar(setup, "INSERT INTO driftread.event_objects(kind,title) VALUES('fact','TTL race') RETURNING id")
        setup.commit()
    with connection(pg_database) as user, connection(pg_database, "service_role") as worker:
        with user.cursor() as cur:
            if kind in {"favorite", "read_later"}:
                cur.execute("INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type) VALUES(%s,%s,%s)",
                            (owner, article, kind))
            elif kind == "curation":
                cur.execute("INSERT INTO driftread.fact_articles(fact_id,article_id) VALUES(%s,%s)", (fact, article))
            else:
                cur.execute("UPDATE driftread.articles SET staged_at=now() WHERE id=%s", (article,))

        def run():
            report = _enforce(worker)
            worker.commit()
            return report

        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(run)
            # Deletion's sync invalidation may wait on the transaction's shared
            # sync clock row, but the protected article itself must be skipped.
            time.sleep(0.2)
            user.commit()
            report = pending.result(timeout=5)
        assert report["deleted"] == 1
    with connection(pg_database) as observer:
        assert scalar(observer, "SELECT count(*) FROM driftread.articles WHERE id=%s", (article,)) == 1


def test_two_ttl_runs_do_not_double_delete(pg_database):
    with connection(pg_database) as setup:
        _seed(setup, "pair")
    with connection(pg_database, "service_role") as first, connection(pg_database, "service_role") as second:
        a = _enforce(first)
        b = _enforce(second)
        first.commit()
        second.commit()
    assert a["deleted"] == 3 and b["deleted"] == 0 and b["eligible"] == 0


@pytest.mark.parametrize("first_kind", ["ttl", "cap"])
def test_cap_ttl_overlap_skip_same_feed_without_deadlock(pg_database, first_kind):
    with connection(pg_database) as setup:
        feed = _seed(setup, "cap-overlap", rows=110)
        with setup.cursor() as cur:
            cur.execute("UPDATE driftread.feeds SET article_cap=100 WHERE id=%s", (feed,))
        setup.commit()
    with connection(pg_database, "service_role") as first, connection(pg_database, "service_role") as second:
        cap = lambda conn: scalar(conn, "SELECT driftread.enforce_article_cap(1000,5,false,false)")
        if first_kind == "ttl":
            assert _enforce(first, limit=5)["deleted"] == 5
            assert cap(second)["skipped_feeds"] == 1
        else:
            assert cap(first)["deleted"] == 5
            assert _enforce(second)["skipped_feeds"] == 1
        first.commit()
        second.commit()


def test_only_protected_expired_feed_is_not_locked(pg_database):
    with connection(pg_database) as setup:
        feed = _seed(setup, "protected", rows=1)
        owner = scalar(setup, "SELECT id FROM auth.users LIMIT 1")
        with setup.cursor() as cur:
            cur.execute("INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type) SELECT %s,id,'favorite' FROM driftread.articles WHERE feed_id=%s", (owner, feed))
        setup.commit()
    with connection(pg_database, "service_role") as worker, connection(pg_database) as refresh:
        assert _enforce(worker)["deleted"] == 0
        with refresh.cursor() as cur:
            cur.execute("SET lock_timeout='200ms'")
            cur.execute("SELECT id FROM driftread.feeds WHERE id=%s FOR UPDATE", (feed,))


def test_undated_cache_is_bounded_and_ignored_when_disabled(pg_database):
    with connection(pg_database) as conn:
        feed = _seed(conn, "cache", rows=1002, undated=True)
        assert _enforce(conn, limit=1000)["deleted"] == 1000
        conn.commit()
        assert _enforce(conn)["deleted"] == 2
        conn.commit()
        assert scalar(conn, "SELECT cardinality(url_hashes) FROM driftread.article_ttl_reingest_guard WHERE feed_id=%s", (feed,)) == 1000
        payload = json.dumps([{"title": "repeated", "url": "https://ttl-cache.invalid/1", "content_hash": "a" * 64}])
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.app_settings SET value='{\"enabled\":true,\"days\":14}' WHERE key='article.ttl'")
        assert scalar(conn, "SELECT driftread.ingest_article_batch(%s,%s::jsonb)", (feed, payload)) == 0
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.app_settings SET value='{\"enabled\":false,\"days\":14}' WHERE key='article.ttl'")
        assert scalar(conn, "SELECT driftread.ingest_article_batch(%s,%s::jsonb)", (feed, payload)) == 1
        conn.commit()


def test_activity_is_weighted_completed_worker_aggregate(pg_database):
    with connection(pg_database) as conn:
        assert scalar(conn, "SELECT driftread.article_ttl_activity(now()-interval '24 hours')") == {"deleted": 0, "average_delay_seconds": None}
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO driftread.worker_runs(id,worker_id,kind,status,started_at,finished_at,summary)
              VALUES(gen_random_uuid(),'ttl-test','article_ttl','succeeded',now(),now(),'{"deleted":2,"deletion_delay_seconds":100}'),
              (gen_random_uuid(),'ttl-test','article_ttl','succeeded',now(),now(),'{"deleted":3,"deletion_delay_seconds":200}'),
              (gen_random_uuid(),'ttl-test','article_ttl','failed',now(),now(),'{"deleted":100,"deletion_delay_seconds":1000}'),
              (gen_random_uuid(),'ttl-test','article_ttl','succeeded',now()-interval '2 days',now()-interval '2 days','{"deleted":100,"deletion_delay_seconds":1000}')""")
        assert scalar(conn, "SELECT driftread.article_ttl_activity(now()-interval '24 hours')") == {"deleted": 5, "average_delay_seconds": 60}


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("statement", [
    "SELECT driftread.enforce_article_ttl(14,10,true)",
    "SELECT driftread.article_ttl_candidates(gen_random_uuid(),now(),10)",
    "SELECT driftread.article_ttl_activity(now())",
    "SELECT * FROM driftread.article_ttl_reingest_guard",
])
def test_ttl_functions_and_cache_are_private(pg_database, role, statement):
    with connection(pg_database, role) as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            scalar(conn, statement)
