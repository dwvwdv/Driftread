"""Real PostgreSQL fixtures for review fencing, atomic admission and permissions."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar, assert_blocked

POLICY = {'enabled': True, 'skip_human_review': True, 'engine': 'external_agent', 'model': 'test',
          'min_confidence': .9, 'min_quality': .7, 'max_spam_probability': .2,
          'policy_text': 'Collect useful content', 'sample_size': 5, 'daily_budget_usd': 1}


@pytest.fixture(autouse=True)
def clean_reviews(pg_database):
    with connection(pg_database) as conn:
        scalar(conn, "SELECT driftread.save_app_setting('source.review',%s::jsonb,coalesce((SELECT version FROM driftread.app_settings WHERE key='source.review'),0))", (json.dumps(POLICY),))
        with conn.cursor() as cur:
            cur.execute('TRUNCATE driftread.source_reviews,driftread.analysis_results')
        conn.commit()


def candidate(conn, url=None):
    return scalar(conn, "INSERT INTO driftread.discovery_candidates(feed_url,title,source_host) VALUES(%s,'untrusted','publisher.example') RETURNING id", (url or f'https://{uuid4().hex}.example/feed',))


def request(conn, cid):
    return scalar(conn, 'SELECT driftread.request_source_review(%s,false)', (cid,))


def ready(conn, cid, text='useful evidence ' * 20):
    requested = request(conn, cid)
    r = scalar(conn, "SELECT driftread.claim_source_review('preparer',true,%s)", (requested['id'],))
    assert r['candidate_id'] == str(cid)
    packet = {'source': {'candidate_id': str(cid)}, 'samples': [{'evidence_id': 'rss:0', 'text': text}]}
    digest = 'a' * 64
    scalar(conn, 'SELECT driftread.prepare_source_review(%s,%s,%s::jsonb,%s)',
           (r['id'], r['lease_token'], json.dumps(packet), digest))
    return scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (r['id'],))


def submit(conn, r, decision='accept', **changes):
    result = {'decision': decision, 'confidence': .99,
              'dimensions': {'quality': .9, 'spam_probability': .01, 'relevance': .9},
              'reason_codes': ['informative'], 'rationale': None, 'category': 'tech', 'tags': ['suggested']}
    result.update(changes)
    return scalar(conn, 'SELECT driftread.submit_source_review(%s,%s,%s,%s,%s::jsonb)',
                  (r['id'], 'external_agent', r['lease_token'], r['snapshot_hash'], json.dumps(result)))


def test_fixture_migration_replay_and_no_public_access(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql/test_source_review.sql').read_text())
            cur.execute((Path(__file__).parents[1] / 'migrations/20261013040000_source_review.sql').read_text())
        conn.commit()
    for role in ('anon', 'authenticated'):
        with connection(pg_database, role) as conn:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                scalar(conn, 'SELECT count(*) FROM driftread.source_reviews')
            conn.rollback()
        with connection(pg_database, role) as conn:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                scalar(conn, "SELECT driftread.claim_source_review('external_agent')")
        with connection(pg_database, role) as conn:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                scalar(conn, "SELECT driftread.decide_discovery_candidate(%s,'held',1)", (str(uuid4()),))


def test_atomic_admission_is_idempotent_and_keeps_curated_metadata(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = ready(conn, cid)
        url = scalar(conn, 'SELECT feed_url FROM driftread.discovery_candidates WHERE id=%s', (cid,))
        fid = scalar(conn, "INSERT INTO driftread.feeds(title,url,category,tags,participation_mode,fulltext_policy) VALUES('curated',%s,'original',ARRAY['manual'],'signal_only','summary_only') RETURNING id", (url,))
        first = submit(conn, r)
        assert first['status'] == 'accepted'
        assert submit(conn, r)['id'] == first['id']
        assert scalar(conn, 'SELECT count(*) FROM driftread.analysis_results WHERE review_id=%s', (r['id'],)) == 1
        assert scalar(conn, 'SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == fid
        assert scalar(conn, 'SELECT title||category||tags[1]||participation_mode||fulltext_policy FROM driftread.feeds WHERE id=%s', (fid,)) == 'curatedoriginalmanualsignal_onlysummary_only'
        assert scalar(conn, 'SELECT manual_override FROM driftread.discovery_candidates WHERE id=%s', (cid,)) is None
        conn.commit()


@pytest.mark.parametrize('status,override', [('held', 'hold'), ('rejected', 'reject'), ('approved', 'approve')])
def test_manual_candidate_action_fences_claimed_model(pg_database, status, override):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = ready(conn, cid)
        scalar(conn, 'UPDATE driftread.discovery_candidates SET status=%s,reviewed_at=now() WHERE id=%s RETURNING id', (status, cid))
        assert submit(conn, r)['status'] == 'overridden'
        assert scalar(conn, 'SELECT manual_override FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == override
        assert request(conn, cid)['error'] == 'manual_decision'
        assert scalar(conn, 'SELECT count(*) FROM driftread.feeds WHERE url=(SELECT feed_url FROM driftread.discovery_candidates WHERE id=%s)', (cid,)) == 0
        conn.commit()


@pytest.mark.parametrize('decision', ['held', 'rejected'])
@pytest.mark.parametrize('first', ['human', 'model'])
def test_candidate_human_action_and_model_admission_serialize(pg_database, decision, first):
    """Both orderings wait on the same candidate lock, with no false rejection."""
    with connection(pg_database, 'service_role') as setup:
        cid = candidate(setup)
        host = f'{uuid4().hex}.example'
        tid = scalar(setup, "INSERT INTO driftread.discovery_targets(url,host,status) VALUES(%s,%s,'done') RETURNING id", (f'https://{host}/', host))
        scalar(setup, 'UPDATE driftread.discovery_candidates SET source_host=%s,target_id=%s WHERE id=%s RETURNING id', (host, tid, cid))
        review = ready(setup, cid)
        version = scalar(setup, 'SELECT review_version FROM driftread.discovery_candidates WHERE id=%s', (cid,))
        setup.commit()

    def human(conn):
        return scalar(conn, 'SELECT driftread.decide_discovery_candidate(%s,%s,%s,%s,true)', (cid, decision, version, 'human decision'))

    with connection(pg_database, 'service_role') as actor, connection(pg_database, 'service_role') as contender, connection(pg_database) as observer:
        pid = scalar(contender, 'SELECT pg_backend_pid()')
        contender.commit()
        initial = human(actor) if first == 'human' else submit(actor, review)
        before_feed = scalar(actor, 'SELECT to_jsonb(f) FROM driftread.feeds f WHERE f.id=(SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s)', (cid,)) if first == 'model' else None

        def competing_action():
            result = submit(contender, review) if first == 'human' else human(contender)
            contender.commit()
            return result

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(competing_action)
            assert_blocked(observer, pid, future)
            actor.commit()
            second = future.result(timeout=5)

        if first == 'human':
            assert initial['status'] == decision
            assert second['status'] == 'overridden'
            assert scalar(observer, 'SELECT manual_override FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == ('hold' if decision == 'held' else 'reject')
            assert scalar(observer, 'SELECT count(*) FROM driftread.feeds WHERE url=(SELECT feed_url FROM driftread.discovery_candidates WHERE id=%s)', (cid,)) == 0
            assert scalar(observer, 'SELECT status FROM driftread.discovery_targets WHERE id=%s', (tid,)) == ('rejected' if decision == 'rejected' else 'done')
        else:
            assert initial['status'] == 'accepted'
            assert second == {'error': 'candidate_already_imported'}
            assert scalar(observer, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'imported'
            assert scalar(observer, 'SELECT status FROM driftread.discovery_targets WHERE id=%s', (tid,)) == 'done'
            assert scalar(observer, 'SELECT to_jsonb(f) FROM driftread.feeds f WHERE f.id=(SELECT feed_id FROM driftread.discovery_candidates WHERE id=%s)', (cid,)) == before_feed


def test_candidate_human_action_checks_version_and_rolls_back_atomically(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        host = f'{uuid4().hex}.example'
        tid = scalar(conn, "INSERT INTO driftread.discovery_targets(url,host,status) VALUES(%s,%s,'done') RETURNING id", (f'https://{host}/', host))
        scalar(conn, 'UPDATE driftread.discovery_candidates SET source_host=%s,target_id=%s WHERE id=%s RETURNING id', (host, tid, cid))
        old_version = scalar(conn, 'SELECT review_version FROM driftread.discovery_candidates WHERE id=%s', (cid,))
        scalar(conn, "UPDATE driftread.discovery_candidates SET title='new evidence' WHERE id=%s RETURNING id", (cid,))
        version = scalar(conn, 'SELECT review_version FROM driftread.discovery_candidates WHERE id=%s', (cid,))
        conn.commit()
        assert scalar(conn, "SELECT driftread.decide_discovery_candidate(%s,'rejected',%s,NULL,true)", (cid, old_version)) == {'error': 'candidate_changed'}
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'pending'
        assert scalar(conn, 'SELECT status FROM driftread.discovery_targets WHERE id=%s', (tid,)) == 'done'
        assert scalar(conn, "SELECT driftread.decide_discovery_candidate(%s,'rejected',%s,NULL,true)", (cid, version))['status'] == 'rejected'
        conn.rollback()
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'pending'
        assert scalar(conn, 'SELECT manual_override FROM driftread.discovery_candidates WHERE id=%s', (cid,)) is None
        assert scalar(conn, 'SELECT status FROM driftread.discovery_targets WHERE id=%s', (tid,)) == 'done'


@pytest.mark.parametrize('decision', ['held', 'rejected'])
def test_candidate_action_refuses_existing_alias_feed(pg_database, decision):
    with connection(pg_database, 'service_role') as conn:
        url = f'https://{uuid4().hex}.example/feed'
        cid = candidate(conn, url)
        fid = scalar(conn, "INSERT INTO driftread.feeds(title,url,category,tags) VALUES('curated',%s,'manual',ARRAY['saved']) RETURNING id", (url + '/',))
        version = scalar(conn, 'SELECT review_version FROM driftread.discovery_candidates WHERE id=%s', (cid,))
        assert scalar(conn, 'SELECT driftread.decide_discovery_candidate(%s,%s,%s)', (cid, decision, version)) == {'error': 'candidate_already_imported'}
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'pending'
        assert scalar(conn, 'SELECT manual_override FROM driftread.discovery_candidates WHERE id=%s', (cid,)) is None
        assert scalar(conn, 'SELECT title||category||tags[1] FROM driftread.feeds WHERE id=%s', (fid,)) == 'curatedmanualsaved'
        conn.commit()


def test_explicit_retry_assigns_new_paid_request_identity(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        ordinary = request(conn, cid)
        assert ordinary['paid_retry_key'] is None
        assert request(conn, cid)['id'] == ordinary['id']
        first_retry = scalar(conn, 'SELECT driftread.request_source_review(%s,true)', (cid,))
        second_retry = scalar(conn, 'SELECT driftread.request_source_review(%s,true)', (cid,))
        assert first_retry['paid_retry_key'] is not None
        assert second_retry['paid_retry_key'] != first_retry['paid_retry_key']
        assert second_retry['id'] != first_retry['id']
        assert request(conn, cid)['paid_retry_key'] == second_retry['paid_retry_key']
        conn.commit()


def test_policy_change_fences_work_and_does_not_drain_human_queue(pg_database):
    with connection(pg_database, 'service_role') as conn:
        scalar(conn, "UPDATE driftread.app_settings SET value=jsonb_set(value,'{skip_human_review}','false') WHERE key='source.review' RETURNING version")
        cid = candidate(conn)
        r = ready(conn, cid)
        assert submit(conn, r)['status'] == 'awaiting_human'
        second_id = candidate(conn)
        second = ready(conn, second_id)
        scalar(conn, "UPDATE driftread.app_settings SET value=jsonb_set(value,'{skip_human_review}','true') WHERE key='source.review' RETURNING version")
        assert submit(conn, second)['status'] == 'stale'
        scalar(conn, 'SELECT driftread.prune_source_reviews()')
        assert scalar(conn, 'SELECT status FROM driftread.source_reviews WHERE id=%s', (r['id'],)) == 'awaiting_human'
        assert scalar(conn, 'SELECT count(*) FROM driftread.feeds WHERE url=(SELECT feed_url FROM driftread.discovery_candidates WHERE id=%s)', (cid,)) == 0
        retry = scalar(conn, 'SELECT driftread.request_source_review(%s,true)', (second_id,))
        assert retry['status'] == 'requested' and retry['id'] != second['id']
        assert scalar(conn, "SELECT driftread.resolve_source_review(%s,'accept')", (r['id'],))['status'] == 'accepted'
        assert scalar(conn, 'SELECT manual_override FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'approve'
        conn.commit()


@pytest.mark.parametrize('text,changes', [('', {}), ('title only', {}), ('real text '*30, {'confidence': .2}), ('real text '*30, {'dimensions': {'quality': .9, 'spam_probability': .01, 'relevance': 0}}), ('real text '*30, {'dimensions': {'quality': .2, 'spam_probability': .9, 'relevance': .9}})])
def test_hard_gates_defer_accept(pg_database, text, changes):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = ready(conn, cid, text)
        assert submit(conn, r, **changes)['status'] == 'deferred'
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'pending'
        conn.commit()


def test_model_rejection_does_not_block_host_or_create_manual_override(pg_database):
    with connection(pg_database, 'service_role') as conn:
        tid = scalar(conn, "INSERT INTO driftread.discovery_targets(url,host) VALUES(%s,'publisher.example') RETURNING id", (f'https://{uuid4().hex}.example/',))
        cid = candidate(conn)
        scalar(conn, 'UPDATE driftread.discovery_candidates SET target_id=%s WHERE id=%s RETURNING id', (tid, cid))
        assert submit(conn, ready(conn, cid), 'reject')['status'] == 'rejected'
        assert scalar(conn, 'SELECT status FROM driftread.discovery_targets WHERE id=%s', (tid,)) == 'pending'
        assert scalar(conn, 'SELECT manual_override FROM driftread.discovery_candidates WHERE id=%s', (cid,)) is None
        assert scalar(conn, 'SELECT status FROM driftread.discovery_candidates WHERE id=%s', (cid,)) == 'pending'
        conn.commit()


def test_lease_reclaim_fences_previous_agent(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        first = ready(conn, cid)
        scalar(conn, "UPDATE driftread.source_reviews SET lease_expires_at=now()-interval '1 second' WHERE id=%s RETURNING id", (first['id'],))
        second = scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (first['id'],))
        assert second['lease_token'] != first['lease_token']
        assert submit(conn, first)['error'] == 'lease'
        assert submit(conn, second)['status'] == 'accepted'
        conn.commit()


def test_concurrent_claim_only_one_agent_gets_task(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        row = ready(conn, cid)
        scalar(conn, "UPDATE driftread.source_reviews SET status='ready' WHERE id=%s RETURNING id", (row['id'],))
        conn.commit()
    barrier = Barrier(2)
    def claim():
        with connection(pg_database, 'service_role') as conn:
            barrier.wait(3)
            result = scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (row['id'],))
            conn.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(result is not None for result in results) == 1


def test_atomic_import_rolls_back_and_legacy_cannot_bypass_review(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = ready(conn, cid)
        conn.commit()
        assert submit(conn, r)['status'] == 'accepted'
        conn.rollback()
        assert scalar(conn, 'SELECT status FROM driftread.source_reviews WHERE id=%s', (r['id'],)) == 'leased'
        assert scalar(conn, 'SELECT count(*) FROM driftread.analysis_results WHERE review_id=%s', (r['id'],)) == 0
        assert scalar(conn, 'SELECT driftread.promote_discovery_candidate(%s)', (cid,)) is None
        assert scalar(conn, "SELECT driftread.promote_discovery_candidate(%s,'manual',ARRAY['tag'],true)", (cid,))['category'] == 'manual'
        assert submit(conn, r)['status'] == 'overridden'
        conn.commit()


def test_slash_alias_hold_blocks_admission(pg_database):
    with connection(pg_database, 'service_role') as conn:
        url = f'https://{uuid4().hex}.example/feed'
        cid = candidate(conn, url)
        r = ready(conn, cid)
        alias = candidate(conn, url + '/')
        scalar(conn, "UPDATE driftread.discovery_candidates SET status='held' WHERE id=%s RETURNING id", (alias,))
        conn.commit()
        assert submit(conn, r)['status'] == 'overridden'
        assert scalar(conn, 'SELECT count(*) FROM driftread.feeds WHERE url=%s', (url,)) == 0
        conn.commit()


def test_pruning_removes_expired_snippets_and_bounds_old_results(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        for n in range(12):
            scalar(conn, "INSERT INTO driftread.source_reviews(candidate_id,status,engine,model,policy_version,policy,candidate_version,snapshot,snapshot_expires_at,created_at) VALUES(%s,'rejected','external_agent','test',1,'{}',1,'{\"samples\":[]}',now()-interval '1 day',now()-interval '40 days'+%s*interval '1 second') RETURNING id", (cid, n))
        assert scalar(conn, 'SELECT driftread.prune_source_reviews()') >= 12
        assert scalar(conn, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s', (cid,)) == 10
        assert scalar(conn, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s AND snapshot IS NOT NULL', (cid,)) == 0
        conn.commit()


@pytest.mark.parametrize('mutation', ['held', 'rejected', 'version', 'host_block'])
def test_preparation_claim_checks_manual_version_and_database_host_block(pg_database, mutation):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = request(conn, cid)
        if mutation in {'held', 'rejected'}:
            scalar(conn, 'UPDATE driftread.discovery_candidates SET status=%s WHERE id=%s RETURNING id', (mutation, cid))
        elif mutation == 'version':
            scalar(conn, "UPDATE driftread.discovery_candidates SET title='changed' WHERE id=%s RETURNING id", (cid,))
        else:
            scalar(conn, "INSERT INTO driftread.discovery_targets(url,host,status) VALUES(%s,'publisher.example','rejected') RETURNING id", (f'https://{uuid4().hex}.example/',))
        assert scalar(conn, "SELECT driftread.claim_source_review('preparer',true,%s)", (r['id'],)) is None
        assert scalar(conn, 'SELECT status FROM driftread.source_reviews WHERE id=%s', (r['id'],)) == 'overridden'
        conn.rollback()  # A host block in this fixture must not affect later cases.


def test_external_model_identity_and_preparation_failure_backoff(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = request(conn, cid)
        assert r['model'] == 'external-agent'
        for failure in range(1, 4):
            claim = scalar(conn, "SELECT driftread.claim_source_review('preparer',true,%s)", (r['id'],))
            result = scalar(conn, "SELECT driftread.prepare_source_review(%s,%s,NULL,NULL,'sample_fetch_failed')", (r['id'], claim['lease_token']))
            assert result['failure_count'] == failure
            assert result['status'] == ('requested' if failure < 3 else 'failed')
            assert scalar(conn, "SELECT driftread.claim_source_review('preparer',true,%s)", (r['id'],)) is None
            scalar(conn, "UPDATE driftread.source_reviews SET available_at=now()-interval '1 second' WHERE id=%s RETURNING id", (r['id'],))
        conn.commit()


def test_provider_retry_preserves_snapshot_and_pauses_unknown_outcome(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = ready(conn, cid)
        original_hash = r['snapshot_hash']
        for failure in range(1, 4):
            result = scalar(conn, "SELECT driftread.fail_source_review(%s,%s,'provider_http_retryable')", (r['id'], r['lease_token']))
            assert result['failure_count'] == failure
            assert result['status'] == ('ready' if failure < 3 else 'failed')
            assert result['snapshot_hash'] == original_hash
            assert scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (r['id'],)) is None
            if failure < 3:
                scalar(conn, "UPDATE driftread.source_reviews SET available_at=now()-interval '1 second' WHERE id=%s RETURNING id", (r['id'],))
                r = scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (r['id'],))
        cid = candidate(conn)
        unknown = ready(conn, cid)
        result = scalar(conn, "SELECT driftread.fail_source_review(%s,%s,'request_unknown_paused')", (unknown['id'], unknown['lease_token']))
        assert result['status'] == 'failed'
        assert scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (unknown['id'],)) is None
        conn.commit()


def test_budget_wait_is_due_later_without_consuming_failure_counter(pg_database):
    with connection(pg_database, 'service_role') as conn:
        cid = candidate(conn)
        r = ready(conn, cid)
        for _ in range(4):
            result = scalar(conn, "SELECT driftread.fail_source_review(%s,%s,'budget_exceeded')", (r['id'], r['lease_token']))
            assert result['status'] == 'ready' and result['failure_count'] == 0 and result['attempts'] == 0
            assert scalar(conn, "SELECT available_at>now()+interval '4 minutes' FROM driftread.source_reviews WHERE id=%s", (r['id'],)) is True
            assert scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (r['id'],)) is None
            scalar(conn, "UPDATE driftread.source_reviews SET available_at=now()-interval '1 second' WHERE id=%s RETURNING id", (r['id'],))
            r = scalar(conn, "SELECT driftread.claim_source_review('external_agent',false,%s)", (r['id'],))
        conn.commit()


def test_policy_hot_reload_requeues_unfinished_stale_but_keeps_human_results(pg_database):
    with connection(pg_database, 'service_role') as conn:
        # Quarantine candidates from other tests so bounded selection is deterministic.
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.discovery_candidates SET status='held' WHERE status='pending'")
        scalar(conn, "UPDATE driftread.app_settings SET value=jsonb_set(value,'{skip_human_review}','false') WHERE key='source.review' RETURNING version")
        human_id = candidate(conn)
        human = ready(conn, human_id)
        assert submit(conn, human)['status'] == 'awaiting_human'
        unfinished_id = candidate(conn)
        unfinished = request(conn, unfinished_id)
        rejected_id = candidate(conn)
        rejected = ready(conn, rejected_id)
        assert submit(conn, rejected, 'abstain')['status'] == 'deferred'
        scalar(conn, "UPDATE driftread.app_settings SET value=jsonb_set(jsonb_set(value,'{skip_human_review}','true'),'{engine}','\"jev\"') WHERE key='source.review' RETURNING version")
        scalar(conn, 'SELECT driftread.prune_source_reviews()')
        assert scalar(conn, 'SELECT status FROM driftread.source_reviews WHERE id=%s', (unfinished['id'],)) == 'stale'
        result = scalar(conn, 'SELECT driftread.request_due_source_reviews(10)')
        assert result['requested'] == 1
        new = scalar(conn, 'SELECT to_jsonb(r) FROM driftread.source_reviews r WHERE candidate_id=%s ORDER BY created_at DESC,id DESC LIMIT 1', (unfinished_id,))
        assert new['id'] != unfinished['id'] and new['status'] == 'requested' and new['engine'] == 'jev'
        assert new['policy_version'] > unfinished['policy_version']
        assert scalar(conn, 'SELECT status FROM driftread.source_reviews WHERE id=%s', (human['id'],)) == 'awaiting_human'
        assert scalar(conn, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s', (human_id,)) == 1
        assert scalar(conn, 'SELECT count(*) FROM driftread.source_reviews WHERE candidate_id=%s', (rejected_id,)) == 1
        conn.rollback()
