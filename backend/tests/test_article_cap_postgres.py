"""Per-source article cap against real PostgreSQL, including lock races."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / 'migrations/20261022120000_article_cap.sql'


def _seed(conn, label, rows, cap=100):
    feed = scalar(conn, "INSERT INTO driftread.feeds(title,url,article_cap) VALUES(%s,%s,%s) RETURNING id",
                  (label, f'https://{label}.invalid/rss', cap))
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO driftread.articles(feed_id,title,url,content_hash,published_at,
                discovery_extracted_at,discovery_extracted_hash)
            SELECT %s,'item '||i,%s||i,repeat('a',64),now()-make_interval(hours=>i),now(),repeat('a',64)
            FROM generate_series(1,%s) i""", (feed, f'https://{label}.invalid/', rows))
        cur.execute("SELECT driftread.reconcile_feed_article_count(%s)", (feed,))
    conn.commit()
    return feed


def _enforce(conn, dry_run=False):
    return scalar(conn, "SELECT driftread.enforce_article_cap(100000,500,%s,true)", (dry_run,))


def test_article_cap_rules_and_replay(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute(MIGRATION.read_text())
            cur.execute(MIGRATION.read_text())
            cur.execute((ROOT / 'tests/sql/test_article_cap.sql').read_text())


def test_feed_being_refreshed_is_skipped_not_waited_on(pg_database):
    with connection(pg_database) as setup:
        feed = _seed(setup, 'cap-busy', 110)
    with connection(pg_database) as refresh, connection(pg_database, 'service_role') as worker:
        scalar(refresh, "SELECT id FROM driftread.feeds WHERE id=%s FOR UPDATE", (feed,))
        report = _enforce(worker)
        worker.commit()
        assert report['skipped_feeds'] == 1 and report['deleted'] == 0
        refresh.rollback()
        report = _enforce(worker)
        worker.commit()
        assert report['deleted'] == 10
    with connection(pg_database) as observer:
        assert scalar(observer, "SELECT count(*) FROM driftread.articles WHERE feed_id=%s", (feed,)) == 100


def test_bookmark_in_flight_keeps_its_article(pg_database):
    """An uncommitted bookmark holds KEY SHARE on its article: eviction skips
    that row, deletes the rest, and the bookmark commits normally."""
    with connection(pg_database) as setup:
        feed = _seed(setup, 'cap-race', 102)
        oldest, second = (scalar(setup, "SELECT id FROM driftread.articles WHERE feed_id=%s AND url=%s",
                                 (feed, f'https://cap-race.invalid/{n}')) for n in (102, 101))
        owner = scalar(setup, "SELECT id FROM auth.users LIMIT 1")
    with connection(pg_database) as user, connection(pg_database, 'service_role') as worker:
        with user.cursor() as cur:
            cur.execute("INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type) VALUES(%s,%s,'favorite')",
                        (owner, oldest))

        def run():
            report = _enforce(worker)
            worker.commit()
            return report

        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(run)
            # The delete then waits only on the shared sync clock row, which
            # the user's transaction releases on commit.
            time.sleep(0.3)
            user.commit()
            report = pending.result(timeout=5)
        assert report['deleted'] == 1
    with connection(pg_database) as observer:
        assert scalar(observer, "SELECT count(*) FROM driftread.articles WHERE id=%s", (oldest,)) == 1
        assert scalar(observer, "SELECT count(*) FROM driftread.user_bookmarks WHERE article_id=%s", (oldest,)) == 1
        assert scalar(observer, "SELECT count(*) FROM driftread.articles WHERE id=%s", (second,)) == 0


def test_concurrent_runs_do_not_double_delete(pg_database):
    with connection(pg_database) as setup:
        feed = _seed(setup, 'cap-pair', 120)
    with connection(pg_database, 'service_role') as first, connection(pg_database, 'service_role') as second:
        a = _enforce(first)
        b = _enforce(second)  # advisory lock held by the first transaction
        first.commit()
        second.commit()
    assert a['deleted'] >= 20
    assert b == {'dry_run': False, 'feeds_over_cap': 0, 'excess': 0, 'eligible': 0,
                 'deleted': 0, 'skipped_feeds': 0}
    with connection(pg_database) as observer:
        assert scalar(observer, "SELECT article_count FROM driftread.feeds WHERE id=%s", (feed,)) == 100


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_article_cap_is_not_public(pg_database, role):
    with connection(pg_database, role) as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            scalar(conn, "SELECT driftread.enforce_article_cap(1000,10,true,true)")
