"""Shared feed-refresh logic: the due queue, adaptive backoff, and the health
bookkeeping that used to live inline in routers/admin.py.

Both the admin HTTP endpoints and the standalone scheduler (worker.py) drive
refreshes through refresh_one() / refresh_due() so failure counting, auto-archival
and interval backoff behave identically no matter what triggered the fetch.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from env_utils import env_flag, env_int
from rss_parser import fetch_and_parse_conditional
from services.articles import upsert_articles
from services.feed_discovery import validate_fetch_url

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

# Consecutive failures before a feed is considered dead and archived. Lives here
# rather than in routers/admin.py so the scheduler and the manual endpoint share
# one threshold; admin.py re-exports it for backwards compatibility.
AUTO_ARCHIVE_FAILURE_THRESHOLD = 10

DEFAULT_INTERVAL_MINUTES = 60

Outcome = Literal["new", "unchanged", "failed"]
Status = Literal["updated", "not_modified", "failed"]


def _env_int(name: str, default: int) -> int:
    """Kept as a name of its own (rather than importing env_int at every call
    site) because the module's public env accessors below all read through it and
    the tests pin its fallback behavior."""
    return env_int(name, default)


def min_interval_minutes() -> int:
    return _env_int("FEED_REFRESH_MIN_INTERVAL_MINUTES", 15)


def max_interval_minutes() -> int:
    return _env_int("FEED_REFRESH_MAX_INTERVAL_MINUTES", 1440)


def batch_size() -> int:
    return _env_int("FEED_REFRESH_BATCH_SIZE", 50)


def concurrency() -> int:
    return _env_int("FEED_REFRESH_CONCURRENCY", 5)


def tick_seconds() -> int:
    return _env_int("FEED_REFRESH_TICK_SECONDS", 300)


def refresh_enabled() -> bool:
    return env_flag("FEED_REFRESH_ENABLED", True)


@dataclass(frozen=True)
class RefreshResult:
    feed_id: str
    status: Status
    # Genuinely new rows, derived from the article-count delta. This — not
    # `upserted` — is what decides whether the feed is worth polling sooner.
    new_articles: int = 0
    # Rows touched by the upsert, i.e. what upsert_articles() returns. Kept for
    # the admin endpoint's response, which has always reported this number.
    upserted: int = 0
    total_articles: int | None = None
    error: str | None = None
    archived: bool = False


def next_interval(current: int, outcome: Outcome) -> int:
    """Adaptive polling interval, in minutes.

    Feeds that produce new articles are polled twice as often, up to the floor;
    quiet or broken ones back off exponentially, up to the ceiling. Bounds come
    from env on every call so a compose restart can retune without a code change.
    """
    floor = min_interval_minutes()
    ceiling = max_interval_minutes()
    if ceiling < floor:
        ceiling = floor
    current = max(current or DEFAULT_INTERVAL_MINUTES, 1)

    if outcome == "new":
        candidate = max(floor, current // 2)
    else:
        # "unchanged" and "failed" both back off. max(current, floor) keeps a
        # feed already below the floor from doubling into a value still under it.
        candidate = max(current, floor) * 2

    return max(floor, min(ceiling, candidate))


def count_articles(db: "Client", feed_id: str) -> int:
    """Total cached articles for a feed.

    Called before and after the upsert: the delta is the only trustworthy
    "did anything new arrive" signal, because upsert_articles() returns rows
    *touched*, which includes re-upserting the same items a feed serves every
    time. See refresh_one().
    """
    result = (
        db.table("articles")
        .select("id", count="exact", head=True)
        .eq("feed_id", feed_id)
        .execute()
    )
    return getattr(result, "count", None) or 0


def select_due_feeds(db: "Client", limit: int) -> list[dict]:
    """Unarchived feeds whose next_fetch_at has passed, oldest due first.

    Matches the feeds_next_fetch_at_idx partial index from migration 005.
    """
    now = datetime.now(timezone.utc).isoformat()
    result = (
        db.table("feeds")
        .select("*")
        .is_("archived_at", "null")
        .lte("next_fetch_at", now)
        .order("next_fetch_at")
        .limit(limit)
        .execute()
    )
    return list(result.data or [])


def _schedule(interval: int, now: datetime) -> dict:
    return {
        "fetch_interval_minutes": interval,
        "next_fetch_at": (now + timedelta(minutes=interval)).isoformat(),
    }


async def refresh_one(db: "Client", feed: dict) -> RefreshResult:
    """Re-fetch one feed, upsert its articles, and update health + schedule.

    Never raises for an unreachable or unparseable feed — the failure is
    recorded on the row and returned as status="failed", so a single bad source
    can't abort a batch. Programming errors still propagate.
    """
    feed_id = str(feed["id"])
    feed_url: str = feed["url"]
    current_failures: int = feed.get("consecutive_failures") or 0
    current_interval: int = feed.get("fetch_interval_minutes") or DEFAULT_INTERVAL_MINUTES

    try:
        safe_url = validate_fetch_url(feed_url)
        fetched = await fetch_and_parse_conditional(
            safe_url,
            etag=feed.get("etag"),
            last_modified=feed.get("last_modified"),
        )
    except Exception as e:
        now = datetime.now(timezone.utc)
        failures = current_failures + 1
        interval = next_interval(current_interval, "failed")
        update = {
            "consecutive_failures": failures,
            "last_failure_at": now.isoformat(),
            "last_failure_reason": str(e)[:500],
            "health_score": max(0, 100 - failures * 10),
            **_schedule(interval, now),
        }
        archived = failures >= AUTO_ARCHIVE_FAILURE_THRESHOLD
        if archived:
            update["archived_at"] = now.isoformat()
        db.table("feeds").update(update).eq("id", feed_id).execute()
        return RefreshResult(
            feed_id=feed_id, status="failed", error=str(e)[:500], archived=archived
        )

    now = datetime.now(timezone.utc)

    if fetched.not_modified:
        interval = next_interval(current_interval, "unchanged")
        db.table("feeds").update({
            "last_fetched_at": now.isoformat(),
            "consecutive_failures": 0,
            "health_score": 100,
            "last_failure_reason": None,
            "etag": fetched.etag,
            "last_modified": fetched.last_modified,
            **_schedule(interval, now),
        }).eq("id", feed_id).execute()
        return RefreshResult(feed_id=feed_id, status="not_modified")

    assert fetched.parsed is not None  # not_modified is False, so there's a body
    before = count_articles(db, feed_id)
    upserted = upsert_articles(db, feed_id, fetched.parsed.articles)
    total = count_articles(db, feed_id)
    new_articles = max(0, total - before)

    interval = next_interval(current_interval, "new" if new_articles else "unchanged")
    db.table("feeds").update({
        "last_fetched_at": now.isoformat(),
        "article_count": total,
        "consecutive_failures": 0,
        "health_score": 100,
        "last_failure_reason": None,
        "etag": fetched.etag,
        "last_modified": fetched.last_modified,
        **_schedule(interval, now),
    }).eq("id", feed_id).execute()

    return RefreshResult(
        feed_id=feed_id,
        status="updated",
        new_articles=new_articles,
        upserted=upserted,
        total_articles=total,
    )


async def refresh_due(
    db: "Client", limit: int | None = None, max_concurrency: int | None = None
) -> list[RefreshResult]:
    """Refresh every feed currently due, bounded by `limit` and `max_concurrency`.

    A large catalog would otherwise stampede: without the semaphore, one cycle
    opens one connection per due feed simultaneously.
    """
    limit = limit or batch_size()
    max_concurrency = max_concurrency or concurrency()

    feeds = select_due_feeds(db, limit)
    if not feeds:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _guarded(feed: dict) -> RefreshResult:
        async with semaphore:
            return await refresh_one(db, feed)

    settled = await asyncio.gather(
        *(_guarded(feed) for feed in feeds), return_exceptions=True
    )

    results: list[RefreshResult] = []
    for feed, outcome in zip(feeds, settled):
        if isinstance(outcome, BaseException):
            # refresh_one already absorbs fetch failures, so reaching here means
            # something unexpected broke (a bad row shape, a DB error). Log it
            # and keep the rest of the batch's results.
            logger.exception(
                "Unexpected error refreshing feed %s", feed.get("id"), exc_info=outcome
            )
            results.append(
                RefreshResult(
                    feed_id=str(feed.get("id")), status="failed", error=str(outcome)[:500]
                )
            )
        else:
            results.append(outcome)
    return results


def summarize(results: list[RefreshResult]) -> dict[str, int]:
    """Counts by status plus totals, for endpoint responses and worker logs."""
    return {
        "processed": len(results),
        "updated": sum(1 for r in results if r.status == "updated"),
        "not_modified": sum(1 for r in results if r.status == "not_modified"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "archived": sum(1 for r in results if r.archived),
        "new_articles": sum(r.new_articles for r in results),
    }
