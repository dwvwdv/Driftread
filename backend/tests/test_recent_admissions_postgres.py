"""Real database coverage of immutable, transactional catalog provenance."""
import json
from pathlib import Path
from uuid import uuid4
import psycopg2
import pytest
from tests.test_postgres_lifecycle import pg_database, connection, scalar
from tests.test_source_review_postgres import candidate, ready, submit, POLICY

@pytest.fixture(autouse=True)
def reset(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute('TRUNCATE driftread.source_reviews,driftread.analysis_results')
            cur.execute('DELETE FROM driftread.discovery_candidates; DELETE FROM driftread.feeds')
        policy(conn)
        conn.commit()

def policy(conn, **changes):
    scalar(conn, "SELECT driftread.save_app_setting('source.review',%s::jsonb,coalesce((SELECT version FROM driftread.app_settings WHERE key='source.review'),0))", (json.dumps({**POLICY, **changes}),))

def ledger(conn, fid):
    return scalar(conn, 'SELECT (SELECT to_jsonb(a) FROM driftread.feed_admissions a WHERE feed_id=%s)', (fid,))

def recent(conn, limit=10):
    return scalar(conn, 'SELECT driftread.list_recent_feed_admissions(%s)', (limit,))

def test_replay_permissions_empty(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parents[1] / 'migrations/20261016140000_recent_admissions.sql').read_text())
            cur.execute((Path(__file__).parent / 'sql/test_recent_admissions.sql').read_text())
        assert recent(conn) == {'items': [], 'limit': 10}
        conn.commit()
    for role in ('anon', 'authenticated'):
        for statement in ('SELECT * FROM driftread.feed_admissions', 'SELECT driftread.list_recent_feed_admissions(10)',
                          'SELECT driftread.admit_source_review(NULL,NULL,NULL,NULL,NULL,NULL)'):
            with connection(pg_database, role) as conn:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege): scalar(conn, statement)

@pytest.mark.parametrize('value', [None, 0, 51])
def test_rpc_limit(pg_database, value):
    with connection(pg_database, 'service_role') as conn:
        with pytest.raises(psycopg2.errors.InvalidParameterValue): recent(conn, value)

@pytest.mark.parametrize('engine', ['external_agent', 'jev'])
def test_ai_atomic_retry_and_distinct_quality_gate(pg_database, engine):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = ready(conn, cid)
        if engine == 'jev':
            scalar(conn, "UPDATE driftread.source_reviews SET engine='jev',reviewer_id='jev' WHERE id=%s RETURNING id", (r['id'],))
        def send():
            result = {'decision': 'accept', 'confidence': .99,
                      'dimensions': {'quality': .9, 'spam_probability': .01, 'relevance': .9}}
            return scalar(conn, 'SELECT driftread.submit_source_review(%s,%s,%s,%s,%s::jsonb)',
                          (r['id'], engine, r['lease_token'], r['snapshot_hash'], json.dumps(result)))
        assert send()['status'] == 'accepted'
        fid = scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (cid,))
        original = ledger(conn, fid)
        assert original['method'] == 'ai_auto' and original['engine'] == engine
        assert original['review_id'] == r['id'] and original['candidate_id'] == str(cid)
        assert send()['status'] == 'accepted'
        assert ledger(conn, fid) == original
        assert scalar(conn, 'SELECT admitted_at IS NULL FROM driftread.feeds WHERE id=%s', (fid,))
        assert scalar(conn, 'SELECT f.created_at=a.admitted_at FROM driftread.feeds f JOIN driftread.feed_admissions a ON a.feed_id=f.id WHERE f.id=%s', (fid,))
        conn.rollback()
        assert scalar(conn, 'SELECT count(*) FROM driftread.feed_admissions') == 0
        assert scalar(conn, 'SELECT count(*) FROM driftread.feeds') == 0

@pytest.mark.parametrize('assessment,decision,method', [('agree','accept','human_agree'), ('disagree','reject','human_disagree')])
def test_human_assessment_and_prune(pg_database, assessment, decision, method):
    with connection(pg_database, 'service_role') as conn:
        policy(conn, skip_human_review=False)
        cid = candidate(conn); r = ready(conn, cid)
        initial = submit(conn, r, decision=decision)
        assert initial['status'] == 'awaiting_human'
        assert scalar(conn, 'SELECT count(*) FROM driftread.feed_admissions') == 0
        judged = scalar(conn, 'SELECT driftread.judge_source_review(%s,%s)', (r['id'], assessment))
        assert judged['status'] == 'accepted' and judged['result'] == initial['result']
        fid = scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (cid,))
        assert ledger(conn, fid)['method'] == method and ledger(conn, fid)['review_id'] == r['id']
        scalar(conn, 'DELETE FROM driftread.source_reviews WHERE id=%s RETURNING id', (r['id'],))
        assert recent(conn)['items'][0]['method'] == method
        assert recent(conn)['items'][0]['review_id'] == r['id']

@pytest.mark.parametrize('manual,method', [(True,'manual'), (False,'referrer_auto')])
def test_promotion_provenance(pg_database, manual, method):
    with connection(pg_database, 'service_role') as conn:
        policy(conn, enabled=False)
        cid = candidate(conn)
        feed = scalar(conn, 'SELECT driftread.promote_discovery_candidate(%s,NULL,NULL,%s)', (cid, manual))
        assert ledger(conn, feed['id'])['method'] == method
        assert ledger(conn, feed['id'])['engine'] is None and ledger(conn, feed['id'])['review_id'] is None

def test_recovered_manual_approval(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        scalar(conn, "UPDATE driftread.discovery_candidates SET status='approved' WHERE id=%s RETURNING id", (cid,))
        feed = scalar(conn, 'SELECT driftread.promote_discovery_candidate(%s)', (cid,))
        assert ledger(conn, feed['id'])['method'] == 'manual'

@pytest.mark.parametrize('suffix', ['', '/'])
def test_existing_alias_does_not_acquire_provenance(pg_database, suffix):
    with connection(pg_database, 'service_role') as conn:
        url = f'https://{uuid4().hex}.example/feed'
        fid = scalar(conn, "INSERT INTO driftread.feeds(title,url,created_at,admitted_at) VALUES('curated',%s,now()-interval '100 days',now()) RETURNING id", (url + suffix,))
        cid = candidate(conn, url); r = ready(conn, cid)
        assert submit(conn, r)['status'] == 'accepted' and ledger(conn, fid) is None
        row = recent(conn)['items'][0]
        assert row['method'] == 'unknown' and row['engine'] is None and row['review_id'] is None
        assert row['admitted_at'] == scalar(conn, 'SELECT to_jsonb(created_at) FROM driftread.feeds WHERE id=%s', (fid,))

def test_reapproval_preserves_first_method(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn); r = ready(conn, cid); submit(conn, r)
        fid = scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (cid,))
        original = ledger(conn, fid)
        scalar(conn, 'SELECT driftread.promote_discovery_candidate(%s,NULL,NULL,true)', (cid,))
        assert ledger(conn, fid) == original

def test_recency_and_projection(pg_database):
    with connection(pg_database, 'service_role') as conn:
        old = scalar(conn, "INSERT INTO driftread.feeds(title,url,created_at,admitted_at) VALUES('old',%s,now()-interval '2 days',now()) RETURNING id", (f'https://{uuid4().hex}.example/feed',))
        new = scalar(conn, "INSERT INTO driftread.feeds(title,url,created_at) VALUES('new',%s,now()-interval '1 day') RETURNING id", (f'https://{uuid4().hex}.example/feed',))
        page = recent(conn, 1)
        assert page['limit'] == 1 and len(page['items']) == 1 and page['items'][0]['feed_id'] == str(new)
        assert [x['feed_id'] for x in recent(conn)['items']] == [str(new), str(old)]
        assert set(page['items'][0]) == {'feed_id','title','feed_url','website_url','admitted_at','method','engine','candidate_id','review_id'}
