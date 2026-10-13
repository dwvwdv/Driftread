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


def test_rest_request_packet_retry_and_human_resolution(review_client):
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
        enqueue.assert_called_with(db, CANDIDATE_ID, retry=True)
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
