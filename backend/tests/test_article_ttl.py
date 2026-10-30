import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from database import get_client
from routers.admin_retention import router
from services import article_ttl
from services.article_ttl import ArticleTtlSummary, enforce_article_ttl, scheduled_expiry
from services.settings import (
    ARTICLE_TTL_KEY, ArticleTtlValue, get_setting, load_article_ttl_settings, save_setting,
)
from tests.discovery_fakes import FakeDB

KEY = {"x-api-key": "test-admin-key"}
BASE = "/admin/retention/article-ttl/run"


def summary(**overrides):
    return {"dry_run": True, "expired": 100, "protected": 7, "eligible": 20,
            "deleted": 0, "reads_affected": 3, "skipped_feeds": 0,
            "deletion_delay_seconds": 0} | overrides


def rpc_db(**overrides):
    db = Mock(spec=["rpc"])
    db.rpc.return_value.execute.return_value.data = summary(**overrides)
    return db


@pytest.mark.asyncio
async def test_preview_defaults_to_saved_days_without_consulting_other_switches(monkeypatch):
    db = rpc_db()
    load = Mock(return_value=ArticleTtlValue(enabled=False, days=21))
    monkeypatch.setattr(article_ttl, "load_article_ttl_settings", load)
    result = await enforce_article_ttl(db)
    load.assert_called_once_with(db)
    assert result.deleted == 0 and result.reads_affected == 3
    db.rpc.assert_called_once_with("enforce_article_ttl", {
        "p_days": 21, "p_limit": 500, "p_dry_run": True,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"days": 0}, {"days": 3651}, {"days": True}, {"days": 14.0},
    {"limit": 0}, {"limit": 1001}, {"limit": True}, {"dry_run": "false"},
])
async def test_invalid_input_never_reaches_rpc(kwargs):
    db = rpc_db()
    with pytest.raises(ValueError):
        await enforce_article_ttl(db, **({"days": 14} | kwargs))
    db.rpc.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("data,dry_run", [
    (summary(dry_run=False), True),
    (summary(deleted=1), True),
    (summary(deletion_delay_seconds=1), True),
    (summary(dry_run=False, eligible=2, deleted=3), False),
    (summary(eligible=501), True),
    (summary(protected=101), True),
    (summary(dry_run=False, deleted=0, deletion_delay_seconds=1), False),
])
async def test_rpc_contract_violation_is_not_reported_as_success(data, dry_run):
    db = rpc_db()
    db.rpc.return_value.execute.return_value.data = data
    with pytest.raises(RuntimeError):
        await enforce_article_ttl(db, days=14, dry_run=dry_run)


def test_absent_setting_is_disabled_fourteen_days():
    db = FakeDB(app_settings=[])
    setting = get_setting(db, ARTICLE_TTL_KEY)
    assert setting.value == {"enabled": False, "days": 14}
    assert setting.version == 0 and setting.updated_at is None
    assert load_article_ttl_settings(db) == ArticleTtlValue()


@pytest.mark.parametrize("value", [
    {"enabled": "true", "days": 14}, {"enabled": True, "days": "14"},
    {"enabled": True, "days": True}, {"enabled": True, "days": 0},
    {"enabled": False, "days": 3651}, {"enabled": True, "days": 14, "extra": 1},
])
def test_setting_is_strict_and_bounded(value):
    with pytest.raises(ValidationError):
        ArticleTtlValue.model_validate(value)


@pytest.mark.asyncio
async def test_saving_ttl_uses_versioned_setting_rpc_without_network():
    db = Mock(spec=["rpc"])
    value = {"enabled": True, "days": 14}
    db.rpc.return_value.execute.return_value.data = {"key": ARTICLE_TTL_KEY,
                                                    "value": value, "version": 4}
    saved = await save_setting(db, ARTICLE_TTL_KEY, value, 3)
    assert saved.value == value
    db.rpc.assert_called_once_with("save_app_setting", {
        "p_key": ARTICLE_TTL_KEY, "p_value": value, "p_expected_version": 3, "p_seeds": [],
    })


@pytest.mark.asyncio
async def test_disabled_or_unreadable_setting_never_deletes(monkeypatch):
    db = rpc_db()
    load = Mock(return_value=ArticleTtlValue())
    monkeypatch.setattr(article_ttl, "load_article_ttl_settings", load)
    assert await scheduled_expiry(db) is None
    assert not await article_ttl.article_ttl_enabled(db)
    db.rpc.assert_not_called()
    load.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        await scheduled_expiry(db)
    with pytest.raises(RuntimeError):
        await article_ttl.article_ttl_enabled(db)
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_rereads_current_setting_before_every_batch(monkeypatch):
    db = rpc_db(dry_run=False, deleted=20, deletion_delay_seconds=120)
    load = Mock(side_effect=[ArticleTtlValue(enabled=True, days=7), ArticleTtlValue(enabled=False)])
    monkeypatch.setattr(article_ttl, "load_article_ttl_settings", load)
    result = await scheduled_expiry(db)
    assert result.deleted == 20
    assert await scheduled_expiry(db) is None
    db.rpc.assert_called_once_with("enforce_article_ttl", {
        "p_days": 7, "p_limit": 500, "p_dry_run": False,
    })


@pytest.fixture
def ttl_client(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(article_ttl, "load_article_ttl_settings", Mock(return_value=ArticleTtlValue()))
    app = FastAPI()
    app.include_router(router)
    db = rpc_db()
    app.dependency_overrides[get_client] = lambda: db
    with TestClient(app) as client:
        yield client, db


@pytest.mark.parametrize("headers,status", [({}, 422), ({"x-api-key": "bad"}, 403)])
def test_endpoint_requires_admin_before_rpc(ttl_client, headers, status):
    client, db = ttl_client
    assert client.post(BASE, headers=headers).status_code == status
    db.rpc.assert_not_called()


def test_endpoint_previews_while_schedule_remains_disabled(ttl_client):
    client, db = ttl_client
    response = client.post(BASE, headers=KEY)
    assert response.status_code == 200
    assert response.json() == summary()
    assert db.rpc.call_args.args[1]["p_dry_run"] is True


def test_endpoint_explicit_apply_uses_given_bounds(ttl_client):
    client, db = ttl_client
    db.rpc.return_value.execute.return_value.data = summary(dry_run=False, deleted=20)
    response = client.post(f"{BASE}?days=7&limit=20&dry_run=false", headers=KEY)
    assert response.status_code == 200
    assert db.rpc.call_args.args[1] == {"p_days": 7, "p_limit": 20, "p_dry_run": False}


@pytest.mark.parametrize("query", [
    "days=0", "days=3651", "limit=0", "limit=1001", "dry_run=nonsense",
])
def test_endpoint_rejects_invalid_bounds(ttl_client, query):
    client, db = ttl_client
    assert client.post(f"{BASE}?{query}", headers=KEY).status_code == 422
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_worker_retains_disabled_jobs_and_records_enabled_batch():
    import worker
    from services.operations import OperationsRecorder
    from tests.worker_fakes import MemoryQueue

    stop = asyncio.Event()
    allowed = iter([False, True])

    async def can_start(db):
        return next(allowed)

    async def expire(db):
        stop.set()
        return ArticleTtlSummary(**summary(dry_run=False, deleted=20))

    claims = []

    class RecordingQueue(MemoryQueue):
        async def claim(self, kind, worker_id):
            claims.append(kind)
            return await super().claim(kind, worker_id)

    with (
        patch.object(worker, "JobQueue", RecordingQueue),
        patch.object(worker, "get_client", return_value=None),
        patch.object(worker, "article_ttl_enabled", can_start),
        patch.object(worker, "scheduled_expiry", expire),
        patch.object(worker, "POLL_SECONDS", 0.01),
        patch.object(OperationsRecorder, "pulse", AsyncMock()),
        patch.object(OperationsRecorder, "begin", AsyncMock(return_value="run")),
        patch.object(OperationsRecorder, "finish", AsyncMock()) as finish,
    ):
        await asyncio.wait_for(worker.run_article_ttl_forever(stop), 2)
    assert claims == ["article_ttl"]
    assert finish.await_args.args[1] == "succeeded"
    assert finish.await_args.args[2]["deleted"] == 20


@pytest.mark.asyncio
async def test_worker_settings_outage_never_enqueues_or_claims():
    import worker
    from services.operations import OperationsRecorder

    stop = asyncio.Event()
    queue = Mock(spec=["reconcile", "enqueue", "claim"])
    queue.reconcile = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.claim = AsyncMock()

    async def unavailable(db):
        stop.set()
        raise RuntimeError("unavailable")

    with (
        patch.object(worker, "JobQueue", return_value=queue),
        patch.object(worker, "get_client", return_value=None),
        patch.object(worker, "article_ttl_enabled", unavailable),
        patch.object(OperationsRecorder, "pulse", AsyncMock()),
    ):
        await asyncio.wait_for(worker.run_article_ttl_forever(stop), 2)
    queue.enqueue.assert_not_awaited()
    queue.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_ttl_queue_uses_hourly_recurrence_and_bounded_timeout():
    from services.job_queue import JobQueue

    db = Mock(spec=["rpc"])
    db.rpc.return_value.execute.return_value.data = "job"
    assert await JobQueue(db).enqueue("article_ttl", article_ttl.TICK_SECONDS) == "job"
    db.rpc.assert_called_once_with("enqueue_background_job", {
        "p_kind": "article_ttl", "p_singleton_key": "scheduler",
        "p_repeat_seconds": 3600, "p_timeout_seconds": 900,
    })


@pytest.mark.asyncio
async def test_ttl_monitor_starts_independently_of_other_deployment_switches(monkeypatch):
    import worker

    for key in ("FEED_REFRESH_ENABLED", "FEED_DISCOVERY_ENABLED", "ARTICLE_RETENTION_ENABLED"):
        monkeypatch.setenv(key, "false")
    with (
        patch.object(worker, "run_forever", AsyncMock()) as refresh,
        patch.object(worker, "run_discovery_forever", AsyncMock()) as discovery,
        patch.object(worker, "run_retention_forever", AsyncMock()) as retention,
        patch.object(worker, "run_storage_forever", AsyncMock()) as storage,
        patch.object(worker, "run_source_review_forever", AsyncMock()) as review,
        patch.object(worker, "run_article_cap_forever", AsyncMock()) as cap,
        patch.object(worker, "run_article_ttl_forever", AsyncMock()) as ttl,
    ):
        assert await worker.main() == 0
    ttl.assert_awaited_once()
    storage.assert_awaited_once()
    review.assert_awaited_once()
    cap.assert_awaited_once()
    refresh.assert_not_awaited()
    discovery.assert_not_awaited()
    retention.assert_not_awaited()
