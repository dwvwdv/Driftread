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

import httpx

from env_utils import env_flag, env_int
from rss_parser import fetch_and_parse_conditional
from services.articles import upsert_articles
from services.feed_discovery import validate_fetch_url
from services.language import resolve_feed_language
from services.source_identity import (
    MIRROR_MIN_ITEMS,
    canonical_item_urls,
    is_comment_feed,
    new_source_rejection,
)

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)
AUTO_ARCHIVE_FAILURE_THRESHOLD = 10
_ADMISSION_LOCK = asyncio.Lock()
ADMISSION_EXCLUSION_PREFIX = "Excluded before admission"
DEFAULT_INTERVAL_MINUTES = 60
Outcome = Literal["new", "unchanged", "failed"]
Status = Literal["updated", "not_modified", "failed"]


def _env_int(name: str, default: int) -> int:
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
    new_articles: int = 0
    upserted: int = 0
    total_articles: int | None = None
    error: str | None = None
    archived: bool = False


def next_interval(current: int, outcome: Outcome) -> int:
    floor = min_interval_minutes()
    ceiling = max_interval_minutes()
    if ceiling < floor:
        ceiling = floor
    current = max(current or DEFAULT_INTERVAL_MINUTES, 1)
    if outcome == "new":
        candidate = max(floor, current // 2)
    else:
        candidate = max(current, floor) * 2
    return max(floor, min(ceiling, candidate))


def count_articles(db: "Client", feed_id: str) -> int:
    result = (
        db.table("articles")
        .select("id", count="exact", head=True)
        .eq("feed_id", feed_id)
        .execute()
    )
    count = getattr(result, "count", None)
    if count is None:
        raise RuntimeError("Article count unavailable")
    return count


def select_due_feeds(db: "Client", limit: int) -> list[dict]:
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


async def refresh_one(db: "Client", feed: dict, *, force: bool = False) -> RefreshResult:
    """Re-fetch one feed, upsert articles, enrich language and update health."""
    feed_id = str(feed["id"])
    feed_url: str = feed["url"]

    # Persist the attempt before external I/O; a killed worker still leaves
    # evidence that an attempt started. Only successful fetch/parse advances ok.
    started = datetime.now(timezone.utc).isoformat()
    db.rpc("record_source_fetch", {"p_feed_id": feed_id, "p_at": started, "p_ok": False}).execute()
    diagnostics = {"outcome": "in_progress", "at": started}
    db.table("feeds").update({"last_fetch_result": diagnostics}, returning="minimal").eq("id", feed_id).execute()
    try:
        safe_url = await validate_fetch_url(feed_url)
        fetched = await fetch_and_parse_conditional(
            safe_url,
            etag=feed.get("etag") if not force and feed.get("article_count") != 0 else None,
            last_modified=feed.get("last_modified") if not force and feed.get("article_count") != 0 else None,
        )
    except Exception as exc:
        outcome = "fetch_failed"
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            outcome = "fetch_timeout"
        elif isinstance(exc, httpx.HTTPStatusError):
            outcome = "http_error"
            diagnostics["http_status"] = exc.response.status_code
        elif isinstance(exc, ValueError):
            outcome = "parse_failed"
        return _record_failure(db, feed, diagnostics | {"outcome": outcome})

    try:
        return await _store_fetched(db, feed, fetched, diagnostics)
    except Exception:
        # A parsed HTTP response is not evidence that its articles were saved.
        # Do not persist database/provider exception payloads or validators from
        # an incomplete ingest; the next attempt must download the body again.
        return _record_failure(db, feed, diagnostics | {"outcome": "ingest_failed"})


def _record_failure(db, feed, diagnostics) -> RefreshResult:
    now = datetime.now(timezone.utc)
    failures = (feed.get("consecutive_failures") or 0) + 1
    archived = failures >= AUTO_ARCHIVE_FAILURE_THRESHOLD
    outcome = diagnostics["outcome"]
    update = {
        "consecutive_failures": failures,
        "last_failure_at": now.isoformat(),
        "last_failure_reason": outcome,
        "last_fetch_result": diagnostics,
        "health_score": max(0, 100 - failures * 10),
        **_schedule(next_interval(feed.get("fetch_interval_minutes") or DEFAULT_INTERVAL_MINUTES, "failed"), now),
    }
    if archived:
        update["archived_at"] = now.isoformat()
    db.table("feeds").update(update, returning="minimal").eq("id", str(feed["id"])).execute()
    return RefreshResult(feed_id=str(feed["id"]), status="failed", error=outcome, archived=archived)


async def _store_fetched(db, feed, fetched, diagnostics) -> RefreshResult:
    feed_id = str(feed["id"])
    feed_url = feed["url"]
    current_interval = feed.get("fetch_interval_minutes") or DEFAULT_INTERVAL_MINUTES

    now = datetime.now(timezone.utc)
    db.rpc("record_source_fetch", {"p_feed_id": feed_id, "p_at": now.isoformat(), "p_ok": True}).execute()
    if fetched.not_modified:
        interval = next_interval(current_interval, "unchanged")
        db.table("feeds").update({
            "last_fetched_at": now.isoformat(),
            "consecutive_failures": 0,
            "health_score": 100,
            "last_failure_reason": None,
            "last_fetch_result": diagnostics | {"outcome": "not_modified"},
            "etag": fetched.etag,
            "last_modified": fetched.last_modified,
            **_schedule(interval, now),
        }, returning="minimal").eq("id", feed_id).execute()
        return RefreshResult(feed_id=feed_id, status="not_modified")

    assert fetched.parsed is not None
    urls = [article.url for article in fetched.parsed.articles if article.url]
    diagnostics.update(entries_seen=len(fetched.parsed.articles),
                       missing_url=len(fetched.parsed.articles) - len(urls),
                       duplicate_entries=len(urls) - len(set(urls)))
    before = count_articles(db, feed_id)
    initial = not feed.get("last_fetched_at")
    # A row without the column comes from a database the migration has not
    # reached yet (worker started ahead of the api); keep the old behavior.
    gated = "admitted_at" in feed and not feed["admitted_at"]
    if gated:
        # Until a source is admitted (enough stored items to have been
        # judged, or an administrator's override), every fetch re-runs the
        # gate. One process-wide lock covers the check and the write, so
        # aliases promoted together and refreshed in the same batch cannot
        # both pass before either stores its items.
        async with _ADMISSION_LOCK:
            rejection = await asyncio.to_thread(_admission_rejection, db, feed_id, feed_url, fetched.parsed)
            if rejection:
                db.table("feeds").update({
                    "archived_at": now.isoformat(),
                    "last_fetched_at": now.isoformat(),
                    "last_failure_reason": rejection,
                    "last_fetch_result": diagnostics | {"outcome": "excluded"},
                }, returning="minimal").eq("id", feed_id).execute()
                logger.info("Archived %s before admission: %s", feed_url, rejection)
                return RefreshResult(feed_id=feed_id, status="failed", error=rejection, archived=True)
            upserted = await _ingest(db, feed_id, fetched.parsed, initial)
    else:
        upserted = await _ingest(db, feed_id, fetched.parsed, initial)
    total = count_articles(db, feed_id)
    new_articles = max(0, total - before)
    outcome = "updated" if new_articles else "unchanged"
    if not fetched.parsed.articles:
        outcome = "empty_feed"
    elif not urls:
        outcome = "no_usable_entries"
    interval = next_interval(current_interval, "new" if new_articles else "unchanged")

    # Never overwrite an existing/manual classification with detector output.
    # Missing languages are filled from publisher metadata first, then from a
    # conservative text detector. Existing locale-ish values are canonicalized
    # to the same primary-language buckets used by filters and recommendations.
    language = resolve_feed_language(fetched.parsed, existing=feed.get("language"))
    update = {
        "last_fetched_at": now.isoformat(),
        "article_count": total,
        "consecutive_failures": 0,
        "health_score": 100,
        "last_failure_reason": None,
        "last_fetch_result": diagnostics | {"outcome": outcome, "upserted": upserted,
                                               "new_articles": new_articles, "total_articles": total},
        "etag": fetched.etag,
        "last_modified": fetched.last_modified,
        **_schedule(interval, now),
    }
    if language:
        update["language"] = language
    # Admit only after a fetch whose own items were enough to judge. The
    # stored total is not the evidence the gate saw: a feed that only ever
    # shows three items would otherwise be admitted unchecked once three
    # fetches had accumulated five articles.
    if gated and len(canonical_item_urls([a.url for a in fetched.parsed.articles])) >= MIRROR_MIN_ITEMS:
        update["admitted_at"] = now.isoformat()
    # returning="minimal" — same reason as the two update() calls above.
    db.table("feeds").update(update, returning="minimal").eq("id", feed_id).execute()

    return RefreshResult(
        feed_id=feed_id,
        status="updated",
        new_articles=new_articles,
        upserted=upserted,
        total_articles=total,
    )


async def _ingest(db: "Client", feed_id: str, parsed, initial: bool) -> int:
    # Batch ingestion includes synchronous PostgREST calls and HTML parsing.
    # Keep worker heartbeat and other async refreshes responsive during it.
    return await asyncio.to_thread(
        upsert_articles, db, feed_id, parsed.articles,
        backfill=initial,
        backfill_reason="initial_fetch" if initial else None,
    )


def _admission_rejection(db: "Client", feed_id: str, feed_url: str, parsed) -> str | None:
    """Keep comment feeds and mirrors out before their items are stored.

    Candidate promotion and bulk JSON import never fetch, so the scheduled
    fetches are the only point that sees their items. A source someone
    already subscribed to is left alone: archiving it would silently empty
    that reader's subscription, and every subscribing path already ran this
    gate before creating the row.
    """
    if is_comment_feed(feed_url):
        rejection = ("comment_feed", None)
    else:
        rejection = new_source_rejection(
            db, [article.url for article in parsed.articles], exclude_feed_id=feed_id,
        )
    if not rejection:
        return None
    subscribers = (
        db.table("user_feeds").select("feed_id", count="exact", head=True)
        .eq("feed_id", feed_id).execute()
    )
    if getattr(subscribers, "count", None):
        return None
    kind, mirror = rejection
    if kind == "comment_feed":
        return f"{ADMISSION_EXCLUSION_PREFIX}: comment feed"
    return f"{ADMISSION_EXCLUSION_PREFIX}: duplicates existing source {mirror['feed_id']}"


async def refresh_due(
    db: "Client", limit: int | None = None, max_concurrency: int | None = None
) -> list[RefreshResult]:
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
            logger.error("Unexpected error refreshing feed %s", feed.get("id"))
            results.append(
                RefreshResult(
                    feed_id=str(feed.get("id")), status="failed", error="refresh_failed"
                )
            )
        else:
            results.append(outcome)
    return results


def summarize(results: list[RefreshResult]) -> dict[str, int]:
    return {
        "processed": len(results),
        "updated": sum(1 for r in results if r.status == "updated"),
        "not_modified": sum(1 for r in results if r.status == "not_modified"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "archived": sum(1 for r in results if r.archived),
        "new_articles": sum(r.new_articles for r in results),
    }
