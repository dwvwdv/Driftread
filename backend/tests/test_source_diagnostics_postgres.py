"""Issue #106: recovery is atomic and never creates a paid retry identity."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, RealDictCursor

from tests.test_postgres_lifecycle import pg_database, connection, scalar
from tests.test_source_review_postgres import clean_reviews, candidate, request, ready, submit


class SQLClient:
    """Small PostgREST-shaped adapter; every operation executes real SQL.

    Network responses alone are stubbed in the pipeline test below. This is
    not a replacement for deployment/PostgREST or browser acceptance.
    """
    def __init__(self, conn):
        self.conn = conn

    def rpc(self, name, args):
        def execute():
            query = sql.SQL('SELECT driftread.{}({})').format(sql.Identifier(name), sql.SQL(',').join(
                sql.SQL('{}=>%s').format(sql.Identifier(key)) for key in args))
            params = [Json(value) if isinstance(value, dict) or key == 'p_articles' else value
                      for key, value in args.items()]
            if name == 'find_mirror_sources':
                query = sql.SQL('SELECT * FROM driftread.{}({})').format(sql.Identifier(name), sql.SQL(',').join(
                    sql.SQL('{}=>%s').format(sql.Identifier(key)) for key in args))
                with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, params)
                    return SimpleNamespace(data=list(cur.fetchall()))
            return SimpleNamespace(data=scalar(self.conn, query, params))
        return SimpleNamespace(execute=execute)

    def table(self, name):
        return SQLQuery(self.conn, name)


class SQLQuery:
    def __init__(self, conn, table):
        self.conn, self.table = conn, table
        self.filters, self.sort, self.values = [], [], None
        self.head, self.single, self.maximum = False, False, None

    def select(self, columns, *, count=None, head=False):
        self.columns, self.head = columns.split(','), head
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def update(self, values, **kwargs):
        self.values = values
        return self

    def order(self, column, *, desc=False):
        self.sort.append(sql.SQL('{} {}').format(sql.Identifier(column), sql.SQL('DESC' if desc else 'ASC')))
        return self

    def limit(self, n):
        self.maximum = n
        return self

    def maybe_single(self):
        self.single = True
        return self

    def execute(self):
        where = sql.SQL(' AND ').join(sql.SQL('{}=%s').format(sql.Identifier(key)) for key, _ in self.filters)
        params = [value for _, value in self.filters]
        if self.values is not None:
            assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(key)) for key in self.values)
            query = sql.SQL('UPDATE driftread.{} SET {} WHERE {}').format(sql.Identifier(self.table), assignments, where)
            params = [Json(value) if isinstance(value, dict) else value for value in self.values.values()] + params
        else:
            columns = sql.SQL('count(*) AS count') if self.head else sql.SQL(',').join(map(sql.Identifier, self.columns))
            query = sql.SQL('SELECT {} FROM driftread.{} WHERE {}').format(columns, sql.Identifier(self.table), where)
            if self.sort:
                query += sql.SQL(' ORDER BY ') + sql.SQL(',').join(self.sort)
            if self.maximum is not None:
                query += sql.SQL(' LIMIT %s')
                params.append(self.maximum)
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = list(cur.fetchall()) if cur.description else []
        return SimpleNamespace(data=(rows[0] if rows else None) if self.single else rows,
                               count=rows[0]['count'] if self.head else None)


def test_migration_replay_and_private_queue_summary(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            sql = (Path(__file__).parents[1] / 'migrations/20261016122206_source_diagnostics_recovery.sql').read_text()
            cur.execute(sql)
            cur.execute(sql)
            cur.execute((Path(__file__).parent / 'sql/test_source_diagnostics.sql').read_text())
        conn.commit()
    for role in ('anon', 'authenticated'):
        with connection(pg_database, role) as conn:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                scalar(conn, 'SELECT driftread.source_review_queue_status()')


def test_audit_script_is_executable_as_a_read_only_snapshot(pg_database):
    with connection(pg_database, 'service_role') as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parents[2] / 'scripts/audit_source_pipeline.sql').read_text())


def test_ordinary_request_recovers_stale_and_old_policy_without_paid_key(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        old = request(conn, cid)
        assert old['request_action'] == 'enqueued'
        assert request(conn, cid)['request_action'] == 'reused'
        scalar(conn, "UPDATE driftread.app_settings SET version=version+1 WHERE key='source.review' RETURNING version")
        new = request(conn, cid)
        assert new['id'] != old['id'] and new['request_action'] == 'requeued'
        assert new['policy_version'] > old['policy_version'] and new['paid_retry_key'] is None
        assert scalar(conn, 'SELECT status FROM driftread.source_reviews WHERE id=%s', (old['id'],)) == 'stale'
        assert request(conn, cid)['id'] == new['id']
        scalar(conn, "UPDATE driftread.source_reviews SET status='stale' WHERE id=%s RETURNING id", (new['id'],))
        third = request(conn, cid)
        assert third['id'] != new['id'] and third['paid_retry_key'] is None
        assert scalar(conn, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s', (cid,)) == 3


def test_missing_jev_key_can_transfer_to_external_agent_but_other_failures_do_not(pg_database):
    with connection(pg_database, 'service_role') as conn:
        ids = []
        for error in ('provider_key_missing', 'sample_fetch_failed', 'provider_outcome_unknown'):
            cid = candidate(conn)
            old = request(conn, cid)
            scalar(conn, "UPDATE driftread.source_reviews SET engine='jev',status='failed',error_code=%s WHERE id=%s RETURNING id", (error, old['id']))
            new = request(conn, cid)
            ids.append(cid)
            if error == 'provider_key_missing':
                assert new['id'] != old['id'] and new['engine'] == 'external_agent'
                assert new['paid_retry_key'] is None and new['snapshot'] is None
                assert scalar(conn, 'SELECT error_code FROM driftread.source_reviews WHERE id=%s', (old['id'],)) == error
            else:
                assert new['id'] == old['id'] and new['request_action'] == 'reused'
        summary = scalar(conn, 'SELECT driftread.source_review_queue_status()')
        assert summary['latest_pending']['failed'] == 2
        assert summary['latest_pending']['requested'] == 1
        assert summary['provider_key_missing'] == 0


def test_current_jev_failure_stays_failed_and_human_results_stay_authoritative(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        old = request(conn, cid)
        scalar(conn, "UPDATE driftread.source_reviews SET status='failed',error_code='provider_key_missing' WHERE id=%s RETURNING id", (old['id'],))
        assert request(conn, cid)['id'] == old['id']  # same engine, never automatic paid retry
        human_id = candidate(conn)
        human = ready(conn, human_id)
        scalar(conn, "UPDATE driftread.app_settings SET value=jsonb_set(value,'{skip_human_review}','false') WHERE key='source.review' RETURNING version")
        # The setting trigger advances version: prepare a new task under it.
        human = ready(conn, human_id)
        assert submit(conn, human)['status'] == 'awaiting_human'
        scalar(conn, "UPDATE driftread.app_settings SET version=version+1 WHERE key='source.review' RETURNING version")
        assert request(conn, human_id)['status'] == 'awaiting_human'


def test_concurrent_stale_requests_create_only_one_new_task(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        old = request(conn, cid)
        scalar(conn, "UPDATE driftread.source_reviews SET status='stale' WHERE id=%s RETURNING id", (old['id'],))
        conn.commit()
    def enqueue(_):
        with connection(pg_database, 'service_role') as conn:
            result = request(conn, cid)
            conn.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enqueue, range(2)))
    assert results[0]['id'] == results[1]['id']
    assert {r['request_action'] for r in results} == {'requeued', 'reused'}
    assert all(r['paid_retry_key'] is None for r in results)


@pytest.mark.parametrize('include_real_article,expected', [(False, 'deferred'), (True, 'accepted')])
def test_default_demo_samples_cannot_auto_admit_but_real_samples_can(pg_database, include_real_article, expected):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = request(conn, cid)
        prep = scalar(conn, "SELECT driftread.claim_source_review('preparer',true,%s)", (r['id'],))
        # A legacy packet has no hint; SQL independently checks its evidence.
        samples = [{'title': 'Hello world!', 'text': 'Welcome to WordPress! This is your first post. Edit or delete it to take the first step in your blogging journey.'}]
        if include_real_article:
            samples.append({'text': 'A useful original article. ' * 10})
        scalar(conn, 'SELECT driftread.prepare_source_review(%s,%s,%s::jsonb,%s)',
               (r['id'], prep['lease_token'], json.dumps({'samples': samples}), 'a' * 64))
        leased = scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (r['id'],))
        result = submit(conn, leased)
        assert result['status'] == expected
        if not include_real_article:
            assert result['error_code'] == 'default_demo_content'
            assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'pending'


@pytest.mark.asyncio
async def test_candidate_review_admission_refresh_ingestion_and_public_detail(pg_database, monkeypatch):
    from rss_parser import ConditionalFetch, parse_feed
    from services.feed_refresh import refresh_one
    from routers.feeds import get_feed
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'false')  # ingestion must not discover new URLs
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        review = ready(conn, cid)
        assert submit(conn, review)['status'] == 'accepted'
        feed = scalar(conn, 'SELECT to_jsonb(f) FROM driftread.feeds f JOIN driftread.discovery_candidates c ON c.feed_id=f.id WHERE c.id=%s', (cid,))
        assert feed['article_count'] == 0 and feed['last_fetched_at'] is None
        host = uuid4().hex + '.example'
        parsed = parse_feed('<rss><channel><title>Pipeline</title>' + ''.join(
            f'<item><title>Article {i}</title><link>https://{host}/{i}</link><description>Original article content.</description></item>'
            for i in range(6)) + '</channel></rss>')
        db = SQLClient(conn)
        with (patch('services.feed_refresh.validate_fetch_url', side_effect=lambda url: url),
              patch('services.feed_refresh.fetch_and_parse_conditional', return_value=ConditionalFetch(not_modified=False, parsed=parsed))):
            result = await refresh_one(db, feed)
        assert result.status == 'updated' and result.new_articles == result.total_articles == 6
        public = await get_feed(UUID(feed['id']), db)
        assert public.article_count == len(public.articles) == 6
        assert public.admitted_at is not None
        assert public.last_fetch_result['outcome'] == 'updated'
        assert scalar(conn, 'SELECT count(*) FROM driftread.articles WHERE feed_id=%s', (feed['id'],)) == 6
