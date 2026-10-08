"""Stage two: reach out to a frontier target and see whether it publishes a feed.

The actual probing is the existing services/feed_discovery.py::discover_feeds() —
its four-stage sweep (is-it-a-feed, <link rel=alternate>, validate by parsing,
well-known paths) is exactly what's wanted, and it already validates every
candidate by parsing it. What this module adds is everything around that: the due
queue, politeness, robots, retry backoff, and turning results into candidates.

**The empty-result problem.** By default discover_feeds() absorbs its own fetch
errors and returns `[]`, so from its return value alone "this site has no feed"
and "this site is down" look identical. Retrying the first forever is pure waste;
not retrying the second loses real sources.

An earlier version inferred the difference from the robots.txt fetch, on the
grounds that it hits the same host moments earlier. That is not sound: a 404 on
/robots.txt proves the server answered *that* request, not that the target page
is fetchable, so a site whose homepage was timing out could still be filed as
"no feed here" and never revisited. The probe therefore passes
`raise_on_fetch_error=True` and gets the target's own outcome:

| discover_feeds outcome | means                          | result                  |
|------------------------|--------------------------------|-------------------------|
| raises                 | the page itself is unreachable | failed, backoff         |
| `[]`                   | fetched fine, advertises no feed | done, terminal        |
| candidates             | feeds found                     | done, recorded         |

Which also means the decision no longer depends on robots being enabled.
"""
from __future__ import annotations

import asyncio
import logging
import re
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
from services.link_harvest import is_denied_host, normalize_host
from services.settings import load_discovery_profiles

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


def _profile_hosts(profile) -> tuple[str, ...]:
    """Only plain normalized DNS hostnames ever reach PostgREST set filters."""
    hosts = set()
    for url in profile.seed_urls:
        host = normalize_host(url)
        if host and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            hosts.add(host)
    return tuple(sorted(hosts))


def _normal_order(row: dict) -> tuple:
    return (-(row.get("referring_feed_count") or 0), row.get("next_probe_at") or "", str(row["id"]))


def select_due_targets(db: "Client", limit: int) -> list[dict]:
    """Reserve at most half a batch across configurable discovery profiles.

    Profile language/category describe desired sources; they never substitute
    for a feed's detected facts. Reservation applies only to due pending seeds
    at explicitly configured exact hosts. Other targets retain evidence order.
    """
    if limit <= 0:
        return []
    now = datetime.now(timezone.utc).isoformat()
    profiles = load_discovery_profiles(db)
    enabled_hosts: set[str] = set()
    disabled_hosts: set[str] = set()
    for profile in profiles:
        hosts = _profile_hosts(profile)
        (enabled_hosts if profile.enabled else disabled_hosts).update(hosts)
    exclusive_disabled = sorted(disabled_hosts - enabled_hosts)
    reserved_limit = limit // 2
    queues = []
    for profile in profiles:
        hosts = _profile_hosts(profile)
        quota = min(profile.quota, reserved_limit)
        if not profile.enabled or not hosts or not quota:
            continue
        rows = list((db.table("discovery_targets").select("*")
                     .eq("status", "pending").lte("next_probe_at", now)
                     .eq("source", "seed").in_("host", list(hosts))
                     .order("next_probe_at").order("host").order("url")
                     .limit(quota + reserved_limit).execute()).data or [])
        if rows:
            queues.append((str(profile.id), rows, quota))
    # Oldest head first, then one slot per profile per round. Small batch budgets
    # progress through profiles as their oldest pending seeds are completed.
    queues.sort(key=lambda item: (item[1][0].get("next_probe_at") or "", item[0]))
    reserved: list[dict] = []
    reserved_ids: set[str] = set()
    while queues and len(reserved) < reserved_limit:
        next_round = []
        for profile_id, rows, quota in queues:
            while rows and str(rows[0]["id"]) in reserved_ids:
                rows.pop(0)
            if rows and len(reserved) < reserved_limit:
                row = rows.pop(0)
                reserved.append(row)
                reserved_ids.add(str(row["id"]))
                quota -= 1
            if rows and quota:
                next_round.append((profile_id, rows, quota))
        queues = next_round

    def normal_query(*, source: str | None = None):
        query = (db.table("discovery_targets").select("*")
                 .eq("status", "pending").lte("next_probe_at", now))
        if source == "seed":
            query = query.eq("source", "seed").not_.in_("host", exclusive_disabled)
        elif source == "organic":
            query = query.neq("source", "seed")
        if reserved_ids:
            # IDs originate from this UUID-typed table, never user filter text.
            query = query.not_.in_("id", sorted(reserved_ids))
        return list(query.order("referring_feed_count", desc=True)
                    .order("next_probe_at").limit(limit - len(reserved)).execute().data or [])

    if exclusive_disabled:
        normal = normal_query(source="organic") + normal_query(source="seed")
        normal.sort(key=_normal_order)
    else:
        normal = normal_query()
    return reserved + normal[:limit - len(reserved)]


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
        safe_url = await validate_fetch_url(target["url"])
    except DiscoveryError as e:
        exhausted, _ = _record_failure(db, target, str(e))
        return ProbeResult(target_id, host, "failed", error=str(e)[:500],
                           exhausted=exhausted)

    # 2. Re-check the denylist. Catches targets seeded before a denylist update,
    #    and costs nothing.
    if is_denied_host(host):
        _record_terminal(db, target, "blocked", last_failure_reason="denylisted host")
        return ProbeResult(target_id, host, "blocked", error="denylisted host")

    # 3. robots.txt.
    delay = host_delay_seconds()
    if respect_robots():
        decision = await robots.check(safe_url, ua)
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

    # 4. The actual sweep. raise_on_fetch_error makes the target's own fetch
    #    outcome visible instead of collapsing into [] — that is what separates
    #    "no feed here" from "this page is down".
    try:
        candidates = await discover_feeds(
            safe_url, delay_seconds=delay, allow_url=make_gate(ua),
            raise_on_fetch_error=True,
        )
    except Exception as e:  # noqa: BLE001 - a bad target must not abort the batch
        reason = str(e)[:500] or e.__class__.__name__
        logger.info("Probe of %s failed: %s", safe_url, reason)
        exhausted, _ = _record_failure(db, target, reason)
        return ProbeResult(target_id, host, "failed", error=reason,
                           exhausted=exhausted)

    if candidates:
        new, seen = record_candidates(db, target, candidates)
        _record_terminal(
            db, target, "done",
            feeds_found=len(candidates), attempts=0, last_failure_reason=None,
        )
        return ProbeResult(target_id, host, "found", candidates_new=new,
                           candidates_seen=seen)

    # 5. We fetched the page and it advertises no feed, and none of the
    #    well-known paths answered either. Terminal — retrying would only ask the
    #    same question again.
    _record_terminal(
        db, target, "done", feeds_found=0, attempts=0, last_failure_reason=None
    )
    return ProbeResult(target_id, host, "none")


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

    targets = await asyncio.to_thread(select_due_targets, db, limit)
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
