from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from database import get_client
from routers.admin_retention import router
from services import article_cap
from services.article_cap import enforce_article_cap, scheduled_enforcement
from services.settings import (
    ARTICLE_CAP_KEY, ArticleCapValue, get_setting, load_article_cap_settings,
)

KEY = {"x-api-key": "test-admin-key"}
BASE = "/admin/retention/article-cap/run"


def _summary(**overrides):
    return {"dry_run": True, "feeds_over_cap": 2, "excess": 90, "eligible": 38,
            "deleted": 0, "skipped_feeds": 0} | overrides


def _db(**overrides):
    # Only the RPC entry point exists: a wrong method name fails loudly.
    db = Mock(spec=["rpc"])
    db.rpc.return_value.execute.return_value.data = _summary(**overrides)
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("discovery,keep", [("true", True), ("false", False)])
async def test_preview_is_default_and_extraction_guard_follows_discovery(monkeypatch, discovery, keep):
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", discovery)
    db = _db()
    summary = await enforce_article_cap(db, cap=1000)
    assert summary.deleted == 0
    db.rpc.assert_called_once_with("enforce_article_cap", {
        "p_default_cap": 1000, "p_limit": 500, "p_dry_run": True, "p_keep_unextracted": keep,
    })


@pytest.mark.asyncio
async def test_cap_defaults_to_the_global_setting():
    db = _db(eligible=10)
    with patch.object(article_cap, "load_article_cap_settings",
                      return_value=ArticleCapValue(max_per_feed=300)) as load:
        await enforce_article_cap(db, limit=10)
    load.assert_called_once_with(db)
    assert db.rpc.call_args.args[1]["p_default_cap"] == 300
    assert db.rpc.call_args.args[1]["p_limit"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"cap": 99}, {"cap": 100001}, {"cap": True}, {"limit": 0}, {"limit": 1001},
    {"limit": True}, {"dry_run": "false"},
])
async def test_invalid_arguments_never_reach_db(kwargs):
    db = _db()
    with pytest.raises(ValueError):
        await enforce_article_cap(db, **({"cap": 1000} | kwargs))
    db.rpc.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("data,dry_run", [
    (_summary(dry_run=False), True),
    (_summary(deleted=1), True),
    (_summary(dry_run=False, eligible=2, deleted=3), False),
    (_summary(eligible=501), True),
])
async def test_rpc_contract_violations_raise(data, dry_run):
    db = Mock(spec=["rpc"])
    db.rpc.return_value.execute.return_value.data = data
    with pytest.raises(RuntimeError):
        await enforce_article_cap(db, cap=1000, dry_run=dry_run)


@pytest.mark.asyncio
async def test_scheduler_does_nothing_while_disabled():
    db = _db()
    with patch.object(article_cap, "load_article_cap_settings",
                      return_value=ArticleCapValue(enabled=False)):
        assert await scheduled_enforcement(db) is None
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_applies_one_batch_with_the_setting_cap():
    db = _db(dry_run=False, deleted=30)
    with patch.object(article_cap, "load_article_cap_settings",
                      return_value=ArticleCapValue(max_per_feed=2000)):
        summary = await scheduled_enforcement(db)
    assert summary.deleted == 30
    params = db.rpc.call_args.args[1]
    assert params["p_dry_run"] is False and params["p_default_cap"] == 2000


def test_setting_defaults_to_enabled_1000_when_row_is_absent():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = None
    setting = get_setting(db, ARTICLE_CAP_KEY)
    assert setting.version == 0
    assert setting.value == {"enabled": True, "max_per_feed": 1000}
    assert load_article_cap_settings(db) == ArticleCapValue()


@pytest.mark.parametrize("value", [
    {"enabled": True, "max_per_feed": 99}, {"enabled": True, "max_per_feed": 100001},
    {"enabled": "yes", "max_per_feed": 1000}, {"enabled": True, "max_per_feed": "1000"},
    {"enabled": True, "max_per_feed": 1000, "extra": 1},
])
def test_setting_rejects_values_the_rpc_would_refuse(value):
    with pytest.raises(ValidationError):
        ArticleCapValue.model_validate(value)


@pytest.fixture
def cap_client(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")
    app = FastAPI()
    app.include_router(router)
    db = _db()
    app.dependency_overrides[get_client] = lambda: db
    with TestClient(app) as client:
        yield client, db


@pytest.mark.parametrize("headers,status", [({}, 422), ({"x-api-key": "bad"}, 403)])
def test_endpoint_rejects_unauthorized_request_before_rpc(cap_client, headers, status):
    client, db = cap_client
    assert client.post(BASE, headers=headers).status_code == status
    db.rpc.assert_not_called()


def test_endpoint_previews_by_default(cap_client):
    client, db = cap_client
    with patch.object(article_cap, "load_article_cap_settings",
                      return_value=ArticleCapValue(enabled=False)):
        response = client.post(BASE, headers=KEY)
    assert response.status_code == 200
    assert response.json() == _summary()
    assert db.rpc.call_args.args[1]["p_dry_run"] is True


def test_endpoint_explicit_apply_forwards_bounds(cap_client):
    client, db = cap_client
    db.rpc.return_value.execute.return_value.data = _summary(dry_run=False, eligible=40, deleted=40)
    response = client.post(f"{BASE}?dry_run=false&max_per_feed=500&limit=40", headers=KEY)
    assert response.status_code == 200
    assert db.rpc.call_args.args[1] == {
        "p_default_cap": 500, "p_limit": 40, "p_dry_run": False, "p_keep_unextracted": True,
    }


@pytest.mark.parametrize("query", [
    "max_per_feed=99", "max_per_feed=100001", "limit=0", "limit=1001", "dry_run=nonsense",
])
def test_endpoint_rejects_invalid_bounds_before_rpc(cap_client, query):
    client, db = cap_client
    assert client.post(f"{BASE}?{query}", headers=KEY).status_code == 422
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_worker_loop_records_summary_and_skips_claim_while_disabled():
    import asyncio
    from unittest.mock import AsyncMock

    import worker
    from services.article_cap import ArticleCapSummary
    from services.operations import OperationsRecorder
    from tests.worker_fakes import MemoryQueue

    stop = asyncio.Event()
    allowed = iter([False, True])

    async def can_start(db):
        return next(allowed)

    async def enforce(db):
        stop.set()
        return ArticleCapSummary(dry_run=False, feeds_over_cap=1, excess=10,
                                 eligible=10, deleted=10, skipped_feeds=0)

    queue_calls = []

    class RecordingQueue(MemoryQueue):
        async def claim(self, kind, worker_id):
            queue_calls.append(kind)
            return await super().claim(kind, worker_id)

    with patch.object(worker, "JobQueue", RecordingQueue), \
         patch.object(worker, "get_client", return_value=None), \
         patch.object(worker, "article_cap_enabled", can_start), \
         patch.object(worker, "scheduled_enforcement", enforce), \
         patch.object(worker, "POLL_SECONDS", 0.01), \
         patch.object(OperationsRecorder, "pulse", AsyncMock()), \
         patch.object(OperationsRecorder, "begin", AsyncMock(return_value="run")), \
         patch.object(OperationsRecorder, "finish", AsyncMock()) as finish:
        await asyncio.wait_for(worker.run_article_cap_forever(stop), 2)
    # The disabled pass neither enqueued nor claimed; the enabled one ran once.
    assert queue_calls == ["article_cap"]
    finish.assert_awaited_once()
    assert finish.await_args.args[1] == "succeeded"
    assert finish.await_args.args[2]["deleted"] == 10
