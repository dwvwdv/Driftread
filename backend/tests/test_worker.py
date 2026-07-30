from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import worker
from services.feed_refresh import RefreshResult


# The conftest `client` fixture can't help here: it swaps get_client through
# FastAPI's dependency_overrides, and the worker has no FastAPI app — it calls
# database.get_client directly. Patch the name the worker module resolved.


@pytest.mark.asyncio
async def test_main_exits_without_polling_when_disabled(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "false")
    with (
        patch.object(worker, "get_client") as get_client,
        patch.object(worker, "refresh_due") as refresh,
    ):
        assert await worker.main() == 0

    # Must not even construct a Supabase client — a deployment that disables the
    # scheduler shouldn't require SUPABASE_* to be set for the worker to start.
    get_client.assert_not_called()
    refresh.assert_not_called()


@pytest.mark.parametrize("value", ["0", "no", "off", "FALSE", " false "])
@pytest.mark.asyncio
async def test_disabled_accepts_common_falsey_spellings(monkeypatch, value):
    monkeypatch.setenv("FEED_REFRESH_ENABLED", value)
    with patch.object(worker, "get_client") as get_client:
        assert await worker.main() == 0
    get_client.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv("FEED_REFRESH_ENABLED", raising=False)
    from services.feed_refresh import refresh_enabled

    assert refresh_enabled() is True


@pytest.mark.asyncio
async def test_run_forever_polls_then_stops_on_signal(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_TICK_SECONDS", "60")
    stop = asyncio.Event()
    calls = []

    async def fake_refresh_due(db):
        calls.append(db)
        # One cycle is enough; ask the loop to wind down.
        stop.set()
        return [RefreshResult(feed_id="a", status="updated", new_articles=1)]

    sentinel = MagicMock(name="supabase-client")
    with (
        patch.object(worker, "get_client", return_value=sentinel),
        patch.object(worker, "refresh_due", side_effect=fake_refresh_due),
    ):
        # Must return promptly despite the 60s tick — the sleep is a wait on the
        # stop event, not a bare asyncio.sleep.
        await asyncio.wait_for(worker.run_forever(stop), timeout=2)

    assert calls == [sentinel]


@pytest.mark.asyncio
async def test_run_forever_survives_a_failing_cycle(monkeypatch):
    """A transient Supabase error must not kill the process: compose's restart
    policy has no backoff, so a crash-loop would hammer the API.
    """
    monkeypatch.setenv("FEED_REFRESH_TICK_SECONDS", "60")
    stop = asyncio.Event()
    attempts = []

    async def flaky(_db):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("supabase unreachable")
        stop.set()
        return []

    with (
        patch.object(worker, "get_client", return_value=MagicMock()),
        patch.object(worker, "refresh_due", side_effect=flaky),
        # Collapse the inter-cycle wait so the second attempt happens at once.
        patch.object(worker, "tick_seconds", return_value=0),
    ):
        await asyncio.wait_for(worker.run_forever(stop), timeout=2)

    assert len(attempts) == 2  # recovered and ran a second cycle
