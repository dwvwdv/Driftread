from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import call, patch

import pytest
from pydantic import ValidationError

from services.review_models import ReviewSubmission
from services.source_review import ReviewError, _fetch_snapshot, _rpc, get_review_packet, latest_reviews, submit_review
from services import source_review as service
from services.review_models import BatchReviewSubmission


def body(**changes):
    value = {'lease_token': str(uuid4()), 'snapshot_hash': 'a'*64, 'decision': 'accept',
             'confidence': .99, 'dimensions': {'quality': .9, 'spam_probability': .01, 'relevance': .9},
             'reason_codes': [], 'tags': []}
    value.update(changes)
    return value


@pytest.mark.parametrize('changes', [
    {'confidence': float('nan')}, {'confidence': float('inf')}, {'confidence': -1},
    {'tags': ['x'*101]}, {'reason_codes': ['x'*101]}, {'tags': ['x']*21},
    {'rationale': 'x'*2001}, {'engine': 'trusted_agent'}, {'snapshot_hash': 'wrong'},
])
def test_submission_bounds(changes):
    with pytest.raises(ValidationError):
        ReviewSubmission.model_validate(body(**changes))


class RPCDB:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.response))


def test_latest_reviews_uses_one_embedded_request_with_per_candidate_limit_and_no_packet_secrets():
    import httpx
    from postgrest import SyncPostgrestClient

    candidate_ids = [str(uuid4()) for _ in range(50)]
    latest_id, tied_id = "ffffffff-ffff-4fff-8fff-ffffffffffff", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    histories = {
        # A single candidate has more history than the old list endpoint's
        # global maximum; it must not consume another candidate's result slot.
        candidate_ids[0]: [
            {"id": str(uuid4()), "candidate_id": candidate_ids[0], "created_at": "2026-10-01T00:00:00Z"}
            for _ in range(120)
        ] + [
            {"id": tied_id, "candidate_id": candidate_ids[0], "created_at": "2026-10-15T00:00:00Z"},
            {"id": latest_id, "candidate_id": candidate_ids[0], "created_at": "2026-10-15T00:00:00Z",
             "status": "awaiting_human", "snapshot": {"secret": "untrusted samples"},
             "lease_token": "private-token", "lease_expires_at": "private-expiry", "policy": {"secret": "private-policy"}},
        ],
        candidate_ids[1]: [],
        **{cid: [{"id": str(uuid4()), "candidate_id": cid, "created_at": "2026-09-01T00:00:00Z"}]
           for cid in candidate_ids[2:]},
    }
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/discovery_candidates"
        params = request.url.params
        assert params["select"] == "id,manual_override,source_reviews(*)"
        assert params["id"] == "in.(" + ",".join(candidate_ids) + ")"
        assert params["source_reviews.order"] == "created_at.desc,id.desc"
        assert params["source_reviews.limit"] == "1"
        assert "limit" not in params and "order" not in params
        # This models the embedded response contract; the assertions above use
        # the real client to verify the actual PostgREST request parameters.
        rows = [{"id": cid, "manual_override": "held" if cid == candidate_ids[0] else None,
                 "source_reviews": sorted(history, key=lambda row: (row["created_at"], row["id"]), reverse=True)[:1]}
                for cid, history in histories.items()]
        return httpx.Response(200, json=rows)

    with httpx.Client(base_url="https://postgrest.test", transport=httpx.MockTransport(handle)) as http:
        db = SyncPostgrestClient("https://postgrest.test", schema="driftread", http_client=http)
        items = latest_reviews(db, candidate_ids)
    assert len(requests) == 1 and len(items) == 49
    by_candidate = {row["candidate_id"]: row for row in items}
    assert set(by_candidate) == set(candidate_ids) - {candidate_ids[1]}
    assert by_candidate[candidate_ids[0]]["id"] == latest_id
    assert by_candidate[candidate_ids[0]]["manual_override"] == "held"
    assert all(by_candidate[cid]["manual_override"] is None for cid in candidate_ids[2:])
    assert not {"snapshot", "lease_token", "lease_expires_at", "policy"}.intersection(by_candidate[candidate_ids[0]])


def test_latest_reviews_omits_missing_candidates():
    import httpx
    from postgrest import SyncPostgrestClient

    with httpx.Client(base_url="https://postgrest.test", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[]))) as http:
        db = SyncPostgrestClient("https://postgrest.test", schema="driftread", http_client=http)
        assert latest_reviews(db, [str(uuid4())]) == []


@pytest.mark.parametrize('error,status', [('disabled',503), ('not_found',404), ('lease',409), ('manual_decision',409)])
def test_rpc_error_mapping(error, status):
    with pytest.raises(ReviewError) as caught:
        _rpc(RPCDB({'error':error}), 'submit_source_review', {})
    assert caught.value.status_code == status


def test_prune_integer_rpc_response_is_supported():
    assert _rpc(RPCDB(5), 'prune_source_reviews', {}) == 5


@pytest.mark.asyncio
async def test_snapshot_marks_complete_default_demo_without_marking_real_articles(monkeypatch):
    @asynccontextmanager
    async def client(**kwargs):
        yield object()

    async def fetch(*args, **kwargs):
        demo = 'Welcome to WordPress! This is your first post. Edit or delete it to take the first step in your blogging journey.'
        return ('<rss><channel><title>Test</title>'
                f'<item><title>Hello world!</title><link>https://publisher.org/1</link><description>{demo}</description></item>'
                '<item><title>Hello world!</title><link>https://publisher.org/2</link><description>My original writing.</description></item>'
                '</channel></rss>'), 'application/rss+xml'
    monkeypatch.setattr(service, 'ssrf_safe_client', client)
    monkeypatch.setattr(service, 'fetch_with_cap', fetch)
    packet = await _fetch_snapshot({'id': str(uuid4()), 'feed_url': 'https://publisher.org/feed'}, 5)
    assert packet['samples'][0]['default_demo'] is True
    assert packet['samples'][0]['text'].startswith('Welcome to WordPress!')
    assert 'default_demo' not in packet['samples'][1]


def test_model_cannot_assign_provider_identity_or_use_unknown_reviewer():
    db = RPCDB(None)
    with pytest.raises(ReviewError) as caught:
        submit_review(db, str(uuid4()), body(engine='jev'))
    assert caught.value.status_code == 422 and not db.calls
    with pytest.raises(ReviewError) as caught:
        submit_review(db, str(uuid4()), body(), reviewer_id='admin')
    assert caught.value.status_code == 403 and not db.calls


@pytest.mark.parametrize('retry', [False, True])
def test_batch_requests_deduplicate_in_order_and_keep_partial_failures(retry, caplog):
    ids = [str(uuid4()) for _ in range(4)]
    expected = {cid: str(uuid4()) for cid in ids}
    db = object()
    with patch.object(service, 'request_review', side_effect=[
        {'id': 'first'}, ReviewError(409, 'A manual decision already controls this candidate'),
        RuntimeError('provider?key=SECRET'), {'id': 'last'},
    ]) as request:
        result = service.request_reviews(db, [ids[0], ids[1], ids[0], ids[2], ids[3]], retry=retry,
                                         expected_review_ids=expected)
    assert request.call_args_list == [call(db, cid, retry=True, expected_review_id=expected[cid])
                                     if retry else call(db, cid, retry=False) for cid in ids]
    assert [item['candidate_id'] for item in result['items']] == ids
    assert [item['ok'] for item in result['items']] == [True, False, False, True]
    assert result['succeeded'] == result['failed'] == 2
    assert result['items'][1]['status_code'] == 409
    assert result['items'][2]['status_code'] == 503
    assert 'SECRET' not in str(result) + caplog.text


def test_explicit_retry_uses_the_selected_review_in_the_fenced_rpc():
    cid, rid = str(uuid4()), str(uuid4())
    db = RPCDB({'id': str(uuid4()), 'candidate_id': cid, 'status': 'requested'})
    with patch.object(service, 'get_candidate', return_value={'feed_url': 'https://publisher.example/feed'}):
        result = service.request_review(db, cid, retry=True, expected_review_id=rid)
    assert result['status'] == 'requested'
    assert db.calls == [('retry_source_review', {'p_candidate_id': cid, 'p_expected_review_id': rid})]


def test_retry_requires_review_identity_before_any_database_mutation():
    db = RPCDB(None)
    with patch.object(service, 'get_candidate') as read:
        with pytest.raises(ReviewError) as caught:
            service.request_review(db, str(uuid4()), retry=True)
    assert caught.value.status_code == 422
    assert db.calls == []
    read.assert_not_called()
    with patch.object(service, 'request_review') as request:
        with pytest.raises(ReviewError) as caught:
            service.request_reviews(db, [str(uuid4()), str(uuid4())], retry=True)
    assert caught.value.status_code == 422
    request.assert_not_called()


def test_stale_retry_is_a_partial_conflict_without_replacing_another_review():
    ids = [str(uuid4()), str(uuid4())]
    expected = {cid: str(uuid4()) for cid in ids}
    with patch.object(service, 'request_review', side_effect=[ReviewError(409, 'Review conflict'),
                                                           {'status': 'requested'}]):
        result = service.request_reviews(object(), ids, retry=True, expected_review_ids=expected)
    assert result['succeeded'] == result['failed'] == 1
    assert result['items'][0]['status_code'] == 409
    assert result['items'][1]['ok'] is True


def test_batch_resolutions_deduplicate_and_preserve_decision():
    ids = [str(uuid4()) for _ in range(2)]
    db = object()
    with patch.object(service, 'resolve_review', return_value={'status': 'accepted'}) as resolve:
        result = service.resolve_reviews(db, [ids[0], ids[0], ids[1]], 'accept')
    assert resolve.call_args_list == [call(db, rid, 'accept') for rid in ids]
    assert result['succeeded'] == 2 and result['failed'] == 0


def test_batch_submissions_keep_each_token_and_reviewer_and_report_partial_failure():
    items = [BatchReviewSubmission(review_id=uuid4(), submission=body()) for _ in range(3)]
    db = object()
    with patch.object(service, 'submit_review', side_effect=[{'status': 'accepted'},
            ReviewError(409, 'Review lease or snapshot is no longer valid'), {'status': 'awaiting_human'}]) as submit:
        result = service.submit_reviews(db, items, reviewer_id='external_agent')
    assert submit.call_args_list == [call(db, str(item.review_id), item.submission,
                                        reviewer_id='external_agent') for item in items]
    assert [row['review_id'] for row in result['items']] == [str(item.review_id) for item in items]
    assert result['succeeded'] == 2 and result['failed'] == 1
    assert result['items'][1]['status_code'] == 409


@pytest.mark.parametrize('outcomes,expected,error', [
    ([{'lease_token': 'first'}, {'lease_token': 'second'}, None], ['first', 'second'], False),
    ([{'lease_token': 'first'}, RuntimeError('SECRET'), {'lease_token': 'never'}], ['first'], True),
    ([None], [], False),
])
def test_batch_claims_keep_returned_leases_and_stop_on_empty_or_error(outcomes, expected, error, caplog):
    db = object()
    with patch.object(service, 'claim_review', side_effect=outcomes) as claim:
        result = service.claim_reviews(db, 5, reviewer_id='external_agent')
    assert [item['lease_token'] for item in result['reviews']] == expected
    assert result['claimed'] == len(expected)
    assert bool(result['error']) is error
    assert claim.call_count == len(expected) + 1
    assert all(c == call(db, reviewer_id='external_agent') for c in claim.call_args_list)
    assert 'SECRET' not in str(result) + caplog.text


def test_successful_claim_returns_lease_without_a_second_candidate_read():
    row = {'id': str(uuid4()), 'candidate_id': str(uuid4()), 'policy': {},
           'snapshot': {'source': {'name': 'Example'}, 'samples': []},
           'lease_token': str(uuid4()), 'lease_expires_at': '2099-01-01T00:00:00Z'}
    db = RPCDB(row)
    with patch.object(service, 'get_candidate', side_effect=RuntimeError('database unavailable')) as read:
        result = service.claim_review(db)
    read.assert_not_called()
    assert result['lease_token'] == row['lease_token']
    assert result['lease_expires_at'] == row['lease_expires_at']
    assert db.calls == [('claim_source_review', {'p_reviewer_id': 'external_agent'})]


@pytest.mark.asyncio
async def test_private_rss_preview_is_bounded_and_uses_each_hop_robots_policy(monkeypatch):
    import services.source_review as service
    captured = {}

    @asynccontextmanager
    async def client(**kwargs):
        captured['client'] = kwargs
        yield object()

    async def fetch(_client, url, cap, *, allow_url):
        captured['cap'] = cap
        assert await allow_url(url)
        # Redirects to a denied publisher are refused by the same gate.
        assert not await allow_url('https://facebook.com/feed')
        return '<rss version="2.0"><channel><title>Feed</title>' + ''.join(
            f'<item><title>A</title><link>https://publisher.org/{i}</link><description>{"Useful "*1000}</description></item>'
            for i in range(15)) + '</channel></rss>', 'application/rss+xml'

    async def robots(url, ua):
        captured.setdefault('robots', []).append(url)
        return SimpleNamespace(allowed=True, crawl_delay=0)

    async def sleep(delay):
        captured.setdefault('delays', []).append(delay)

    monkeypatch.setattr(service, 'ssrf_safe_client', client)
    monkeypatch.setattr(service, 'fetch_with_cap', fetch)
    monkeypatch.setattr(service.robots, 'check', robots)
    monkeypatch.setattr(service.asyncio, 'sleep', sleep)
    packet = await _fetch_snapshot({'id':str(uuid4()), 'feed_url':'https://publisher.org/feed'}, 5)
    assert len(packet['samples']) == 5
    assert all(len(sample['text']) <= 2000 for sample in packet['samples'])
    assert captured['client']['follow_redirects'] is False
    assert captured['robots'] == ['https://publisher.org/feed']
    assert len(captured['delays']) == 1


def test_external_agent_cannot_read_jev_packet():
    class Query:
        def select(self, value): return self
        def eq(self, key, value): return self
        def maybe_single(self): return self
        def execute(self): return SimpleNamespace(data={'engine':'jev','reviewer_id':'jev'})
    class DB:
        def table(self, name):
            assert name == 'source_reviews'
            return Query()
    with pytest.raises(ReviewError) as caught:
        get_review_packet(DB(), str(uuid4()), reviewer_id='external_agent')
    assert caught.value.status_code == 403


def test_server_analysis_metadata_is_receipt_linked_and_not_part_of_machine_dto():
    from services.source_review import _rubric_hash
    policy = {'policy_text': 'Useful content', 'min_quality': .7, 'min_confidence': .9,
              'max_spam_probability': .2, 'sample_size': 5}
    review_id, candidate_id, receipt_id = str(uuid4()), str(uuid4()), str(uuid4())

    class DB(RPCDB):
        def table(self, table_name):
            assert table_name in {'source_reviews', 'discovery_candidates'}
            class Query:
                def select(self, fields): return self
                def eq(self, key, value): return self
                def maybe_single(self): return self
                def execute(self):
                    return SimpleNamespace(data={'policy': policy} if table_name == 'source_reviews' else None)
            return Query()

    db = DB({'id': review_id, 'candidate_id': candidate_id, 'status': 'accepted'})
    submit_review(db, review_id, body(), 'jev', analysis_metadata={
        'receipt_id': receipt_id, 'provider_prompt_hash': 'b'*64, 'provider_input_hash': 'c'*64,
        'adapter_version': 'source-review-v1', 'secret': 'must-not-save',
    })
    payload = db.calls[0][1]['p_result']
    assert payload['analysis_metadata'] == {
        'receipt_id': receipt_id, 'provider_prompt_hash': 'b'*64, 'provider_input_hash': 'c'*64,
        'rubric_hash': _rubric_hash(policy), 'adapter_version': 'source-review-v1',
    }
    assert 'secret' not in str(payload)
    with pytest.raises(ReviewError) as caught:
        submit_review(db, review_id, body(analysis_metadata={'receipt_id': receipt_id}))
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_database_host_policy_rechecked_before_redirect_fetch(monkeypatch):
    import services.source_review as service
    seen, robot_hosts = [], []

    @asynccontextmanager
    async def client(**kwargs):
        yield object()

    async def fetch(_client, url, cap, *, allow_url):
        assert await allow_url(url)
        assert not await allow_url('https://blocked.publisher.org/feed')
        return '<rss version="2.0"><channel><title>Safe</title></channel></rss>', 'application/rss+xml'

    def database_gate(db, candidate, host, review):
        seen.append(host)
        return host != 'blocked.publisher.org'

    async def robots_check(url, ua):
        robot_hosts.append(url)
        return SimpleNamespace(allowed=True, crawl_delay=0)

    async def sleep(delay): pass

    monkeypatch.setattr(service, 'ssrf_safe_client', client)
    monkeypatch.setattr(service, 'fetch_with_cap', fetch)
    monkeypatch.setattr(service, '_crawl_database_allowed', database_gate)
    monkeypatch.setattr(service.robots, 'check', robots_check)
    monkeypatch.setattr(service.asyncio, 'sleep', sleep)
    await _fetch_snapshot({'id':str(uuid4()), 'feed_url':'https://publisher.org/feed'}, 5, db=object(), review={})
    assert seen == ['publisher.org', 'blocked.publisher.org']
    assert robot_hosts == ['https://publisher.org/feed']


@pytest.mark.asyncio
async def test_jev_adapter_metadata_is_removed_before_submission_validation(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, Mock
    import services.source_review as service
    monkeypatch.setattr(service, 'runtime_enabled', lambda db: True)
    import services.review_provider as provider
    import services.settings as settings

    row = {'id':str(uuid4()), 'candidate_id':str(uuid4()), 'status':'leased', 'engine':'jev',
           'policy':{}, 'snapshot':{'source':{}, 'samples':[]}, 'snapshot_hash':'a'*64,
           'lease_token':str(uuid4()), 'lease_expires_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()}
    metadata = {'receipt_id': str(uuid4()), 'provider_input_hash': 'b'*64}
    result = body()
    result.pop('lease_token')
    result.pop('snapshot_hash')
    result['_analysis_metadata'] = metadata
    monkeypatch.setattr(service, '_rpc', lambda db, name, params: row)
    monkeypatch.setattr(service, 'get_candidate', lambda db, candidate_id: {})
    monkeypatch.setattr(settings, 'load_source_review_settings', lambda db: {})
    monkeypatch.setattr(provider, 'evaluate_jev', AsyncMock(return_value=result))
    submit = Mock(return_value={'status':'accepted'})
    monkeypatch.setattr(service, 'submit_review', submit)
    counts = {'submitted':0, 'failed':0}
    assert await service._run_jev_review(object(), row['id'], counts)
    assert '_analysis_metadata' not in submit.call_args.args[2]
    assert submit.call_args.kwargs['analysis_metadata'] == metadata
    assert counts == {'submitted':1, 'failed':0}


def test_judgment_preserves_human_metadata_in_summary():
    cid, rid = str(uuid4()), str(uuid4())
    human = {'assessment': 'disagree', 'ai_decision': 'reject', 'decision': 'accept', 'decided_at': '2026-10-16'}
    db = RPCDB({'id': rid, 'candidate_id': cid, 'human_review': human, 'result': {'decision': 'reject'}})
    with patch.object(service, 'get_candidate', return_value={'manual_override': 'approve'}):
        result = service.judge_review(db, rid, 'disagree')
    assert db.calls == [('judge_source_review', {'p_review_id': rid, 'p_assessment': 'disagree', 'p_decision': None})]
    assert result['human_review'] == human and result['result']['decision'] == 'reject'


def test_batch_judgments_keep_partial_failures_and_deduplicate():
    ids = [str(uuid4()), str(uuid4())]
    db = object()
    with patch.object(service, 'judge_review', side_effect=[{'id': ids[0]}, ReviewError(409, 'Review conflict')]) as judge:
        result = service.judge_reviews(db, [ids[0], ids[1], ids[0]], 'agree')
    assert judge.call_args_list == [call(db, rid, 'agree', None) for rid in ids]
    assert result['succeeded'] == result['failed'] == 1
    assert [item['ok'] for item in result['items']] == [True, False]


@pytest.mark.parametrize('error,status', [('invalid_judgment', 422), ('policy_deferred', 409)])
def test_judgment_rpc_maps_clear_business_errors(error, status):
    with pytest.raises(ReviewError) as caught:
        _rpc(RPCDB({'error': error}), 'judge_source_review', {})
    assert caught.value.status_code == status
    assert caught.value.detail != 'Review conflict'
