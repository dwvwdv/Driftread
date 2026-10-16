"""Browser retries cannot cancel a worker's newer task or mint another paid key."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from tests.test_postgres_lifecycle import assert_blocked, connection, pg_database, scalar
from tests.test_source_review_postgres import POLICY, candidate, request


@pytest.fixture(autouse=True)
def enable_review(pg_database):
    with connection(pg_database) as conn:
        scalar(conn, "SELECT driftread.save_app_setting('source.review',%s::jsonb,coalesce((SELECT version FROM driftread.app_settings WHERE key='source.review'),0))",
               (json.dumps(POLICY),))
        conn.commit()


def test_retry_fencing_sql(pg_database):
    with connection(pg_database, 'service_role') as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql/test_review_retry_fencing.sql').read_text())


@pytest.mark.parametrize('new_status', ['requested', 'preparing', 'ready', 'leased'])
def test_stale_page_retry_preserves_worker_new_task_and_lease(pg_database, new_status):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        old = request(conn, cid)
        scalar(conn, "UPDATE driftread.source_reviews SET status='stale' WHERE id=%s RETURNING id", (old['id'],))
        assert scalar(conn, 'SELECT driftread.request_due_source_reviews(10)')['requested'] == 1
        worker_task = scalar(conn, """UPDATE driftread.source_reviews r SET status=%s,lease_token=gen_random_uuid(),
          lease_expires_at=now()+interval '10 minutes',snapshot='{"samples":["worker evidence"]}',snapshot_hash='worker-hash'
          WHERE candidate_id=%s AND id<>%s RETURNING to_jsonb(r)""", (new_status, cid, old['id']))
        assert scalar(conn, 'SELECT driftread.retry_source_review(%s,%s)', (cid, old['id'])) == {'error': 'conflict'}
        assert scalar(conn, 'SELECT to_jsonb(r) FROM driftread.source_reviews r WHERE id=%s', (worker_task['id'],)) == worker_task
        assert scalar(conn, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s', (cid,)) == 2
        assert scalar(conn, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s AND paid_retry_key IS NOT NULL', (cid,)) == 0


def test_retry_verifies_latest_review_after_waiting_for_worker_candidate_lock(pg_database):
    with connection(pg_database, 'service_role') as setup:
        cid = candidate(setup)
        old = request(setup, cid)
        scalar(setup, "UPDATE driftread.source_reviews SET status='stale' WHERE id=%s RETURNING id", (old['id'],))
        setup.commit()
    with connection(pg_database, 'service_role') as worker, connection(pg_database, 'service_role') as browser, connection(pg_database) as observer:
        assert scalar(worker, 'SELECT driftread.request_due_source_reviews(10)')['requested'] == 1
        latest = scalar(worker, 'SELECT to_jsonb(r) FROM driftread.source_reviews r WHERE candidate_id=%s ORDER BY created_at DESC,id DESC LIMIT 1', (cid,))
        browser_pid = scalar(browser, 'SELECT pg_backend_pid()')
        browser.commit()

        def browser_retry():
            response = scalar(browser, 'SELECT driftread.retry_source_review(%s,%s)', (cid, old['id']))
            browser.commit()
            return response

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(browser_retry)
            assert_blocked(observer, browser_pid, result)
            worker.commit()
            assert result.result(timeout=5) == {'error': 'conflict'}
        assert scalar(observer, 'SELECT to_jsonb(r) FROM driftread.source_reviews r WHERE id=%s', (latest['id'],)) == latest
        assert scalar(observer, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s AND paid_retry_key IS NOT NULL', (cid,)) == 0
