"""Stage two: reach out to a frontier target and see whether it publishes a feed.

The actual probing is the existing services/feed_discovery.py::discover_feeds() —
its four-stage sweep (is-it-a-feed, <link rel=alternate>, validate by parsing,
well-known paths) is exactly what's wanted, and it already validates every
candidate by parsing it. What this module adds is everything around that: the due
queue, politeness, robots, retry backoff, and turning results into candidates.

**The empty-result problem.** discover_feeds() absorbs its own fetch errors and
returns `[]`, so from its return value alone "this site has no feed" and "this
site is down" look identical. Retrying the first forever is pure waste; not
retrying the second loses real sources. The resolution is to reuse the robots.txt
fetch as a reachability probe — we make that request anyway, against the same
host, moments earlier:

| robots | reachable | `[]` means      | outcome                     |
|--------|-----------|-----------------|-----------------------------|
| on     | yes       | no feed here    | done, terminal, no retry    |
| on     | no        | host unreachable| failed, backoff             |
| off    | unknown   | can't tell      | failed, backoff (imprecise) |

So turning FEED_DISCOVERY_RESPECT_ROBOTS off costs retry precision as well as
politeness. That asymmetry is documented in docs/FEATURES.md too.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from services import robots
from services.crawl_policy import make_gate
from services.discovery_candidates import record_candidates
from services.discovery_config import (
    host_delay_seconds,
    probe_batch_size,
    probe_concurrency,
    probe_max_attempts,
    probe_retry_hours,
    respect_robots,
)
from services.feed_discovery import (
    DiscoveryError,
    discover_feeds,
    user_agent,
    validate_fetch_url,
)
from services.link_harvest import is_denied_host

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

# Ceiling on the retry backoff: 30 days. Past that a target is effectively
# abandoned anyway, and probe_max_attempts usually gets there first.
MAX_RETRY_HOURS = 720

ProbeStatus = Literal["found", "none", "blocked", "failed"]


@dataclass(frozen=True)
class ProbeResult:
    target_id: str
    host: str
    status: ProbeStatus
    candidates_new: int = 0
    candidates_seen: int = 0
    error: str | None = None
    exhausted: bool = False


def select_due_targets(db: "Client", limit: int) -> list[dict]:
    """Pending targets whose next_probe_at has passed, best-evidenced first.

    Matches discovery_targets_due_idx from migration 006. Ordering by
    referring_feed_count before next_probe_at is deliberate: the probe budget is
    the scarce resource, so it should always be spent on the hosts the most
    distinct feeds vouch for.
    """
    now = datetime.now(timezone.utc).isoformat()
    result = (
        db.table("discovery_targets")
        .select("*")
        .eq("status", "pending")
        .lte("next_probe_at", now)
        .order("referring_feed_count", desc=True)
        .order("next_probe_at")
        .limit(limit)
        .execute()
    )
    return list(result.data or [])


def next_probe_delay_hours(attempts: int) -> int:
    """Doubling backoff from the configured base, capped at MAX_RETRY_HOURS."""
    base = probe_retry_hours()
    if attempts < 1:
        return base
    return min(MAX_RETRY_HOURS, base * (2 ** (attempts - 1)))


def _record_failure(
    db: "Client", target: dict, reason: str
) -> tuple[bool, dict]:
    """Bump attempts, back off, and exhaust if we've tried enough.

    Mirrors feed_refresh.refresh_one's failure bookkeeping; `exhausted` here is
    the analogue of its AUTO_ARCHIVE_FAILURE_THRESHOLD.
    """
    now = datetime.now(timezone.utc)
    attempts = (target.get("attempts") or 0) + 1
    exhausted = attempts >= probe_max_attempts()
    update = {
        "attempts": attempts,
        "last_probe_at": now.isoformat(),
        "last_failure_reason": reason[:500],
        "status": "exhausted" if exhausted else "pending",
        "next_probe_at": (
            now + timedelta(hours=next_probe_delay_hours(attempts))
        ).isoformat(),
    }
    db.table("discovery_targets").update(update).eq("id", str(target["id"])).execute()
    return (exhausted, update)


def _record_terminal(db: "Client", target: dict, status: str, **extra) -> None:
    update = {
        "status": status,
        "last_probe_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    db.table("discovery_targets").update(update).eq("id", str(target["id"])).execute()


async def probe_one(db: "Client", target: dict) -> ProbeResult:
    """Probe one target. Never raises for a bad target — the refresh_one contract."""
    target_id = str(target["id"])
    host = target.get("host") or ""
    ua = user_agent()

    # 1. SSRF gate first, before any network request and before any DB write.
    #    A host that has since started resolving to a private address is a
    #    failure, exactly as feed_refresh treats the same rejection.
    try:
        safe_url = validate_fetch_url(target["url"])
    except DiscoveryError as e:
        exhausted, _ = _record_failure(db, target, str(e))
        return ProbeResult(target_id, host, "failed", error=str(e)[:500],
                           exhausted=exhausted)

    # 2. Re-check the denylist. Catches targets seeded before a denylist update,
    #    and costs nothing.
    if is_denied_host(host):
        _record_terminal(db, target, "blocked", last_failure_reason="denylisted host")
        return ProbeResult(target_id, host, "blocked", error="denylisted host")

    # 3. robots.txt. Doubles as the reachability probe — see the module docstring.
    delay = host_delay_seconds()
    reachable: bool | None = None
    if respect_robots():
        decision = await robots.check(safe_url, ua)
        reachable = decision.reachable
        if not decision.allowed:
            if not decision.transient:
                # An actual Disallow rule is an answer, not a failure: attempts
                # stays put and the target is terminal rather than retried.
                _record_terminal(
                    db, target, "blocked", last_failure_reason="robots.txt disallow"
                )
                return ProbeResult(target_id, host, "blocked",
                                   error="robots.txt disallow")
            # A 5xx or an unreachable robots.txt disallows us right now, but the
            # site never said to stay away — so it has to go down the retry path,
            # not be filed away as permanently excluded.
            reason = (
                "robots.txt server error"
                if decision.reachable
                else "robots.txt unreachable"
            )
            exhausted, _ = _record_failure(db, target, reason)
            return ProbeResult(target_id, host, "failed", error=reason,
                               exhausted=exhausted)
        if decision.crawl_delay:
            delay = max(delay, decision.crawl_delay)
        delay = min(delay, robots.MAX_CRAWL_DELAY_SECONDS)

    # 4. The actual sweep.
    try:
        candidates = await discover_feeds(
            safe_url, delay_seconds=delay, allow_url=make_gate(ua)
        )
    except Exception as e:  # noqa: BLE001 - a bad target must not abort the batch
        logger.exception("Probe of %s failed unexpectedly", safe_url)
        exhausted, _ = _record_failure(db, target, str(e))
        return ProbeResult(target_id, host, "failed", error=str(e)[:500],
                           exhausted=exhausted)

    if candidates:
        new, seen = record_candidates(db, target, candidates)
        _record_terminal(
            db, target, "done",
            feeds_found=len(candidates), attempts=0, last_failure_reason=None,
        )
        return ProbeResult(target_id, host, "found", candidates_new=new,
                           candidates_seen=seen)

    # 5. Nothing found. What that means depends on whether we know the host is up.
    if reachable:
        _record_terminal(
            db, target, "done", feeds_found=0, attempts=0, last_failure_reason=None
        )
        return ProbeResult(target_id, host, "none")

    exhausted, _ = _record_failure(db, target, "no feed found and host unverified")
    return ProbeResult(target_id, host, "failed",
                       error="no feed found and host unverified", exhausted=exhausted)


async def probe_due(
    db: "Client", limit: int | None = None, max_concurrency: int | None = None
) -> list[ProbeResult]:
    """Probe every due target, bounded by `limit` and `max_concurrency`.

    Targets are URL-unique and the frontier is broad, so concurrent probes are
    almost always against different hosts; the spacing that matters is *within* a
    probe (robots, homepage, up to seven fallback paths) and that's what
    delay_seconds covers. Worst case here is `max_concurrency` request streams at
    one request per `host_delay_seconds`. If two subdomains of one site ever prove
    too aggressive, the fix is a per-site_key asyncio.Lock map.
    """
    limit = limit or probe_batch_size()
    max_concurrency = max_concurrency or probe_concurrency()

    targets = select_due_targets(db, limit)
    if not targets:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _guarded(target: dict) -> ProbeResult:
        async with semaphore:
            return await probe_one(db, target)

    settled = await asyncio.gather(
        *(_guarded(t) for t in targets), return_exceptions=True
    )

    results: list[ProbeResult] = []
    for target, outcome in zip(targets, settled):
        if isinstance(outcome, BaseException):
            # probe_one already absorbs probe failures, so reaching here means
            # something unexpected broke (a bad row shape, a DB error). Log it and
            # keep the rest of the batch's results.
            logger.exception(
                "Unexpected error probing target %s", target.get("id"), exc_info=outcome
            )
            results.append(
                ProbeResult(
                    str(target.get("id")), target.get("host") or "", "failed",
                    error=str(outcome)[:500],
                )
            )
        else:
            results.append(outcome)
    return results


def summarize_probes(results: list[ProbeResult]) -> dict[str, int]:
    return {
        "processed": len(results),
        "found": sum(1 for r in results if r.status == "found"),
        # `none_found`, not `none` — reads far better than `none` beside `found`.
        "none_found": sum(1 for r in results if r.status == "none"),
        "blocked": sum(1 for r in results if r.status == "blocked"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "exhausted": sum(1 for r in results if r.exhausted),
        "candidates_new": sum(r.candidates_new for r in results),
    }
