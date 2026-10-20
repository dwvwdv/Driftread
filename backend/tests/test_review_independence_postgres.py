"""Review/admission and safe historical reconciliation on real PostgreSQL."""
import json
from pathlib import Path

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar
from tests.test_source_review_postgres import candidate, ready, submit, POLICY
from tests.test_discovery_control_postgres import set_control


def set_policy(conn, **changes):
    return scalar(conn, "SELECT driftread.save_app_setting('source.review',%s::jsonb,coalesce((SELECT version FROM driftread.app_settings WHERE key='source.review'),0))", (json.dumps({**POLICY, **changes}),))


def reconcile(conn, r, dry=True, **changes):
    params = {'policy': r['policy_version'], 'candidate': r['candidate_version'], **changes}
    return scalar(conn, 'SELECT driftread.reconcile_source_review(%s,%s,%s,%s)',
                  (r['id'], params['policy'], params['candidate'], dry))


def legacy_hold(conn):
    """Execute the old, deployed submission function to create authentic evidence."""
    set_policy(conn)
    set_control(conn, False)
    root = Path(__file__).parents[1]
    old = (root/'migrations/20261018034910_discovery_control.sql').read_text()
    new = (root/'migrations/20261020080000_review_independence.sql').read_text()
    with conn.cursor() as cur:
        cur.execute(old)
    r = ready(conn, candidate(conn))
    result = submit(conn, r)
    assert result['status'] == 'awaiting_human'
    with conn.cursor() as cur:
        cur.execute(new)
        # Test transactions use a fixed now(); model actual deployment chronology.
        cur.execute("UPDATE driftread._migrations SET applied_at=now()-interval '1 minute' WHERE filename='20261018034910_discovery_control.sql'")
        cur.execute("UPDATE driftread._migrations SET applied_at=now()+interval '1 minute' WHERE filename='20261020080000_review_independence.sql'")
    return r


def test_reconciliation_previews_versions_preserves_evidence_and_is_idempotent(pg_database):
    with connection(pg_database) as conn:
        r = legacy_hold(conn)
        with conn.cursor() as cur:
            cur.execute('SET LOCAL ROLE service_role')
            cur.execute((Path(__file__).parent/'sql/test_review_independence.sql').read_text())
        assert reconcile(conn, r, policy=r['policy_version']+1)['eligible'] is False
        assert reconcile(conn, r, candidate=r['candidate_version']+1)['eligible'] is False
        before = scalar(conn, 'SELECT to_jsonb(r) FROM driftread.source_reviews r WHERE id=%s', (r['id'],))
        preview = reconcile(conn, r)
        assert preview['reason'] == 'discovery_paused_legacy' and preview['eligible']
        assert scalar(conn, 'SELECT to_jsonb(r) FROM driftread.source_reviews r WHERE id=%s', (r['id'],)) == before
        result = reconcile(conn, r, False)
        assert result['status'] == 'accepted'
        assert scalar(conn, 'SELECT feed_id IS NOT NULL FROM driftread.discovery_candidates WHERE id=%s', (r['candidate_id'],))
        assert scalar(conn, 'SELECT result FROM driftread.source_reviews WHERE id=%s', (r['id'],)) == before['result']
        assert scalar(conn, 'SELECT count(*) FROM driftread.analysis_results WHERE review_id=%s', (r['id'],)) == 1
        assert reconcile(conn, r, False)['reason'] == 'already_reconciled'
        assert scalar(conn, 'SELECT count(*) FROM driftread.feed_admissions WHERE review_id=%s', (r['id'],)) == 1
        assert scalar(conn, 'SELECT driftread.discovery_runtime_enabled()') is False


@pytest.mark.parametrize('block', ['manual', 'expired', 'policy', 'candidate', 'unknown', 'latest'])
def test_reconciliation_never_releases_uncertain_or_invalid_holds(pg_database, block):
    with connection(pg_database) as conn:
        r = legacy_hold(conn)
        if block == 'manual':
            scalar(conn, "SELECT driftread.judge_source_review(%s,'disagree')", (r['id'],))
        elif block == 'expired':
            scalar(conn, "UPDATE driftread.source_reviews SET snapshot_expires_at=now()-interval '1 second' WHERE id=%s RETURNING id", (r['id'],))
        elif block == 'policy':
            set_policy(conn, skip_human_review=False)
        elif block == 'candidate':
            scalar(conn, 'UPDATE driftread.discovery_candidates SET review_version=review_version+1 WHERE id=%s RETURNING id', (r['candidate_id'],))
        elif block == 'unknown':
            scalar(conn, "UPDATE driftread.source_reviews SET updated_at=now()-interval '1 day' WHERE id=%s RETURNING id", (r['id'],))
        else:
            scalar(conn, "INSERT INTO driftread.source_reviews(candidate_id,candidate_version,policy_version,policy,engine,model,status,created_at) SELECT candidate_id,candidate_version,policy_version,policy,engine,model,'deferred',now()+interval '1 second' FROM driftread.source_reviews WHERE id=%s RETURNING id", (r['id'],))
        assert reconcile(conn, r, False)['eligible'] is False
        assert scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (r['candidate_id'],)) is None


@pytest.mark.parametrize('decision,changes,text', [
    ('abstain', {}, 'evidence '*30),
    ('accept', {'confidence': .1}, 'evidence '*30),
    ('accept', {}, 'short'),
    ('accept', {'dimensions': {'quality': .1, 'relevance': .9, 'spam_probability': .01}}, 'evidence '*30),
    ('accept', {'dimensions': {'quality': .9, 'relevance': .1, 'spam_probability': .01}}, 'evidence '*30),
    ('accept', {'dimensions': {'quality': .9, 'relevance': .9, 'spam_probability': .9}}, 'evidence '*30),
])
def test_paused_review_keeps_policy_gates(pg_database, decision, changes, text):
    with connection(pg_database, 'service_role') as conn:
        set_policy(conn)
        set_control(conn, False)
        r = ready(conn, candidate(conn), text)
        assert submit(conn, r, decision, **changes)['status'] == 'deferred'
        assert scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (r['candidate_id'],)) is None


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_reconciliation_rpc_private(pg_database, role):
    with connection(pg_database, role) as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            scalar(conn, 'SELECT driftread.reconcile_source_review(NULL,1,1,true)')


@pytest.mark.parametrize('guard', ['lease', 'candidate', 'manual'])
def test_paused_submission_keeps_fencing(pg_database, guard):
    with connection(pg_database, 'service_role') as conn:
        set_policy(conn)
        set_control(conn, False)
        r = ready(conn, candidate(conn))
        if guard == 'lease':
            scalar(conn, "UPDATE driftread.source_reviews SET lease_expires_at=now()-interval '1 second' WHERE id=%s RETURNING id", (r['id'],))
        elif guard == 'candidate':
            scalar(conn, 'UPDATE driftread.discovery_candidates SET review_version=review_version+1 WHERE id=%s RETURNING id', (r['candidate_id'],))
        else:
            scalar(conn, "SELECT driftread.decide_discovery_candidate(%s,'held',%s,NULL,true)", (r['candidate_id'], r['candidate_version']))
        result = submit(conn, r)
        if guard == 'lease':
            assert result.get('error') == 'lease'
        else:
            assert result['status'] == 'overridden'
        assert scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (r['candidate_id'],)) is None


def test_history_cli_reports_causes_and_applies_selected_preview_only(pg_database):
    import os
    import subprocess
    import sys
    with connection(pg_database) as conn:
        set_policy(conn, skip_human_review=False)
        human = ready(conn, candidate(conn))
        submit(conn, human)
        r = legacy_hold(conn)
        conn.commit()
    script = Path(__file__).parents[2]/'scripts/reconcile_source_review_holds.py'
    env = {**os.environ, 'DATABASE_URL': pg_database}
    preview = json.loads(subprocess.check_output([sys.executable, str(script)], env=env, text=True))
    assert preview['counts'] == {'human_review_required': 1, 'discovery_paused_legacy': 1}
    selected = next(row for row in preview['reviews'] if row['review_id'] == r['id'])
    assert selected['eligible']
    with connection(pg_database) as conn:
        assert scalar(conn, 'SELECT status FROM driftread.source_reviews WHERE id=%s', (r['id'],)) == 'awaiting_human'
    applied = json.loads(subprocess.check_output([
        sys.executable, str(script), '--apply', r['id'], str(selected['policy_version']), str(selected['candidate_version'])
    ], env=env, text=True))
    assert applied['status'] == 'accepted'
    with connection(pg_database) as conn:
        assert scalar(conn, 'SELECT status FROM driftread.source_reviews WHERE id=%s', (human['id'],)) == 'awaiting_human'
