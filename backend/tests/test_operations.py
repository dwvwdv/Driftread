from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_client
from routers.admin_operations import router
from services import operations
import worker
from tests.worker_fakes import MemoryQueue


@pytest.fixture(autouse=True)
def _durable_queue(monkeypatch):
    monkeypatch.setattr(worker, "JobQueue", MemoryQueue)

from services.discovery import CycleSummary
from services.feed_refresh import RefreshResult


@pytest.mark.asyncio
async def test_worker_records_partial_then_exception_then_success():
    stop = asyncio.Event()
    outcomes = [[RefreshResult(feed_id="f", status="failed")], RuntimeError("secret"), []]

    async def action(db):
        result = outcomes.pop(0)
        if not outcomes:
            stop.set()
        if isinstance(result, Exception):
            raise result
        return result

    db = MagicMock()
    with patch.object(worker, "get_client", return_value=db):
        await worker._run_loop(stop, "refresh", 0, action, worker.summarize)
    updates = [call.args[0] for call in db.table.return_value.update.call_args_list]
    assert [row["status"] for row in updates] == ["partial", "failed", "succeeded"]
    assert updates[1]["error"] == "RuntimeError"
    assert "secret" not in str(updates)


@pytest.mark.asyncio
async def test_discovery_caught_stage_failure_is_partial():
    stop = asyncio.Event()

    async def action(db):
        stop.set()
        return CycleSummary(errors=["probe"])

    db = MagicMock()
    with patch.object(worker, "get_client", return_value=db):
        await worker._run_loop(stop, "discovery", 0, action, worker.asdict)
    assert db.table.return_value.update.call_args.args[0]["status"] == "partial"


@pytest.mark.asyncio
async def test_heartbeat_progresses_during_long_cycle(monkeypatch):
    monkeypatch.setattr(operations, "HEARTBEAT_SECONDS", .01)
    db = MagicMock()
    stop = asyncio.Event()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def action(db):
        entered.set()
        await release.wait()
        stop.set()
        return []

    with patch.object(worker, "get_client", return_value=db):
        task = asyncio.create_task(worker._run_loop(stop, "refresh", 0, action, worker.summarize))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            initial = db.table.return_value.upsert.call_count
            for _ in range(100):
                if db.table.return_value.upsert.call_count >= initial + 2:
                    break
                await asyncio.sleep(.005)
            assert db.table.return_value.upsert.call_count >= initial + 2
            assert db.table.return_value.update.call_count == 0
        finally:
            release.set()
            await asyncio.wait_for(task, 2)
    assert db.table.return_value.upsert.call_args.args[0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_telemetry_outage_does_not_stop_work():
    db = MagicMock()
    db.table.side_effect = RuntimeError("database down")
    stop = asyncio.Event()
    invoked = []

    async def action(db):
        invoked.append(True)
        stop.set()
        return []

    with patch.object(worker, "get_client", return_value=db):
        await worker._run_loop(stop, "refresh", 0, action, worker.summarize)
    assert invoked == [True]


def test_stale_workers_and_interrupted_runs_are_explicit():
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    db = MagicMock()
    heartbeat = MagicMock()
    runs = MagicMock()
    extra = MagicMock()
    extra.select.return_value.order.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[])
    db.table.side_effect = lambda name: {"worker_heartbeats": heartbeat, "worker_runs": runs}.get(name, extra)
    heartbeat.select.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[{
        "worker_id": "w", "status": "running", "heartbeat_at": (now - timedelta(seconds=91)).isoformat(),
    }])
    runs.select.return_value.order.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=[
        {"id": "r", "worker_id": "w", "status": "running"},
        {"id": "ok", "worker_id": "w", "status": "succeeded"},
    ])
    with patch.object(operations, "utcnow", return_value=now):
        result = operations.operations_status(db, 5)
    assert result["workers"][0]["stale"] is True
    assert result["recent_runs"][0]["status"] == "interrupted"
    assert result["recent_runs"][1]["status"] == "succeeded"
    assert result["recent_failures"] == 1
    runs.select.return_value.order.return_value.limit.assert_called_once_with(5)


def test_admin_operations_requires_admin_key_and_bounds_response():
    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    app.dependency_overrides[get_client] = lambda: db
    with TestClient(app) as client, patch("routers.admin_operations.operations_status", return_value={"workers": []}) as status:
        assert client.get("/admin/operations").status_code == 422
        assert client.get("/admin/operations", headers={"X-API-Key": "wrong"}).status_code == 403
        status.assert_not_called()
        assert client.get("/admin/operations?limit=101", headers={"X-API-Key": "test-admin-key"}).status_code == 422
        result = client.get("/admin/operations?limit=5", headers={"X-API-Key": "test-admin-key"})
        assert result.status_code == 200
        status.assert_called_once_with(db, 5)


@pytest.mark.asyncio
async def test_discovery_caught_exception_survives_in_ledger_summary():
    from services.discovery import run_cycle

    with (
        patch("services.discovery.build_host_index", side_effect=RuntimeError("private details")),
        patch("services.discovery.probe_due", return_value=[]),
        patch("services.discovery.auto_promote_due", return_value=[]),
        patch("services.discovery.promote_approved", return_value=[]),
    ):
        result = await run_cycle(MagicMock())
    assert result.errors == ["host_index"]
    assert "private details" not in str(result)


@pytest.mark.asyncio
async def test_retention_runs_when_other_schedulers_disabled(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "false")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")
    monkeypatch.setenv("ARTICLE_RETENTION_ENABLED", "true")

    async def immediate(stop):
        return None

    with (
        patch.object(worker, "run_forever") as refresh,
        patch.object(worker, "run_discovery_forever") as discovery,
        patch.object(worker, "run_retention_forever", side_effect=immediate) as retention,
    ):
        assert await worker.main() == 0
    retention.assert_called_once()
    refresh.assert_not_called()
    discovery.assert_not_called()


@pytest.mark.asyncio
async def test_retention_disabled_does_not_run_with_refresh(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "true")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")
    monkeypatch.setenv("ARTICLE_RETENTION_ENABLED", "false")

    async def immediate(stop):
        return None

    with (
        patch.object(worker, "run_forever", side_effect=immediate),
        patch.object(worker, "run_retention_forever") as retention,
    ):
        assert await worker.main() == 0
    retention.assert_not_called()


@pytest.mark.asyncio
async def test_retention_cycle_records_compaction_summary_and_stops():
    from services.retention import RetentionSummary

    stop = asyncio.Event()
    db = MagicMock()

    async def compact(db):
        stop.set()
        return RetentionSummary(eligible=2, compacted=2, content_bytes=100, dry_run=False)

    with (
        patch.object(worker, "get_client", return_value=db),
        patch.object(worker, "scheduled_compaction", side_effect=compact),
    ):
        await asyncio.wait_for(worker.run_retention_forever(stop), 2)
    row = db.table.return_value.update.call_args.args[0]
    assert row["status"] == "succeeded"
    assert row["summary"]["compacted"] == 2
