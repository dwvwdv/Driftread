"""Bounded article revision history (#82) against the actual migration chain."""
from pathlib import Path
from uuid import uuid4
import json

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar

MIGRATION = Path(__file__).parents[1] / 'migrations/20261012000000_article_revision_retention.sql'
KEEP = 10


def _feed(conn, label):
    return scalar(conn, 'INSERT INTO driftread.feeds(title,url) VALUES(%s,%s) RETURNING id',
                  (label, f'https://{uuid4().hex}.example/rss'))


def _ingest(conn, feed, url, i):
    # hnrss-style: the same item returns with a different summary every refresh.
    payload = json.dumps([{'url': url, 'title': 'post', 'summary': f'Points: {i}',
                           'content_hash': f'{i:064x}'}])
    return scalar(conn, 'SELECT driftread.ingest_article_batch(%s,%s::jsonb)', (feed, payload))


def _revisions(conn, article):
    with conn.cursor() as cur:
        cur.execute('SELECT revision_number FROM driftread.article_revisions '
                    'WHERE article_id=%s ORDER BY revision_number', (article,))
        return [row[0] for row in cur.fetchall()]


def test_ingestion_keeps_only_latest_revisions_per_article(pg_database):
    with connection(pg_database) as conn:
        feed = _feed(conn, 'dynamic')
        conn.commit()
    churn = f'https://{uuid4().hex}.example/churn'
    stable = f'https://{uuid4().hex}.example/stable'
    with connection(pg_database, 'service_role') as conn:
        _ingest(conn, feed, stable, 1)
        for i in range(1, 16):
            assert _ingest(conn, feed, churn, i) == 1
            conn.commit()
        article = scalar(conn, 'SELECT id FROM driftread.articles WHERE feed_id=%s AND url=%s', (feed, churn))
        assert _revisions(conn, article) == list(range(16 - KEEP, 16))
        assert scalar(conn, 'SELECT revision_number FROM driftread.articles WHERE id=%s', (article,)) == 15
        assert scalar(conn, """SELECT r.summary FROM driftread.article_revisions r
                               JOIN driftread.articles a ON a.current_revision_id=r.id
                               WHERE a.id=%s""", (article,)) == 'Points: 15'
        other = scalar(conn, 'SELECT id FROM driftread.articles WHERE feed_id=%s AND url=%s', (feed, stable))
        assert _revisions(conn, other) == [1]


def test_prune_is_not_callable_and_history_stays_append_only(pg_database):
    with connection(pg_database) as conn:
        for role in ('anon', 'authenticated', 'service_role'):
            assert not scalar(conn, "SELECT has_function_privilege(%s,'driftread.prune_article_revisions()','EXECUTE')",
                              (role,))
    with connection(pg_database, 'service_role') as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute('DELETE FROM driftread.article_revisions')


def test_migration_trims_existing_excess_and_replays_safely(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            # A database that applied 20261009000100 before this migration.
            cur.execute('DROP TRIGGER article_revision_prune ON driftread.article_revisions')
        feed = _feed(conn, 'legacy-excess')
        url = f'https://{uuid4().hex}.example/post'
        for i in range(1, 15):
            _ingest(conn, feed, url, i)
        article = scalar(conn, 'SELECT id FROM driftread.articles WHERE feed_id=%s', (feed,))
        assert len(_revisions(conn, article)) == 14
        for _ in range(2):
            with conn.cursor() as cur:
                cur.execute(MIGRATION.read_text())
            assert _revisions(conn, article) == list(range(15 - KEEP, 15))
            assert scalar(conn, """SELECT count(*) FROM pg_trigger WHERE tgname='article_revision_prune'
                                   AND tgrelid='driftread.article_revisions'::regclass""") == 1
        conn.commit()
