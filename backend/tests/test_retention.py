from unittest.mock import MagicMock

import pytest

from services.retention import (
    batch_size,
    compact_content,
    retention_days,
    retention_enabled,
    scheduled_compaction,
    storage_stats,
    tick_seconds,
)


def _db(*, dry_run=True, eligible=4, compacted=0):
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "dry_run": dry_run, "eligible": eligible,
        "compacted": compacted, "content_bytes": 1234,
    }
    return db


@pytest.mark.asyncio
async def test_default_is_single_atomic_preview(monkeypatch):
    monkeypatch.delenv("ARTICLE_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("ARTICLE_RETENTION_BATCH_SIZE", raising=False)
    db = _db()
    summary = await compact_content(db)
    assert summary.compacted == 0
    db.rpc.assert_called_once_with("compact_article_content", {
        "p_retention_days": 30, "p_limit": 200, "p_dry_run": True,
    })
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_apply_forwards_bounded_params():
    db = _db(dry_run=False, compacted=4)
    assert (await compact_content(db, days=60, limit=10, dry_run=False)).compacted == 4
    db.rpc.assert_called_once_with("compact_article_content", {
        "p_retention_days": 60, "p_limit": 10, "p_dry_run": False,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"days": 0}, {"days": 6}, {"days": 3651}, {"days": True},
    {"limit": 0}, {"limit": 1001}, {"limit": True}, {"dry_run": "false"},
])
async def test_invalid_arguments_never_reach_db(kwargs):
    db = _db()
    with pytest.raises(ValueError):
        await compact_content(db, **kwargs)
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_rpc_failure_has_no_unsafe_row_update_fallback():
    db = _db()
    db.rpc.return_value.execute.side_effect = RuntimeError("migration absent")
    with pytest.raises(RuntimeError, match="migration absent"):
        await compact_content(db, dry_run=False)
    db.table.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {"dry_run": False, "eligible": 1, "compacted": 1, "content_bytes": 2},
    {"dry_run": True, "eligible": 1, "compacted": 1, "content_bytes": 2},
    {"dry_run": True, "eligible": 201, "compacted": 0, "content_bytes": 2},
])
async def test_invalid_rpc_contract_is_detected(data):
    db = _db()
    db.rpc.return_value.execute.return_value.data = data
    with pytest.raises(RuntimeError):
        await compact_content(db)


@pytest.mark.parametrize("value", ["", "false", "0", "tru", "enabled"])
@pytest.mark.asyncio
async def test_scheduler_requires_explicit_opt_in(monkeypatch, value):
    monkeypatch.setenv("ARTICLE_RETENTION_ENABLED", value)
    db = _db()
    assert not retention_enabled()
    assert await scheduled_compaction(db) is None
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_only_applies_one_batch(monkeypatch):
    monkeypatch.setenv("ARTICLE_RETENTION_ENABLED", "true")
    db = _db(dry_run=False, compacted=4)
    await scheduled_compaction(db)
    assert db.rpc.call_count == 1
    assert db.rpc.call_args.args[1]["p_dry_run"] is False


def test_environment_bounds_and_invalid_values(monkeypatch):
    monkeypatch.setenv("ARTICLE_RETENTION_DAYS", "2")
    monkeypatch.setenv("ARTICLE_RETENTION_BATCH_SIZE", "invalid")
    monkeypatch.setenv("ARTICLE_RETENTION_INTERVAL_MINUTES", "0")
    assert retention_days() == 30
    assert batch_size() == 200
    assert tick_seconds() == 86400
    monkeypatch.setenv("ARTICLE_RETENTION_DAYS", "999999")
    monkeypatch.setenv("ARTICLE_RETENTION_BATCH_SIZE", "999999")
    monkeypatch.setenv("ARTICLE_RETENTION_INTERVAL_MINUTES", "999999")
    assert retention_days() == 3650
    assert batch_size() == 1000
    assert tick_seconds() == 604800


@pytest.mark.asyncio
async def test_storage_stats_returns_snapshot_using_read_only_rpc():
    db = MagicMock()
    snapshot = {
        "article_count": 100, "full_content_count": 70,
        "summary_only_count": 10, "pending_discovery_count": 5,
        "compacted_count": 20, "table_bytes": 1024,
        "index_bytes": 512, "total_bytes": 1536, "database_bytes": 4096,
    }
    db.rpc.return_value.execute.return_value.data = snapshot
    assert (await storage_stats(db)).model_dump() == snapshot
    db.rpc.assert_called_once_with("article_storage_stats")
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_storage_stats_does_not_hide_missing_migration():
    db = MagicMock()
    db.rpc.return_value.execute.side_effect = RuntimeError("missing RPC")
    with pytest.raises(RuntimeError, match="missing RPC"):
        await storage_stats(db)
    db.table.assert_not_called()
