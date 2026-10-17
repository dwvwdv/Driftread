"""#106: durable batches and the catalog count commit together."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg2
import pytest
from psycopg2.extras import Json

from tests.test_postgres_lifecycle import pg_database, connection, scalar, assert_blocked
from tests.test_source_diagnostics_postgres import SQLClient


def feed(conn):
    return str(scalar(conn, "INSERT INTO driftread.feeds(title,url) VALUES('count fixture',%s) RETURNING id",
                      (f'https://{uuid4().hex}.example/feed',)))


def ingest(conn, fid, suffix='one', **overrides):
    row = dict(title=suffix, url=f'https://example.test/{suffix}', content='body', content_hash='a'*64)
    row.update(overrides)
    return scalar(conn, 'SELECT driftread.ingest_article_batch(%s,%s)', (fid, Json([row])))


def counts(conn, fid):
    return scalar(conn, '''SELECT jsonb_build_array(f.article_count,
      (SELECT count(*) FROM driftread.articles WHERE feed_id=f.id)) FROM driftread.feeds f WHERE id=%s''', (fid,))


def test_batch_count_is_atomic_and_unchanged_ingestion_does_not_inflate_it(pg_database):
    with connection(pg_database, 'service_role') as conn:
        fid = feed(conn)
        assert ingest(conn, fid) == 1
        assert counts(conn, fid) == [1, 1]
        assert ingest(conn, fid) == 0
        assert counts(conn, fid) == [1, 1]
        assert ingest(conn, fid, content='revision', content_hash='b'*64) == 1
        assert counts(conn, fid) == [1, 1]
        conn.rollback()
        assert scalar(conn, 'SELECT count(*) FROM driftread.feeds WHERE id=%s', (fid,)) == 0


def test_later_batch_failure_cannot_hide_committed_articles(pg_database):
    with connection(pg_database, 'service_role') as conn:
        conn.autocommit = True
        fid = feed(conn)
        assert ingest(conn, fid) == 1
        with pytest.raises(psycopg2.Error):
            ingest(conn, fid, 'two', content_hash='invalid')
        assert counts(conn, fid) == [1, 1]
        scalar(conn, 'DELETE FROM driftread.feeds WHERE id=%s RETURNING id', (fid,))


def test_reconcile_repairs_history_and_has_no_public_execute(pg_database):
    with connection(pg_database) as conn:
        migration = Path(__file__).parents[1] / 'migrations/20261017135504_atomic_feed_article_counts.sql'
        with conn.cursor() as cur:
            cur.execute(migration.read_text())
            cur.execute(migration.read_text())
            cur.execute((Path(__file__).parent / 'sql/test_feed_counts.sql').read_text())
        conn.commit()
    with connection(pg_database, 'service_role') as conn:
        fid = feed(conn)
        ingest(conn, fid)
        scalar(conn, 'UPDATE driftread.feeds SET article_count=999 WHERE id=%s RETURNING id', (fid,))
        assert scalar(conn, 'SELECT driftread.reconcile_feed_article_count(%s)', (fid,)) == 1
        assert counts(conn, fid) == [1, 1]
        empty = feed(conn)
        scalar(conn, 'UPDATE driftread.feeds SET article_count=42 WHERE id=%s RETURNING id', (empty,))
        assert scalar(conn, 'SELECT driftread.reconcile_feed_article_count(%s)', (empty,)) == 0
    for role in ('anon', 'authenticated'):
        with connection(pg_database, role) as conn:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                scalar(conn, 'SELECT driftread.reconcile_feed_article_count(%s)', (str(uuid4()),))


def test_reconcile_waits_for_inflight_ingestion_then_counts_committed_rows(pg_database):
    with connection(pg_database, 'service_role') as writer:
        fid = feed(writer)
        writer.commit()
        ingest(writer, fid)
        with connection(pg_database, 'service_role') as repair, connection(pg_database) as observer:
            pid = scalar(repair, 'SELECT pg_backend_pid()')
            def run_repair():
                value = scalar(repair, 'SELECT driftread.reconcile_feed_article_count(%s)', (fid,))
                repair.commit()
                return value
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(run_repair)
                try:
                    assert_blocked(observer, pid, future)
                finally:
                    writer.commit()
                assert future.result() == 1
            assert counts(observer, fid) == [1, 1]
        scalar(writer, 'DELETE FROM driftread.feeds WHERE id=%s RETURNING id', (fid,))
        writer.commit()


@pytest.mark.asyncio
async def test_ingested_rows_survive_metadata_failure_with_count_and_recover(pg_database):
    from rss_parser import ConditionalFetch, ParsedArticle, ParsedFeed
    from services.feed_refresh import refresh_one
    with connection(pg_database, 'service_role') as conn:
        conn.autocommit = True  # each adapter operation commits, like PostgREST
        fid = feed(conn)
        parsed = ParsedFeed(title='fixture', url='https://example.test', language='en', articles=[
            ParsedArticle(title='article', url='https://example.test/one', content='original text')])
        row = {'id': fid, 'url': 'https://example.test/feed', 'article_count': 0,
               'admitted_at': '2026-10-17T00:00:00Z', 'etag': 'old'}
        fetched = ConditionalFetch(not_modified=False, parsed=parsed, etag='new')
        db = SQLClient(conn)
        with patch('services.feed_refresh.validate_fetch_url', side_effect=lambda url: url), \
             patch('services.feed_refresh.fetch_and_parse_conditional', return_value=fetched), \
             patch('services.articles.discovery_enabled', return_value=False), \
             patch('services.feed_refresh.resolve_feed_language', side_effect=RuntimeError('secret=DO_NOT_STORE')):
            result = await refresh_one(db, row)
        assert result.status == 'failed'
        assert counts(conn, fid) == [1, 1]
        failed = scalar(conn, 'SELECT to_jsonb(f) FROM driftread.feeds f WHERE id=%s', (fid,))
        assert failed['last_fetch_result']['stage'] == 'language'
        assert failed['last_failure_reason'] == 'ingest_failed'
        assert failed['etag'] is None
        assert 'DO_NOT_STORE' not in str(failed)
        with patch('services.feed_refresh.validate_fetch_url', side_effect=lambda url: url), \
             patch('services.feed_refresh.fetch_and_parse_conditional', return_value=fetched) as fetch, \
             patch('services.articles.discovery_enabled', return_value=False):
            result = await refresh_one(db, failed)
        assert fetch.call_args.kwargs == {'etag': None, 'last_modified': None}
        assert result.status == 'updated' and result.total_articles == 1
        assert counts(conn, fid) == [1, 1]
        assert scalar(conn, 'SELECT last_failure_reason FROM driftread.feeds WHERE id=%s', (fid,)) is None
        scalar(conn, 'DELETE FROM driftread.feeds WHERE id=%s RETURNING id', (fid,))
