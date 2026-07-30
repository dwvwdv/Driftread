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


# ── two-loop layout (refresh + discovery) ────────────────────────────────────


@pytest.mark.asyncio
async def test_main_exits_when_both_loops_are_disabled(monkeypatch):
    """Exit 0 rather than idling, so compose's `restart: on-failure` leaves a
    deliberately-disabled worker down instead of looping it."""
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "false")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")

    with patch.object(worker, "get_client") as get_client:
        assert await worker.main() == 0

    get_client.assert_not_called()


@pytest.mark.asyncio
async def test_main_runs_only_the_refresh_loop_when_discovery_is_off(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "true")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")

    async def immediate(stop=None):
        return None

    with (
        patch.object(worker, "run_forever", side_effect=immediate) as refresh,
        patch.object(worker, "run_discovery_forever") as discovery,
    ):
        assert await worker.main() == 0

    refresh.assert_called_once()
    discovery.assert_not_called()


@pytest.mark.asyncio
async def test_main_runs_only_the_discovery_loop_when_refresh_is_off(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "false")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")

    async def immediate(stop=None):
        return None

    with (
        patch.object(worker, "run_forever") as refresh,
        patch.object(worker, "run_discovery_forever", side_effect=immediate) as discovery,
    ):
        assert await worker.main() == 0

    refresh.assert_not_called()
    discovery.assert_called_once()


@pytest.mark.asyncio
async def test_main_returns_non_zero_when_a_loop_dies(monkeypatch):
    """So compose's `restart: on-failure` actually restarts it. A failed *cycle*
    never gets here — each loop absorbs those itself."""
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "true")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")

    async def dies(stop=None):
        raise RuntimeError("loop crashed")

    async def survives(stop=None):
        return None

    with (
        patch.object(worker, "run_forever", side_effect=survives),
        patch.object(worker, "run_discovery_forever", side_effect=dies),
    ):
        assert await worker.main() == 1


@pytest.mark.asyncio
async def test_a_dying_loop_does_not_orphan_its_sibling(monkeypatch):
    """A bare gather would propagate the first exception and leave the other task
    running, unawaited, inside a process that thinks it is shutting down."""
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "true")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")
    finished = []

    async def dies(stop=None):
        raise RuntimeError("loop crashed")

    async def slow(stop=None):
        await asyncio.sleep(0)
        finished.append("refresh")

    with (
        patch.object(worker, "run_forever", side_effect=slow),
        patch.object(worker, "run_discovery_forever", side_effect=dies),
    ):
        assert await asyncio.wait_for(worker.main(), timeout=2) == 1

    assert finished == ["refresh"]


@pytest.mark.asyncio
async def test_one_stop_event_winds_down_both_loops(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_ENABLED", "true")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")
    stops: list[object] = []

    async def capture(stop=None):
        stops.append(stop)
        await asyncio.wait_for(stop.wait(), timeout=2)

    async def stopper():
        await asyncio.sleep(0)
        stops[0].set()

    with (
        patch.object(worker, "run_forever", side_effect=capture),
        patch.object(worker, "run_discovery_forever", side_effect=capture),
        patch.object(worker, "_install_signal_handlers"),
    ):
        main_task = asyncio.create_task(worker.main())
        await asyncio.sleep(0)
        await stopper()
        assert await asyncio.wait_for(main_task, timeout=2) == 0

    # Both loops were handed the *same* event, so one SIGTERM winds down both.
    assert len(stops) == 2
    assert stops[0] is stops[1]


@pytest.mark.asyncio
async def test_run_discovery_forever_survives_a_failing_cycle(monkeypatch):
    stop = asyncio.Event()
    attempts = []

    from services.discovery import CycleSummary

    async def flaky(_db):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("supabase unreachable")
        stop.set()
        return CycleSummary()

    with (
        patch.object(worker, "get_client", return_value=MagicMock()),
        patch.object(worker, "run_cycle", side_effect=flaky),
        patch.object(worker, "discovery_tick_seconds", return_value=0),
    ):
        await asyncio.wait_for(worker.run_discovery_forever(stop), timeout=2)

    assert len(attempts) == 2  # recovered and ran a second cycle
