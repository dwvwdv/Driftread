from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_client
from routers.admin_retention import router

BASE = "/admin/retention/run"
KEY = {"x-api-key": "test-admin-key"}


@pytest.fixture
def retention_client(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("ARTICLE_RETENTION_DAYS", "30")
    monkeypatch.setenv("ARTICLE_RETENTION_BATCH_SIZE", "200")
    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "dry_run": True, "eligible": 3, "compacted": 0, "content_bytes": 900,
    }
    app.dependency_overrides[get_client] = lambda: db
    with TestClient(app) as client:
        yield client, db


@pytest.mark.parametrize("headers,status", [({}, 422), ({"x-api-key": "bad"}, 403)])
def test_rejects_unauthorized_request_before_rpc(retention_client, headers, status):
    client, db = retention_client
    assert client.post(BASE, headers=headers).status_code == status
    db.rpc.assert_not_called()


def test_default_request_previews_when_scheduler_disabled(retention_client, monkeypatch):
    monkeypatch.setenv("ARTICLE_RETENTION_ENABLED", "false")
    client, db = retention_client
    response = client.post(BASE, headers=KEY)
    assert response.status_code == 200
    assert response.json() == {
        "dry_run": True, "eligible": 3, "compacted": 0, "content_bytes": 900,
    }
    assert db.rpc.call_args.args[1]["p_dry_run"] is True


def test_explicit_apply_works_with_scheduler_disabled(retention_client, monkeypatch):
    monkeypatch.setenv("ARTICLE_RETENTION_ENABLED", "false")
    client, db = retention_client
    db.rpc.return_value.execute.return_value.data.update(dry_run=False, compacted=3)
    response = client.post(f"{BASE}?dry_run=false&retention_days=90&limit=50", headers=KEY)
    assert response.status_code == 200
    assert db.rpc.call_args.args[1] == {
        "p_retention_days": 90, "p_limit": 50, "p_dry_run": False,
    }


@pytest.mark.parametrize("query", [
    "retention_days=6", "retention_days=3651", "limit=0", "limit=1001",
    "dry_run=nonsense",
])
def test_invalid_bounds_are_rejected_before_rpc(retention_client, query):
    client, db = retention_client
    assert client.post(f"{BASE}?{query}", headers=KEY).status_code == 422
    db.rpc.assert_not_called()


@pytest.mark.parametrize("headers,status", [({}, 422), ({"x-api-key": "bad"}, 403)])
def test_stats_rejects_unauthorized_request(retention_client, headers, status):
    client, db = retention_client
    assert client.get("/admin/retention/stats", headers=headers).status_code == status
    db.rpc.assert_not_called()


def test_stats_returns_current_snapshot_without_article_content(retention_client):
    client, db = retention_client
    snapshot = {
        "article_count": 100, "full_content_count": 70,
        "summary_only_count": 10, "pending_discovery_count": 5,
        "compacted_count": 20, "table_bytes": 1024,
        "index_bytes": 512, "total_bytes": 1536, "database_bytes": 4096,
    }
    db.rpc.return_value.execute.return_value.data = snapshot
    response = client.get("/admin/retention/stats", headers=KEY)
    assert response.status_code == 200
    assert response.json() == snapshot
    db.rpc.assert_called_once_with("article_storage_stats")
    db.table.assert_not_called()
