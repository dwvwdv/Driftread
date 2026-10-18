import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar, assert_blocked
from tests.test_source_review_postgres import candidate, ready, submit, POLICY


def set_control(conn, enabled):
    return scalar(conn, "SELECT driftread.save_app_setting('discovery.control',%s::jsonb,(SELECT version FROM driftread.app_settings WHERE key='discovery.control'))", (json.dumps({'enabled': enabled}),))


def test_control_migration_replay_and_version_fencing(pg_database):
    root = Path(__file__).parents[1]
    with connection(pg_database, 'service_role') as conn:
        scalar(conn, 'SELECT driftread.discovery_runtime_enabled()')
        with conn.cursor() as cur:
            cur.execute((root/'tests/sql/test_discovery_control.sql').read_text())
    with connection(pg_database) as conn:
        set_control(conn, False)
        migration = (root/'migrations/20261018034910_discovery_control.sql').read_text()
        with conn.cursor() as cur:
            cur.execute(migration)
            cur.execute(migration)
        assert not scalar(conn, 'SELECT driftread.discovery_runtime_enabled()'), 'migration resumed operator-paused pipeline'


def test_paused_ai_submission_preserved_for_human_and_manual_admission_allowed(pg_database):
    with connection(pg_database, 'service_role') as conn:
        scalar(conn, "SELECT driftread.save_app_setting('source.review',%s::jsonb,coalesce((SELECT version FROM driftread.app_settings WHERE key='source.review'),0))", (json.dumps(POLICY),))
        cid = candidate(conn)
        review = ready(conn, cid)
        set_control(conn, False)
        result = submit(conn, review)
        assert result['status'] == 'awaiting_human'
        assert result['result']['decision'] == 'accept'
        assert scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (cid,)) is None
        assert scalar(conn, "SELECT driftread.admit_source_review(%s,NULL,NULL,'referrer_auto',NULL,NULL)", (cid,)) is None
        assert scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (cid,)) is None
        # Explicit administrator intent remains available while paused.
        result = scalar(conn, "SELECT driftread.judge_source_review(%s,'agree')", (review['id'],))
        assert result['status'] == 'accepted'
        assert scalar(conn, 'SELECT feed_id IS NOT NULL FROM driftread.discovery_candidates WHERE id=%s', (cid,))


def test_pause_serializes_with_local_admission_only(pg_database):
    with connection(pg_database, 'service_role') as admission, connection(pg_database, 'service_role') as operator, connection(pg_database) as observer:
        assert scalar(admission, 'SELECT driftread.discovery_runtime_enabled()') is True
        def pause():
            state = set_control(operator, False)
            operator.commit()
            return state
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(pause)
            try:
                assert_blocked(observer, operator.get_backend_pid(), task)
            finally:
                admission.commit()
            assert task.result(timeout=5)['value']['enabled'] is False
        assert not scalar(observer, 'SELECT driftread.discovery_runtime_enabled()')
        observer.rollback()
        set_control(operator, True)
        operator.commit()


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_runtime_control_rpc_is_private(pg_database, role):
    with connection(pg_database, role) as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            scalar(conn, 'SELECT driftread.discovery_runtime_enabled()')
