"""Article history and provenance tested against the actual migration chain."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4
import json

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar


def test_revision_provenance_and_retention_fixture(pg_database):
    with connection(pg_database) as conn:
        conn.commit()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql/test_article_revisions.sql').read_text())


def test_revision_tables_are_private_and_service_history_is_append_only(pg_database):
    for role in ('anon', 'authenticated'):
        for table in ('article_revisions', 'article_discoveries'):
            with connection(pg_database, role) as conn:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    scalar(conn, f'SELECT count(*) FROM driftread.{table}')
    with connection(pg_database, 'service_role') as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute('DELETE FROM driftread.article_revisions')


def test_parallel_same_article_creates_one_identity_revision_and_discovery(pg_database):
    with connection(pg_database) as conn:
        feed = scalar(conn, "INSERT INTO driftread.feeds(title,url) VALUES('parallel',%s) RETURNING id",
                      (f'https://{uuid4().hex}.example/rss',))
        conn.commit()
    ready = Barrier(2)
    url = f'https://{uuid4().hex}.example/post'
    payload = json.dumps([{'url': url, 'title': 'same', 'content_hash': 'e' * 64}])
    def ingest():
        with connection(pg_database, 'service_role') as conn:
            ready.wait(timeout=3)
            changed = scalar(conn, 'SELECT driftread.ingest_article_batch(%s,%s::jsonb)', (feed, payload))
            conn.commit()
            return changed
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: ingest(), range(2)))
    assert sorted(result) == [0, 1]
    with connection(pg_database) as conn:
        article = scalar(conn, 'SELECT id FROM driftread.articles WHERE feed_id=%s', (feed,))
        assert scalar(conn, 'SELECT count(*) FROM driftread.article_revisions WHERE article_id=%s', (article,)) == 1
        assert scalar(conn, 'SELECT count(*) FROM driftread.article_discoveries WHERE article_id=%s', (article,)) == 1


def test_ingestion_rollback_removes_article_history_and_provenance(pg_database):
    with connection(pg_database) as conn:
        feed = scalar(conn, "INSERT INTO driftread.feeds(title,url) VALUES('rollback',%s) RETURNING id",
                      (f'https://{uuid4().hex}.example/rss',))
        conn.commit()
        payload = json.dumps([{'url': f'https://{uuid4().hex}.example/post', 'title': 'rollback', 'content_hash': 'f' * 64}])
        scalar(conn, 'SELECT driftread.ingest_article_batch(%s,%s::jsonb)', (feed, payload))
        article = scalar(conn, 'SELECT id FROM driftread.articles WHERE feed_id=%s', (feed,))
        conn.rollback()
        assert scalar(conn, 'SELECT count(*) FROM driftread.articles WHERE id=%s', (article,)) == 0
        assert scalar(conn, 'SELECT count(*) FROM driftread.article_revisions WHERE article_id=%s', (article,)) == 0
        assert scalar(conn, 'SELECT count(*) FROM driftread.article_discoveries WHERE article_id=%s', (article,)) == 0


def test_legacy_baseline_and_replayed_migration_preserve_versions(pg_database):
    migration = (Path(__file__).parents[1] / 'migrations/20261009000100_article_revisions.sql').read_text()
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute('DROP TRIGGER article_revision_prepare ON driftread.articles')
            cur.execute('DROP TRIGGER article_revision_record ON driftread.articles')
            cur.execute('ALTER TABLE driftread.articles ALTER COLUMN discovered_at DROP NOT NULL, ALTER COLUMN timeline_at DROP NOT NULL')
        feed = scalar(conn, "INSERT INTO driftread.feeds(title,url) VALUES('legacy',%s) RETURNING id",
                      (f'https://{uuid4().hex}.example/rss',))
        article = scalar(conn, """INSERT INTO driftread.articles(feed_id,title,url,content,fetched_at,discovered_at,timeline_at)
                         VALUES(%s,'legacy',%s,'cached',now()-interval '10 days',NULL,NULL) RETURNING id""",
                         (feed, f'https://{uuid4().hex}.example/post'))
        with conn.cursor() as cur:
            cur.execute(migration)
        baseline = scalar(conn, 'SELECT current_revision_id FROM driftread.articles WHERE id=%s', (article,))
        assert scalar(conn, "SELECT backfill AND backfill_reason='legacy_import' AND discovered_at=fetched_at AND timeline_at=fetched_at FROM driftread.articles WHERE id=%s", (article,))
        assert scalar(conn, 'SELECT count(*) FROM driftread.article_revisions WHERE article_id=%s', (article,)) == 1
        with conn.cursor() as cur:
            cur.execute(migration)
        assert scalar(conn, 'SELECT current_revision_id FROM driftread.articles WHERE id=%s', (article,)) == baseline
        assert scalar(conn, 'SELECT count(*) FROM driftread.article_revisions WHERE article_id=%s', (article,)) == 1
