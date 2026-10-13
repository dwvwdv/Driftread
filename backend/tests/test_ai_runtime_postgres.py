from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg2
import pytest
from tests.test_postgres_lifecycle import pg_database, connection, scalar


@pytest.fixture(autouse=True)
def clean_receipts(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute('TRUNCATE driftread.ai_request_receipts CASCADE')
        conn.commit()


def reserve(conn, key=None, cost='.01', minute='.1', hour='.5', day='1'):
    return scalar(conn, 'SELECT driftread.reserve_ai_request(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (key or uuid4().hex, 'source_review', 'jev-1.13.0', 'input', 'policy', 'config', cost, minute, hour, day))


def test_sql_fixture_and_migration_replay(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql/test_ai_runtime.sql').read_text())
            cur.execute((Path(__file__).parents[1] / 'migrations/20261013040020_ai_receipts.sql').read_text())
        conn.commit()


def test_concurrent_reservations_cannot_exceed_budget_or_send_twice(pg_database):
    barrier = Barrier(2)
    def run(key):
        with connection(pg_database, 'service_role') as conn:
            barrier.wait(3)
            result = reserve(conn, key, cost='.06')
            conn.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ['one', 'two']))
    assert sorted(r['action'] for r in results) == ['blocked', 'send']
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute('TRUNCATE driftread.ai_request_receipts CASCADE')
        conn.commit()
    barrier.reset()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ['same', 'same']))
    assert sorted(r['action'] for r in results) == ['blocked', 'send']


def test_raw_receipt_survives_parse_crash_unknown_bound_and_fencing(pg_database):
    with connection(pg_database, 'service_role') as conn:
        first = reserve(conn, 'crash')
        assert scalar(conn, 'SELECT driftread.receive_ai_response(%s,%s,%s,200,25)', (first['receipt_id'], first['token'], '{raw'))
        conn.commit()
        cached = reserve(conn, 'crash')
        assert cached['action'] == 'cached' and cached['receipt']['status'] == 'received'
        other = reserve(conn, 'unknown')
        assert scalar(conn, "SELECT driftread.finish_ai_request(%s,%s,'unknown')", (other['receipt_id'], other['token']))
        second = reserve(conn, 'unknown')
        assert second['action'] == 'send' and second['token'] != other['token']
        assert not scalar(conn, 'SELECT driftread.receive_ai_response(%s,%s,%s,200,1)', (other['receipt_id'], other['token'], '{}'))
        assert scalar(conn, "SELECT driftread.finish_ai_request(%s,%s,'unknown')", (second['receipt_id'], second['token']))
        third = reserve(conn, 'unknown')
        assert third['code'] == 'request_unknown_paused'
        assert scalar(conn, 'SELECT sum(charged_usd) FROM driftread.ai_request_attempts WHERE receipt_id=%s', (second['receipt_id'],)) == Decimal('.02')
        conn.commit()


def test_expired_pending_is_unknown_and_daily_zero_blocks(pg_database):
    with connection(pg_database, 'service_role') as conn:
        first = reserve(conn, 'expire')
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.ai_request_receipts SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (first['receipt_id'],))
        assert reserve(conn, 'expire')['action'] == 'send'
        assert scalar(conn, 'SELECT status FROM driftread.ai_request_attempts WHERE token=%s', (first['token'],)) == 'unknown'
        assert reserve(conn, 'zero', day='0')['code'] == 'budget_exceeded'
        conn.commit()


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_private_real_roles_cannot_read_or_execute(pg_database, role):
    with connection(pg_database, role) as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            reserve(conn)
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            scalar(conn, 'SELECT count(*) FROM driftread.ai_request_attempts')


@pytest.mark.parametrize('limits', [dict(minute='1', hour='.06', day='1'), dict(minute='1', hour='1', day='.06')])
def test_hour_and_day_budgets_independently_block(pg_database, limits):
    with connection(pg_database, 'service_role') as conn:
        assert reserve(conn, 'first', cost='.04', **limits)['action'] == 'send'
        assert reserve(conn, 'second', cost='.04', **limits)['code'] == 'budget_exceeded'
        conn.commit()


def test_retention_erases_payload_preserves_dedup_and_unknown_metadata(pg_database):
    with connection(pg_database, 'service_role') as conn:
        first = reserve(conn, 'old')
        assert scalar(conn, 'SELECT driftread.receive_ai_response(%s,%s,%s,200,3)', (first['receipt_id'], first['token'], '{"body":"private"}'))
        assert scalar(conn, "SELECT driftread.finish_ai_request(%s,%s,'completed','{\"decision\":\"accept\"}',NULL,'{}',.001,'jev-1.13.0')", (first['receipt_id'], first['token']))
        unknown = reserve(conn, 'old_unknown')
        scalar(conn, "SELECT driftread.finish_ai_request(%s,%s,'unknown')", (unknown['receipt_id'], unknown['token']))
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.ai_request_receipts SET updated_at=now()-interval '91 days'")
            cur.execute("UPDATE driftread.ai_request_attempts SET started_at=now()-interval '91 days'")
        assert scalar(conn, 'SELECT driftread.prune_ai_receipts()') == 1
        cached = reserve(conn, 'old')
        assert cached['action'] == 'cached' and cached['receipt']['result'] is None
        assert scalar(conn, 'SELECT raw_response IS NULL FROM driftread.ai_request_attempts WHERE token=%s', (first['token'],))
        assert scalar(conn, 'SELECT count(*) FROM driftread.ai_request_receipts') == 2
        summary = scalar(conn, 'SELECT driftread.ai_usage_summary(1)')
        assert len(summary['recent_requests']) == 1
        assert summary['statuses'] == {'completed': 1, 'unknown': 1}
        assert 'raw_response' not in str(summary)
        conn.commit()


def test_http_transient_second_attempt_is_bounded_but_invalid_output_is_terminal(pg_database):
    with connection(pg_database, 'service_role') as conn:
        first = reserve(conn, 'temporary')
        assert scalar(conn, 'SELECT driftread.receive_ai_response(%s,%s,%s,429,1)', (first['receipt_id'], first['token'], 'rate limited'))
        assert scalar(conn, "SELECT driftread.finish_ai_request(%s,%s,'failed',NULL,'provider_http_retryable')", (first['receipt_id'], first['token']))
        second = reserve(conn, 'temporary')
        assert second['action'] == 'send' and second['attempts'] == 2
        assert scalar(conn, 'SELECT driftread.receive_ai_response(%s,%s,%s,503,1)', (second['receipt_id'], second['token'], 'overloaded'))
        assert scalar(conn, "SELECT driftread.finish_ai_request(%s,%s,'failed',NULL,'provider_http_retryable')", (second['receipt_id'], second['token']))
        assert reserve(conn, 'temporary')['action'] == 'cached'
        bad = reserve(conn, 'invalid')
        assert scalar(conn, 'SELECT driftread.receive_ai_response(%s,%s,%s,200,1)', (bad['receipt_id'], bad['token'], 'invalid json'))
        assert scalar(conn, "SELECT driftread.finish_ai_request(%s,%s,'failed',NULL,'provider_invalid_response')", (bad['receipt_id'], bad['token']))
        assert reserve(conn, 'invalid')['action'] == 'cached'
        assert scalar(conn, 'SELECT count(*) FROM driftread.ai_request_attempts WHERE receipt_id=%s', (bad['receipt_id'],)) == 1
        conn.commit()
