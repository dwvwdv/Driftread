from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import patch
from uuid import uuid4

import pytest

from services import discovery_probe
from services.discovery_probe import (
    MAX_RETRY_HOURS,
    next_probe_delay_hours,
    probe_due,
    probe_one,
    select_due_targets,
    summarize_probes,
)
from services.feed_discovery import DiscoveryError
from services.robots import RobotsDecision
from tests.discovery_fakes import FakeDB


@dataclass
class Found:
    feed_url: str
    title: str | None = None
    website_url: str | None = None


def _target(**overrides) -> dict:
    row = {
        "id": str(uuid4()),
        "url": "https://blog.example.org/",
        "host": "blog.example.org",
        "status": "pending",
        "attempts": 0,
        "referring_feed_count": 1,
        "next_probe_at": "2020-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _db(targets=None, **extra):
    return FakeDB(
        discovery_targets=targets if targets is not None else [],
        discovery_candidates=[],
        feeds=[],
        **extra,
    )


def _reachable(allowed=True, reachable=True, crawl_delay=None, transient=None):
    if transient is None:
        # Mirror what robots.check() can actually return: an unreachable host
        # never yields a *permanent* disallow, because the site never told us
        # anything. Only a parsed Disallow rule does.
        transient = not reachable
    return RobotsDecision(
        allowed=allowed, reachable=reachable, crawl_delay=crawl_delay,
        transient=transient,
    )


async def _probe(db, target, *, discover=None, robots_decision=None, respect=True):
    with (
        patch("services.discovery_probe.respect_robots", return_value=respect),
        patch("services.discovery_probe.robots.check",
              return_value=robots_decision or _reachable()) as check,
        patch("services.discovery_probe.discover_feeds",
              return_value=discover if discover is not None else []) as disc,
        patch("services.discovery_probe.validate_fetch_url", side_effect=lambda u: u),
    ):
        result = await probe_one(db, target)
    return result, check, disc


# ── backoff maths ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "attempts,expected", [(0, 24), (1, 24), (2, 48), (3, 96), (4, 192)]
)
def test_next_probe_delay_doubles(monkeypatch, attempts, expected):
    monkeypatch.setenv("FEED_DISCOVERY_PROBE_RETRY_HOURS", "24")
    assert next_probe_delay_hours(attempts) == expected


def test_next_probe_delay_is_capped(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_PROBE_RETRY_HOURS", "24")
    assert next_probe_delay_hours(20) == MAX_RETRY_HOURS


# ── due queue ────────────────────────────────────────────────────────────────

def test_select_due_targets_matches_the_partial_index():
    db = _db()
    select_due_targets(db, 20)
    assert db.op_names("discovery_targets") == [
        "select", "eq", "lte", "order", "order", "limit"
    ]
    ops = db.ops_for("discovery_targets")
    assert ("eq", ("status", "pending")) in ops
    orders = [args for name, args in ops if name == "order"]
    # Evidence first, then oldest-due — the probe budget is the scarce resource.
    assert orders == [("referring_feed_count", ("desc", True)),
                      ("next_probe_at", ("desc", False))]


def test_select_due_targets_skips_terminal_and_future_rows():
    db = _db([
        _target(id="a"),
        _target(id="b", status="done"),
        _target(id="c", status="rejected"),
        _target(id="d", next_probe_at="2099-01-01T00:00:00+00:00"),
    ])
    assert [t["id"] for t in select_due_targets(db, 10)] == ["a"]


# ── gates run before anything else ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_ssrf_rejection_happens_before_any_network_or_candidate_write():
    """A host that has started resolving to a private address is a failure, and
    nothing may be fetched or stored on the way to finding that out."""
    target = _target()
    db = _db([target])

    with (
        patch("services.discovery_probe.validate_fetch_url",
              side_effect=DiscoveryError("Refusing to fetch private/loopback address")),
        patch("services.discovery_probe.discover_feeds") as disc,
        patch("services.discovery_probe.robots.check") as check,
    ):
        result = await probe_one(db, target)

    disc.assert_not_called()
    check.assert_not_called()
    assert result.status == "failed"
    assert db.rows("discovery_candidates") == []
    assert target["attempts"] == 1


@pytest.mark.asyncio
async def test_denylisted_host_is_blocked_without_a_robots_request():
    target = _target(host="twitter.com", url="https://twitter.com/")
    db = _db([target])

    result, check, disc = await _probe(db, target)

    check.assert_not_called()
    disc.assert_not_called()
    assert result.status == "blocked"
    assert target["status"] == "blocked"


@pytest.mark.asyncio
async def test_robots_disallow_blocks_the_probe_and_does_not_count_as_a_failure():
    """A disallow is an answer, not an outage — retrying it would just be rude
    more often."""
    target = _target()
    db = _db([target])

    result, _check, disc = await _probe(
        db, target, robots_decision=_reachable(allowed=False, reachable=True)
    )

    disc.assert_not_called()
    assert result.status == "blocked"
    assert db.rows("discovery_candidates") == []
    assert target["status"] == "blocked"
    assert target["attempts"] == 0
    assert target["last_failure_reason"] == "robots.txt disallow"


@pytest.mark.asyncio
async def test_unreachable_robots_is_a_retryable_failure():
    target = _target()
    db = _db([target])

    result, _check, disc = await _probe(
        db, target, robots_decision=_reachable(allowed=False, reachable=False)
    )

    disc.assert_not_called()
    assert result.status == "failed"
    assert target["status"] == "pending"
    assert target["attempts"] == 1
    assert target["next_probe_at"] > "2026-07-01"


@pytest.mark.asyncio
async def test_robots_server_error_is_retried_not_permanently_blocked():
    """A 503 on /robots.txt disallows us right now (RFC 9309), but the site never
    told us to stay away — filing it as `blocked` would mean never looking again
    after the server recovered."""
    target = _target()
    db = _db([target])

    result, _check, disc = await _probe(
        db, target,
        robots_decision=_reachable(allowed=False, reachable=True, transient=True),
    )

    disc.assert_not_called()
    assert result.status == "failed"
    assert result.error == "robots.txt server error"
    assert target["status"] == "pending"
    assert target["attempts"] == 1
    assert target["next_probe_at"] > "2026-07-01"


@pytest.mark.asyncio
async def test_a_real_disallow_rule_is_still_permanent():
    """The counterpart: an actual Disallow must not start burning retries."""
    target = _target()
    db = _db([target])

    result, _check, _disc = await _probe(
        db, target,
        robots_decision=_reachable(allowed=False, reachable=True, transient=False),
    )

    assert result.status == "blocked"
    assert target["status"] == "blocked"
    assert target["attempts"] == 0


@pytest.mark.asyncio
async def test_robots_off_skips_the_check_entirely():
    """And the empty-result verdict is unaffected, because it now comes from the
    target's own fetch rather than from robots reachability."""
    target = _target()
    db = _db([target])
    result, check, _disc = await _probe(db, target, respect=False)
    check.assert_not_called()
    assert result.status == "none"
    assert target["status"] == "done"


# ── the empty-result decision ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_result_on_a_reachable_host_is_terminal():
    """discover_feeds fetched the page without raising and found nothing, so the
    site genuinely has no feed. Retrying forever would be pure waste."""
    target = _target()
    db = _db([target])

    result, _check, _disc = await _probe(db, target, discover=[])

    assert result.status == "none"
    assert target["status"] == "done"
    assert target["feeds_found"] == 0
    assert target["attempts"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("respect", [True, False])
async def test_a_failing_target_fetch_is_retried(respect):
    """A 404 on /robots.txt proves the server answered *that* request, not that
    the target page is fetchable. So the verdict comes from the target's own
    fetch: if it raises, the target is down and must be retried — with robots on
    or off."""
    target = _target()
    db = _db([target])

    with (
        patch("services.discovery_probe.respect_robots", return_value=respect),
        patch("services.discovery_probe.robots.check", return_value=_reachable()),
        patch("services.discovery_probe.discover_feeds",
              side_effect=DiscoveryError("Response exceeds cap")),
        patch("services.discovery_probe.validate_fetch_url", side_effect=lambda u: u),
    ):
        result = await probe_one(db, target)

    assert result.status == "failed"
    assert target["status"] == "pending"
    assert target["attempts"] == 1


@pytest.mark.asyncio
async def test_probe_asks_discover_feeds_for_the_fetch_outcome():
    """raise_on_fetch_error is what makes [] unambiguous."""
    target = _target()
    db = _db([target])
    _result, _check, disc = await _probe(db, target)
    assert disc.call_args.kwargs["raise_on_fetch_error"] is True


@pytest.mark.asyncio
async def test_attempts_reaching_the_ceiling_exhausts_the_target(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_PROBE_MAX_ATTEMPTS", "3")
    target = _target(attempts=2)
    db = _db([target])

    result, _check, _disc = await _probe(
        db, target, robots_decision=_reachable(allowed=False, reachable=False)
    )

    assert result.exhausted is True
    assert target["status"] == "exhausted"


# ── success path ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_found_feeds_become_candidates_and_close_the_target():
    target = _target()
    db = _db([target])

    result, _check, _disc = await _probe(
        db, target,
        discover=[Found("https://blog.example.org/feed", "A Blog",
                        "https://blog.example.org/")],
    )

    assert result.status == "found"
    assert result.candidates_new == 1
    assert target["status"] == "done"
    assert target["feeds_found"] == 1
    row = db.rows("discovery_candidates")[0]
    assert row["feed_url"] == "https://blog.example.org/feed"
    assert row["source_host"] == "blog.example.org"


@pytest.mark.asyncio
async def test_success_clears_prior_failure_state():
    target = _target(attempts=2, last_failure_reason="earlier boom")
    db = _db([target])
    await _probe(db, target, discover=[Found("https://blog.example.org/feed")])
    assert target["attempts"] == 0
    assert target["last_failure_reason"] is None


# ── politeness ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crawl_delay_overrides_the_configured_delay_when_larger(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_HOST_DELAY_SECONDS", "2")
    target = _target()
    db = _db([target])

    _result, _check, disc = await _probe(
        db, target, robots_decision=_reachable(crawl_delay=9.0)
    )

    assert disc.call_args.kwargs["delay_seconds"] == 9.0


@pytest.mark.asyncio
async def test_configured_delay_wins_when_crawl_delay_is_smaller(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_HOST_DELAY_SECONDS", "5")
    target = _target()
    db = _db([target])

    _result, _check, disc = await _probe(
        db, target, robots_decision=_reachable(crawl_delay=1.0)
    )

    assert disc.call_args.kwargs["delay_seconds"] == 5.0


@pytest.mark.asyncio
async def test_delay_is_clamped(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_HOST_DELAY_SECONDS", "9999")
    target = _target()
    db = _db([target])
    _result, _check, disc = await _probe(db, target)
    assert disc.call_args.kwargs["delay_seconds"] == 30.0


@pytest.mark.asyncio
async def test_probe_passes_a_crawl_policy_gate_that_denies_denylisted_hosts():
    target = _target()
    db = _db([target])
    _result, _check, disc = await _probe(db, target)

    gate = disc.call_args.kwargs["allow_url"]
    with patch("services.crawl_policy.respect_robots", return_value=False):
        assert await gate("https://ok.example.org/feed") is True
        assert await gate("https://facebook.com/x") is False
        assert await gate("https://192.168.0.1/x") is False


# ── probe_due ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_due_caps_concurrency():
    # Distinct registrable domains: subdomains of one domain share a site_key
    # and would (correctly) serialize on the same host lock instead.
    targets = [_target(id=str(i), url=f"https://site{i}.org/",
                       host=f"site{i}.org") for i in range(8)]
    db = _db(targets)
    in_flight = 0
    peak = 0

    async def slow_probe(_db, target):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return discovery_probe.ProbeResult(str(target["id"]), target["host"], "none")

    with patch("services.discovery_probe.probe_one", side_effect=slow_probe):
        results = await probe_due(db, limit=8, max_concurrency=3)

    assert len(results) == 8
    assert peak <= 3


@pytest.mark.asyncio
async def test_probe_due_isolates_an_unexpected_error_per_target():
    targets = [_target(id="a", host="a.example.org"),
               _target(id="b", host="b.example.org")]
    db = _db(targets)

    async def flaky(_db, target):
        if target["id"] == "a":
            raise KeyError("bad row shape")
        return discovery_probe.ProbeResult("b", "b.example.org", "none")

    with patch("services.discovery_probe.probe_one", side_effect=flaky):
        results = await probe_due(db)

    assert [r.target_id for r in results] == ["a", "b"]
    assert results[0].status == "failed"
    assert results[1].status == "none"


@pytest.mark.asyncio
async def test_probe_due_returns_empty_when_nothing_is_due():
    db = _db([_target(status="done")])
    assert await probe_due(db) == []


@pytest.mark.asyncio
async def test_probe_due_respects_the_batch_size(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_PROBE_BATCH_SIZE", "2")
    db = _db([_target(id=str(i), url=f"https://site{i}.org/",
                      host=f"site{i}.org") for i in range(5)])

    async def noop(_db, target):
        return discovery_probe.ProbeResult(str(target["id"]), target["host"], "none")

    with patch("services.discovery_probe.probe_one", side_effect=noop):
        assert len(await probe_due(db)) == 2


def test_summarize_probes_counts_every_outcome():
    R = discovery_probe.ProbeResult
    results = [
        R("1", "a", "found", candidates_new=2),
        R("2", "b", "none"),
        R("3", "c", "blocked"),
        R("4", "d", "failed", exhausted=True),
    ]
    assert summarize_probes(results) == {
        "processed": 4, "found": 1, "none_found": 1, "blocked": 1,
        "failed": 1, "exhausted": 1, "candidates_new": 2,
    }


# ── per-host serialization ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probes_of_one_host_never_overlap():
    """An OPML directory listing several feeds from one publisher puts several
    URL-unique rows on the same host, and they sort adjacently in the due queue.
    Running those concurrently would overlap their delays and make the per-host
    interval — the one guarantee we make to the sites we crawl — meaningless."""
    targets = [
        _target(id="a", url="https://pub.example.org/a", host="pub.example.org"),
        _target(id="b", url="https://pub.example.org/b", host="pub.example.org"),
        _target(id="c", url="https://pub.example.org/c", host="pub.example.org"),
    ]
    db = _db(targets)
    in_flight = 0
    peak = 0

    async def slow_probe(_db, target):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return discovery_probe.ProbeResult(str(target["id"]), target["host"], "none")

    with patch("services.discovery_probe.probe_one", side_effect=slow_probe):
        results = await probe_due(db, limit=3, max_concurrency=3)

    assert len(results) == 3
    assert peak == 1  # serialized despite three free concurrency slots


@pytest.mark.asyncio
async def test_subdomains_of_one_site_also_serialize():
    """Keyed on site_key, so a publisher spread across subdomains still gets one
    request stream."""
    targets = [
        _target(id="a", url="https://a.pub.example.org/", host="a.pub.example.org"),
        _target(id="b", url="https://b.pub.example.org/", host="b.pub.example.org"),
    ]
    db = _db(targets)
    in_flight = 0
    peak = 0

    async def slow_probe(_db, target):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return discovery_probe.ProbeResult(str(target["id"]), target["host"], "none")

    with patch("services.discovery_probe.probe_one", side_effect=slow_probe):
        await probe_due(db, limit=2, max_concurrency=2)

    assert peak == 1


@pytest.mark.asyncio
async def test_different_hosts_still_run_concurrently():
    """The lock must not collapse the batch down to serial."""
    targets = [
        _target(id=str(i), url=f"https://site{i}.org/", host=f"site{i}.org")
        for i in range(4)
    ]
    db = _db(targets)
    in_flight = 0
    peak = 0

    async def slow_probe(_db, target):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return discovery_probe.ProbeResult(str(target["id"]), target["host"], "none")

    with patch("services.discovery_probe.probe_one", side_effect=slow_probe):
        await probe_due(db, limit=4, max_concurrency=3)

    assert peak > 1
