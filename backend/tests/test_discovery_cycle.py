from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from services.discovery import run_cycle
from services.link_harvest import HostIndex
from tests.discovery_fakes import FakeDB

FEED_ID = str(uuid4())


def _db(**extra):
    tables = {
        "feeds": [],
        "articles": [],
        "discovery_targets": [],
        "discovery_target_referrers": [],
        "discovery_candidates": [],
        "discovery_sources": [],
    }
    tables.update(extra)
    return FakeDB(**tables)


def _patched(**overrides):
    """Stub every stage; individual tests override the ones they care about."""
    defaults = {
        "services.discovery.harvest_sources_due": [],
        "services.discovery.harvest_due": [],
        "services.discovery.probe_due": [],
        "services.discovery.auto_promote_due": [],
        "services.discovery.promote_approved": [],
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_cycle_runs_stages_in_order():
    """Directories and harvesting fill the frontier, so both must run before the
    probe that drains it — a host found this cycle gets probed this cycle."""
    calls: list[str] = []

    async def record_async(name):
        async def inner(*a, **k):
            calls.append(name)
            return []
        return inner

    with (
        patch("services.discovery.build_host_index",
              return_value=HostIndex(frozenset(), {})),
        patch("services.discovery.directory_enabled", return_value=True),
        patch("services.discovery.harvest_sources_due", await record_async("directory")),
        patch("services.discovery.harvest_due", await record_async("harvest")),
        patch("services.discovery.probe_due", await record_async("probe")),
        patch("services.discovery.auto_promote_due",
              side_effect=lambda *a, **k: calls.append("auto") or []),
        patch("services.discovery.promote_approved",
              side_effect=lambda *a, **k: calls.append("promote") or []),
    ):
        await run_cycle(_db())

    assert calls == ["directory", "harvest", "probe", "auto", "promote"]


@pytest.mark.asyncio
async def test_directory_stage_is_skipped_when_disabled():
    with (
        patch("services.discovery.build_host_index",
              return_value=HostIndex(frozenset(), {})),
        patch("services.discovery.directory_enabled", return_value=False),
        patch("services.discovery.harvest_sources_due") as sources,
        patch("services.discovery.harvest_due", return_value=[]),
        patch("services.discovery.probe_due", return_value=[]),
        patch("services.discovery.auto_promote_due", return_value=[]),
        patch("services.discovery.promote_approved", return_value=[]),
    ):
        summary = await run_cycle(_db())

    sources.assert_not_called()
    assert summary.directory["processed"] == 0


@pytest.mark.asyncio
async def test_one_failing_stage_does_not_lose_the_rest():
    """The worker's per-cycle try/except is the outer net; this is the inner one."""
    with (
        patch("services.discovery.build_host_index",
              return_value=HostIndex(frozenset(), {})),
        patch("services.discovery.directory_enabled", return_value=True),
        patch("services.discovery.harvest_sources_due",
              side_effect=RuntimeError("directory boom")),
        patch("services.discovery.harvest_due", side_effect=RuntimeError("harvest boom")),
        patch("services.discovery.probe_due", return_value=[]),
        patch("services.discovery.auto_promote_due", return_value=[{"id": "f1"}]),
        patch("services.discovery.promote_approved", return_value=[{"id": "f2"}]),
    ):
        summary = await run_cycle(_db())

    assert summary.directory["processed"] == 0
    assert summary.harvest["processed"] == 0
    assert summary.auto_promoted == 1
    assert summary.imported == 1


@pytest.mark.asyncio
async def test_probe_still_runs_when_the_host_index_cannot_be_built():
    """The frontier already holds work; a broken index only costs us new hosts."""
    with (
        patch("services.discovery.build_host_index", side_effect=RuntimeError("boom")),
        patch("services.discovery.harvest_sources_due") as sources,
        patch("services.discovery.harvest_due") as harvest,
        patch("services.discovery.probe_due", return_value=[]) as probe,
        patch("services.discovery.auto_promote_due", return_value=[]),
        patch("services.discovery.promote_approved", return_value=[]),
    ):
        summary = await run_cycle(_db())

    sources.assert_not_called()
    harvest.assert_not_called()
    probe.assert_called_once()
    assert summary.harvest["processed"] == 0


@pytest.mark.asyncio
async def test_cycle_shares_one_index_between_both_harvest_stages():
    index = HostIndex(frozenset(), {})
    with (
        patch("services.discovery.build_host_index", return_value=index),
        patch("services.discovery.directory_enabled", return_value=True),
        patch("services.discovery.harvest_sources_due", return_value=[]) as sources,
        patch("services.discovery.harvest_due", return_value=[]) as harvest,
        patch("services.discovery.probe_due", return_value=[]),
        patch("services.discovery.auto_promote_due", return_value=[]),
        patch("services.discovery.promote_approved", return_value=[]),
    ):
        await run_cycle(_db())

    assert sources.call_args.args[1] is index
    assert harvest.call_args.kwargs["index"] is index


@pytest.mark.asyncio
async def test_cycle_forwards_bounds_to_the_probe():
    with (
        patch("services.discovery.build_host_index",
              return_value=HostIndex(frozenset(), {})),
        patch("services.discovery.harvest_due", return_value=[]),
        patch("services.discovery.probe_due", return_value=[]) as probe,
        patch("services.discovery.auto_promote_due", return_value=[]),
        patch("services.discovery.promote_approved", return_value=[]),
    ):
        await run_cycle(_db(), probe_limit=7, max_concurrency=2)

    assert probe.call_args.kwargs == {"limit": 7, "max_concurrency": 2}


@pytest.mark.asyncio
async def test_empty_cycle_reports_zeroed_summaries():
    with (
        patch("services.discovery.build_host_index",
              return_value=HostIndex(frozenset(), {})),
        patch("services.discovery.harvest_due", return_value=[]),
        patch("services.discovery.probe_due", return_value=[]),
        patch("services.discovery.auto_promote_due", return_value=[]),
        patch("services.discovery.promote_approved", return_value=[]),
    ):
        summary = await run_cycle(_db())

    assert summary.harvest["targets_created"] == 0
    assert summary.probe["found"] == 0
    assert summary.directory["processed"] == 0
    assert (summary.auto_promoted, summary.imported) == (0, 0)


@pytest.mark.asyncio
async def test_cycle_gates_both_harvest_stages_not_just_the_probe():
    """Both harvest stages make outbound requests (the blogroll hop and the
    directory fetch). Leaving allow_url at its default there would make
    FEED_DISCOVERY_RESPECT_ROBOTS apply only to the probe and quietly mean
    nothing for the other two."""
    index = HostIndex(frozenset(), {})
    with (
        patch("services.discovery.build_host_index", return_value=index),
        patch("services.discovery.directory_enabled", return_value=True),
        patch("services.discovery.harvest_sources_due", return_value=[]) as sources,
        patch("services.discovery.harvest_due", return_value=[]) as harvest,
        patch("services.discovery.probe_due", return_value=[]),
        patch("services.discovery.auto_promote_due", return_value=[]),
        patch("services.discovery.promote_approved", return_value=[]),
    ):
        await run_cycle(_db())

    assert sources.call_args.kwargs["allow_url"] is not None
    assert harvest.call_args.kwargs["allow_url"] is not None
    # And it is the same policy object, so the two stages can't drift apart.
    assert sources.call_args.kwargs["allow_url"] is harvest.call_args.kwargs["allow_url"]


@pytest.mark.asyncio
async def test_cycle_gate_denies_a_denylisted_host():
    """Sanity-check the object actually is the crawl policy, not just non-None."""
    captured = {}

    async def capture(db, limit=None, allow_url=None, index=None):
        captured["gate"] = allow_url
        return []

    with (
        patch("services.discovery.build_host_index",
              return_value=HostIndex(frozenset(), {})),
        patch("services.discovery.harvest_due", side_effect=capture),
        patch("services.discovery.probe_due", return_value=[]),
        patch("services.discovery.auto_promote_due", return_value=[]),
        patch("services.discovery.promote_approved", return_value=[]),
    ):
        await run_cycle(_db())

    with patch("services.crawl_policy.respect_robots", return_value=False):
        assert await captured["gate"]("https://ok.example.org/") is True
        assert await captured["gate"]("https://facebook.com/x") is False
