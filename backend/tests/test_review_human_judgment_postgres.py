"""Human agreement, opposing decisions and fencing on real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.test_postgres_lifecycle import connection, pg_database, scalar, assert_blocked
from tests.test_source_review_postgres import candidate


def completed(conn, ai='accept', status='awaiting_human'):
    cid = candidate(conn)
    rid = scalar(conn, """INSERT INTO driftread.source_reviews(candidate_id,status,engine,model,
      policy_version,policy,candidate_version,result) VALUES(%s,%s,'external_agent','test',1,'{}',1,
      jsonb_build_object('decision',%s::text,'rationale','original result')) RETURNING id""", (cid, status, ai))
    return cid, rid


def judge(conn, rid, assessment='agree', decision=None):
    return scalar(conn, 'SELECT driftread.judge_source_review(%s,%s,%s)', (rid, assessment, decision))


@pytest.mark.parametrize('ai,status,assessment,decision,final,expected', [
    ('accept', 'awaiting_human', 'agree', None, 'accept', 'imported'),
    ('reject', 'awaiting_human', 'agree', None, 'reject', 'rejected'),
    ('reject', 'rejected', 'agree', None, 'reject', 'rejected'),
    ('abstain', 'deferred', 'agree', None, 'defer', 'held'),
    ('accept', 'awaiting_human', 'disagree', None, 'reject', 'rejected'),
    ('reject', 'rejected', 'disagree', None, 'accept', 'imported'),
    ('accept', 'awaiting_human', 'disagree', 'defer', 'defer', 'held'),
    ('abstain', 'deferred', 'disagree', 'accept', 'accept', 'imported'),
    ('abstain', 'deferred', 'disagree', 'reject', 'reject', 'rejected'),
    ('accept', 'deferred', 'disagree', 'reject', 'reject', 'rejected'),
])
def test_agreement_and_disagreement_preserve_ai_and_record_human(pg_database, ai, status, assessment, decision, final, expected):
    with connection(pg_database, 'service_role') as conn:
        cid, rid = completed(conn, ai, status)
        original = scalar(conn, 'SELECT result FROM driftread.source_reviews WHERE id=%s', (rid,))
        result = judge(conn, rid, assessment, decision)
        assert 'error' not in result
        assert result['result'] == original
        assert result['human_review'] == {'assessment': assessment, 'ai_decision': ai,
                                          'decision': final, 'decided_at': result['human_review']['decided_at']}
        assert result['human_review']['decided_at']
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == expected
        assert scalar(conn, 'SELECT manual_override FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == {'imported': 'approve', 'held': 'hold', 'rejected': 'reject'}[expected]
        assert judge(conn, rid) == {'error': 'conflict'}


@pytest.mark.parametrize('ai,status,assessment,decision,error', [
    ('accept', 'deferred', 'agree', None, 'policy_deferred'),
    ('accept', 'awaiting_human', 'agree', 'accept', 'invalid_judgment'),
    ('accept', 'awaiting_human', 'disagree', 'accept', 'invalid_judgment'),
    ('reject', 'rejected', 'disagree', 'reject', 'invalid_judgment'),
    ('abstain', 'deferred', 'disagree', None, 'invalid_judgment'),
    ('abstain', 'deferred', 'disagree', 'defer', 'invalid_judgment'),
    ('unknown', 'awaiting_human', 'agree', None, 'conflict'),
])
def test_invalid_or_policy_blocked_judgment_never_mutates(pg_database, ai, status, assessment, decision, error):
    with connection(pg_database, 'service_role') as conn:
        cid, rid = completed(conn, ai, status)
        assert judge(conn, rid, assessment, decision) == {'error': error}
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'pending'
        assert scalar(conn, 'SELECT human_review FROM driftread.source_reviews WHERE id=%s', (rid,)) is None
        assert scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (cid,)) is None


@pytest.mark.parametrize('mutation', ['requested', 'preparing', 'ready', 'leased', 'accepted', 'stale', 'failed', 'overridden',
                                      'no_result', 'json_null', 'bad_decision', 'changed', 'newer', 'manual'])
def test_judgment_rejects_unfinished_stale_or_overridden_review(pg_database, mutation):
    with connection(pg_database, 'service_role') as conn:
        cid, rid = completed(conn)
        if mutation in {'no_result', 'json_null', 'bad_decision'}:
            value = {'no_result': None, 'json_null': 'null', 'bad_decision': '{"decision":"other"}'}[mutation]
            scalar(conn, 'UPDATE driftread.source_reviews SET result=%s::jsonb WHERE id=%s RETURNING id', (value, rid))
        elif mutation == 'changed':
            scalar(conn, "UPDATE driftread.discovery_candidates SET title='new evidence' WHERE id=%s RETURNING id", (cid,))
        elif mutation == 'newer':
            scalar(conn, """INSERT INTO driftread.source_reviews(candidate_id,status,engine,model,policy_version,policy,candidate_version,created_at)
             VALUES(%s,'rejected','external_agent','test',1,'{}',1,clock_timestamp()+interval '1 second') RETURNING id""", (cid,))
        elif mutation == 'manual':
            scalar(conn, "UPDATE driftread.discovery_candidates SET manual_override='hold' WHERE id=%s RETURNING id", (cid,))
        else:
            scalar(conn, 'UPDATE driftread.source_reviews SET status=%s WHERE id=%s RETURNING id', (mutation, rid))
        assert judge(conn, rid)['error'] == ('manual_decision' if mutation == 'manual' else 'conflict')
        assert scalar(conn, 'SELECT human_review FROM driftread.source_reviews WHERE id=%s', (rid,)) is None


@pytest.mark.parametrize('final,assessment', [('accept', 'agree'), ('reject', 'disagree'), ('defer', 'disagree')])
def test_legacy_resolution_records_assessment(pg_database, final, assessment):
    with connection(pg_database, 'service_role') as conn:
        _, rid = completed(conn)
        result = scalar(conn, 'SELECT driftread.resolve_source_review(%s,%s)', (rid, final))
        assert result['human_review']['assessment'] == assessment
        assert result['human_review']['decision'] == final


@pytest.mark.parametrize('first', ['human', 'retry'])
def test_judgment_and_retry_serialize_without_lost_decision(pg_database, first):
    with connection(pg_database, 'service_role') as setup:
        scalar(setup, "INSERT INTO driftread.app_settings(key,value) VALUES('source.review','{\"enabled\":true,\"engine\":\"external_agent\"}') ON CONFLICT(key) DO UPDATE SET value=excluded.value RETURNING key")
        cid, rid = completed(setup, 'reject', 'rejected')
        setup.commit()
    with connection(pg_database, 'service_role') as actor, connection(pg_database, 'service_role') as contender, connection(pg_database) as observer:
        pid = scalar(contender, 'SELECT pg_backend_pid()')
        contender.commit()
        def action(conn, kind):
            return judge(conn, rid) if kind == 'human' else scalar(conn, 'SELECT driftread.retry_source_review(%s,%s)', (cid, rid))
        initial = action(actor, first)
        def competing():
            result = action(contender, 'retry' if first == 'human' else 'human')
            contender.commit()
            return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(competing)
            assert_blocked(observer, pid, future)
            actor.commit()
            second = future.result(timeout=5)
        assert 'error' not in initial
        assert second['error'] == ('manual_decision' if first == 'human' else 'conflict')
        assert scalar(observer, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s', (cid,)) == (1 if first == 'human' else 2)


def test_judgment_sql_privileges_and_migration_replay(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            migration = (Path(__file__).parents[1] / 'migrations/20261016090000_review_human_judgment.sql').read_text()
            cur.execute(migration)
            cur.execute(migration)
            cur.execute((Path(__file__).parent / 'sql/test_review_human_judgment.sql').read_text())


def test_human_queue_includes_ai_rejection_and_abstention_but_excludes_unusable_results(pg_database):
    with connection(pg_database, 'service_role') as conn:
        expected = []
        for ai, status, usable in [('accept', 'awaiting_human', True), ('reject', 'rejected', True),
                                   ('abstain', 'deferred', True), ('accept', 'deferred', True),
                                   ('other', 'deferred', False), ('accept', 'failed', False)]:
            cid, _ = completed(conn, ai, status)
            scalar(conn, 'UPDATE driftread.discovery_candidates SET referring_feed_count=60000 WHERE id=%s RETURNING id', (cid,))
            if usable:
                expected.append(str(cid))
        cid, rid = completed(conn)
        scalar(conn, "UPDATE driftread.discovery_candidates SET referring_feed_count=60000,title='new evidence' WHERE id=%s RETURNING id", (cid,))
        cid, rid = completed(conn, 'abstain', 'deferred')
        scalar(conn, 'UPDATE driftread.discovery_candidates SET referring_feed_count=60000 WHERE id=%s RETURNING id', (cid,))
        scalar(conn, 'UPDATE driftread.source_reviews SET result=NULL WHERE id=%s RETURNING id', (rid,))
        queue = scalar(conn, "SELECT driftread.list_review_candidates('awaiting_human',20,0,60000)")
        assert queue['total'] == len(expected)
        assert {row['id'] for row in queue['items']} == set(expected)
        assert all('human_review' in row['latest_review'] for row in queue['items'])
