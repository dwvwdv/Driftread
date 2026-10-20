from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_client
from routers import admin_source_reviews as reviews

REVIEW_ID = "10000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "10000000-0000-4000-8000-000000000002"


@pytest.fixture
def review_client(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("SOURCE_REVIEW_MCP_KEY", "review-machine-key")
    app = FastAPI()
    app.include_router(reviews.router, prefix="/api")
    db = Mock(spec=[])
    app.dependency_overrides[get_client] = lambda: db
    with TestClient(app) as client:
        yield client, db


def test_rest_admin_auth_and_management_session_precedence(review_client):
    client, db = review_client
    with patch.object(reviews.source_review, "list_reviews", return_value=[]) as read, \
            patch("services.manage_auth.require_session") as session:
        assert client.get("/api/admin/source-reviews").status_code == 422
        assert client.get("/api/admin/source-reviews", headers={"X-API-Key": "review-machine-key"}).status_code == 403
        read.assert_not_called()
        response = client.get("/api/admin/source-reviews", headers={"Authorization": "Bearer admin", "X-Manage-Session": "session"})
        assert response.status_code == 200 and response.json() == {"items": []}
        session.assert_called_once_with("Bearer admin", "session")
        read.assert_called_once_with(db, candidate_id=None, limit=50)


@pytest.mark.parametrize('discovery', [False, True])
def test_rest_request_packet_retry_and_human_resolution(review_client, monkeypatch, discovery):
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', str(discovery).lower())
    client, db = review_client
    headers = {"X-API-Key": "test-admin-key"}
    with patch.object(reviews.source_review, "request_review", return_value={"id": REVIEW_ID, "status": "queued"}) as enqueue, \
            patch.object(reviews.source_review, "get_review_packet", return_value={"id": REVIEW_ID, "candidate_id": CANDIDATE_ID}) as read, \
            patch.object(reviews.source_review, "resolve_review", return_value={"id": REVIEW_ID, "status": "accepted"}) as resolve:
        assert client.post(f"/api/admin/source-reviews/candidates/{CANDIDATE_ID}/request", headers=headers).status_code == 200
        enqueue.assert_called_with(db, CANDIDATE_ID)
        assert client.get(f"/api/admin/source-reviews/{REVIEW_ID}", headers=headers).status_code == 200
        read.assert_called_with(db, REVIEW_ID)
        assert client.post(f"/api/admin/source-reviews/{REVIEW_ID}/retry", headers=headers).status_code == 200
        enqueue.assert_called_with(db, CANDIDATE_ID, retry=True, expected_review_id=REVIEW_ID)
        result = client.post(f"/api/admin/source-reviews/{REVIEW_ID}/resolve", headers=headers, json={"decision": "accept"})
        assert result.status_code == 200 and result.json()["status"] == "accepted"
        resolve.assert_called_once_with(db, REVIEW_ID, "accept")
        for body in ({"decision": "invalid"}, {"decision": "accept", "engine": "jev"}):
            assert client.post(f"/api/admin/source-reviews/{REVIEW_ID}/resolve", headers=headers, json=body).status_code == 422
        assert client.get("/api/admin/source-reviews?limit=101", headers=headers).status_code == 422
        assert client.get("/api/admin/source-reviews?candidate_id=invalid", headers=headers).status_code == 422
        assert client.get("/api/admin/source-reviews/invalid", headers=headers).status_code == 422
        assert resolve.call_count == 1


def test_rest_maps_business_error_without_retrying(review_client):
    client, _ = review_client
    with patch.object(reviews.source_review, "request_review", side_effect=reviews.source_review.ReviewError(409, "Candidate has a manual decision")) as enqueue:
        result = client.post(f"/api/admin/source-reviews/candidates/{CANDIDATE_ID}/request", headers={"X-API-Key": "test-admin-key"})
        assert result.status_code == 409 and result.json()["detail"] == "Candidate has a manual decision"
        assert enqueue.call_count == 1


def test_usage_summary_is_admin_only_and_not_parsed_as_review_uuid(review_client):
    client, db = review_client
    summary = {"last_24h": {"sends": 2, "charged_usd": .01, "reserved_usd": 0},
               "statuses": {"succeeded": 2}, "unknown_paused": 0, "recent_requests": []}
    with patch.object(reviews, "ai_usage_summary", new_callable=AsyncMock, return_value=summary) as usage:
        assert client.get("/api/admin/source-reviews/usage").status_code == 422
        usage.assert_not_awaited()
        result = client.get("/api/admin/source-reviews/usage?limit=5", headers={"X-API-Key": "test-admin-key"})
        assert result.status_code == 200 and result.json() == summary
        usage.assert_awaited_once_with(db, limit=5)
        assert client.get("/api/admin/source-reviews/usage?limit=101", headers={"X-API-Key": "test-admin-key"}).status_code == 422
        assert usage.await_count == 1


def test_latest_reviews_checks_admin_auth_once_for_the_whole_batch(review_client):
    client, db = review_client
    candidate_ids = [CANDIDATE_ID, REVIEW_ID]
    items = [{"id": REVIEW_ID, "candidate_id": CANDIDATE_ID, "manual_override": "held"}]
    with patch.object(reviews.source_review, "latest_reviews", return_value=items) as read, \
            patch("services.manage_auth.require_session") as session:
        assert client.post("/api/admin/source-reviews/latest", json={"candidate_ids": candidate_ids}).status_code == 422
        assert client.post("/api/admin/source-reviews/latest", headers={"X-API-Key": "review-machine-key"},
                           json={"candidate_ids": candidate_ids}).status_code == 403
        read.assert_not_called()
        result = client.post("/api/admin/source-reviews/latest",
                             headers={"Authorization": "Bearer admin", "X-Manage-Session": "session"},
                             json={"candidate_ids": candidate_ids})
        assert result.status_code == 200 and result.json() == {"items": items}
        session.assert_called_once_with("Bearer admin", "session")
        read.assert_called_once_with(db, candidate_ids)


@pytest.mark.parametrize("body", [
    {}, {"candidate_ids": []}, {"candidate_ids": [CANDIDATE_ID] * 51},
    {"candidate_ids": ["invalid"]}, {"candidate_ids": [CANDIDATE_ID], "limit": 100},
])
def test_latest_reviews_rejects_invalid_or_unbounded_batches(review_client, body):
    client, _ = review_client
    with patch.object(reviews.source_review, "latest_reviews") as read:
        result = client.post("/api/admin/source-reviews/latest", headers={"X-API-Key": "test-admin-key"}, json=body)
        assert result.status_code == 422
        read.assert_not_called()


def test_latest_reviews_accepts_fifty_candidates_and_maps_business_errors(review_client):
    from uuid import uuid4

    client, db = review_client
    candidate_ids = [str(uuid4()) for _ in range(50)]
    with patch.object(reviews.source_review, "latest_reviews", return_value=[]) as read:
        result = client.post("/api/admin/source-reviews/latest", headers={"X-API-Key": "test-admin-key"},
                             json={"candidate_ids": candidate_ids})
        assert result.status_code == 200 and result.json() == {"items": []}
        read.assert_called_once_with(db, candidate_ids)
    with patch.object(reviews.source_review, "latest_reviews", side_effect=reviews.source_review.ReviewError(409, "Review conflict")) as read:
        result = client.post("/api/admin/source-reviews/latest", headers={"X-API-Key": "test-admin-key"},
                             json={"candidate_ids": [CANDIDATE_ID]})
        assert result.status_code == 409 and result.json()["detail"] == "Review conflict"
        read.assert_called_once_with(db, [CANDIDATE_ID])


@pytest.mark.parametrize('retry', [False, True])
def test_batch_request_is_admin_only_and_supports_explicit_paid_retry(review_client, retry):
    client, db = review_client
    payload = {'candidate_ids': [CANDIDATE_ID], 'retry': retry}
    expected = {CANDIDATE_ID: REVIEW_ID} if retry else {}
    if retry:
        payload['expected_review_ids'] = expected
    outcome = {'items': [{'candidate_id': CANDIDATE_ID, 'ok': False, 'status_code': 409,
                          'detail': 'Manual decision'}], 'succeeded': 0, 'failed': 1}
    with patch.object(reviews.source_review, 'request_reviews', return_value=outcome) as enqueue:
        assert client.post('/api/admin/source-reviews/batch/request',
                           headers={'X-API-Key': 'review-machine-key'}, json=payload).status_code == 403
        enqueue.assert_not_called()
        response = client.post('/api/admin/source-reviews/batch/request',
                               headers={'X-API-Key': 'test-admin-key'}, json=payload)
    assert response.status_code == 200 and response.json() == outcome
    assert enqueue.call_args.args[0] is db
    assert [str(cid) for cid in enqueue.call_args.args[1]] == [CANDIDATE_ID]
    assert enqueue.call_args.kwargs == {'retry': retry, 'expected_review_ids': expected}


@pytest.mark.parametrize('expected', [None, {}, {'invalid': REVIEW_ID}, {CANDIDATE_ID: 'invalid'},
                                     {'10000000-0000-4000-8000-000000000003': REVIEW_ID}])
def test_batch_retry_requires_a_selected_review_for_each_candidate(review_client, expected):
    client, _ = review_client
    body = {'candidate_ids': [CANDIDATE_ID], 'retry': True}
    if expected is not None:
        body['expected_review_ids'] = expected
    with patch.object(reviews.source_review, 'request_reviews') as request:
        result = client.post('/api/admin/source-reviews/batch/request',
                             headers={'X-API-Key': 'test-admin-key'}, json=body)
    assert result.status_code == 422
    request.assert_not_called()


@pytest.mark.parametrize('decision', ['accept', 'reject', 'defer'])
def test_batch_resolution_forwards_admin_decision(review_client, decision):
    client, db = review_client
    outcome = {'items': [], 'succeeded': 1, 'failed': 0}
    with patch.object(reviews.source_review, 'resolve_reviews', return_value=outcome) as resolve:
        response = client.post('/api/admin/source-reviews/batch/resolve',
                               headers={'X-API-Key': 'test-admin-key'},
                               json={'review_ids': [REVIEW_ID], 'decision': decision})
    assert response.status_code == 200 and response.json() == outcome
    assert resolve.call_args.args[0] is db
    assert [str(rid) for rid in resolve.call_args.args[1]] == [REVIEW_ID]
    assert resolve.call_args.args[2] == decision


@pytest.mark.parametrize('operation,id_key,extra', [
    ('request', 'candidate_ids', {}), ('resolve', 'review_ids', {'decision': 'accept'}),
])
@pytest.mark.parametrize('ids', [[], [CANDIDATE_ID] * 51, ['invalid']])
def test_rest_batch_rejects_invalid_sizes_and_ids_before_mutation(review_client, operation, id_key, extra, ids):
    client, _ = review_client
    with patch.object(reviews.source_review, 'request_reviews') as request, \
            patch.object(reviews.source_review, 'resolve_reviews') as resolve:
        response = client.post('/api/admin/source-reviews/batch/' + operation,
                               headers={'X-API-Key': 'test-admin-key'}, json={id_key: ids, **extra})
    assert response.status_code == 422
    request.assert_not_called()
    resolve.assert_not_called()


@pytest.mark.parametrize('operation,id_key,extra,service_name', [
    ('request', 'candidate_ids', {}, 'request_reviews'),
    ('resolve', 'review_ids', {'decision': 'defer'}, 'resolve_reviews'),
])
def test_rest_batches_accept_fifty_and_reject_unknown_overrides(review_client, operation, id_key, extra, service_name):
    client, _ = review_client
    with patch.object(reviews.source_review, service_name, return_value={'items': []}) as mutate:
        payload = {id_key: [CANDIDATE_ID] * 50, **extra}
        assert client.post('/api/admin/source-reviews/batch/' + operation,
                           headers={'X-API-Key': 'test-admin-key'}, json=payload).status_code == 200
        assert client.post('/api/admin/source-reviews/batch/' + operation,
                           headers={'X-API-Key': 'test-admin-key'},
                           json={**payload, 'manual_override': None}).status_code == 422
        assert mutate.call_count == 1


@pytest.mark.parametrize('assessment,decision', [('agree', None), ('disagree', None), ('disagree', 'accept'), ('disagree', 'defer')])
@pytest.mark.parametrize('batch', [False, True])
def test_judgment_endpoints_forward_only_admin_assessment(review_client, assessment, decision, batch):
    client, db = review_client
    payload = {'assessment': assessment}
    if decision is not None:
        payload['decision'] = decision
    if batch:
        payload['review_ids'] = [REVIEW_ID]
    name = 'judge_reviews' if batch else 'judge_review'
    url = '/api/admin/source-reviews/batch/judgment' if batch else f'/api/admin/source-reviews/{REVIEW_ID}/judgment'
    with patch.object(reviews.source_review, name, return_value={'items': []}) as judge:
        assert client.post(url, headers={'X-API-Key': 'review-machine-key'}, json=payload).status_code == 403
        judge.assert_not_called()
        response = client.post(url, headers={'X-API-Key': 'test-admin-key'}, json=payload)
    assert response.status_code == 200
    args = judge.call_args.args
    assert args[0] is db
    assert ([str(rid) for rid in args[1]] == [REVIEW_ID]) if batch else args[1] == REVIEW_ID
    assert args[2:] == (assessment, decision)


@pytest.mark.parametrize('body', [{}, {'assessment': 'unknown'}, {'assessment': 'agree', 'decision': 'accept'},
                                  {'assessment': 'agree', 'decision': None},
                                  {'assessment': 'disagree', 'decision': 'invalid'},
                                  {'assessment': 'agree', 'engine': 'jev'}])
def test_judgment_rejects_overrides_before_mutating(review_client, body):
    client, _ = review_client
    with patch.object(reviews.source_review, 'judge_review') as judge:
        response = client.post(f'/api/admin/source-reviews/{REVIEW_ID}/judgment',
                               headers={'X-API-Key': 'test-admin-key'}, json=body)
    assert response.status_code == 422
    judge.assert_not_called()


@pytest.mark.parametrize('ids', [[], [REVIEW_ID] * 51, ['invalid']])
def test_batch_judgment_is_bounded_and_validates_uuid(review_client, ids):
    client, _ = review_client
    with patch.object(reviews.source_review, 'judge_reviews') as judge:
        response = client.post('/api/admin/source-reviews/batch/judgment', headers={'X-API-Key': 'test-admin-key'},
                               json={'assessment': 'agree', 'review_ids': ids})
    assert response.status_code == 422
    judge.assert_not_called()
