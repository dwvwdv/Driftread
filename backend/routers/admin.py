from __future__ import annotations
import os
import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from supabase import Client

from database import get_client
from models import (
    DiscoverImportRequest,
    Feed,
    FeedCreate,
    FeedHealthSummary,
    ImportFeedsRequest,
    PaginatedFeeds,
    RefreshDueSummary,
)
from rss_parser import fetch_and_parse
from services.feed_discovery import DiscoveryError, validate_fetch_url
from services.feed_refresh import (
    AUTO_ARCHIVE_FAILURE_THRESHOLD,  # noqa: F401  (re-exported for existing importers)
    batch_size,
    concurrency,
    refresh_due,
    refresh_one,
    summarize,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_api_key(x_api_key: str = Header(...)) -> None:
    expected = os.getenv("ADMIN_API_KEY", "")
    # compare_digest() requires ASCII-only str (raises TypeError otherwise);
    # comparing as UTF-8 bytes accepts any header value instead of 500ing.
    if not expected or not secrets.compare_digest(
        x_api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid API key")


@router.post("/feeds", response_model=list[Feed], dependencies=[Depends(_require_api_key)])
async def import_feeds(
    body: ImportFeedsRequest,
    db: Client = Depends(get_client),
) -> list[Feed]:
    created: list[Feed] = []
    now = datetime.now(timezone.utc).isoformat()
    for feed_in in body.feeds:
        data = feed_in.model_dump()
        # Fetching inline would make a bulk import of hundreds of feeds time
        # out; mark them due instead and let the scheduler do the first fetch.
        data["next_fetch_at"] = now
        result = (
            db.table("feeds")
            .upsert(data, on_conflict="url")
            .execute()
        )
        if result.data:
            created.append(Feed(**result.data[0]))
    return created


@router.patch("/feeds/{feed_id}/archive", response_model=Feed, dependencies=[Depends(_require_api_key)])
async def archive_feed(
    feed_id: UUID,
    db: Client = Depends(get_client),
) -> Feed:
    result = db.table("feeds").select("*").eq("id", str(feed_id)).maybe_single().execute()
    # postgrest-py has shipped versions where maybe_single().execute() returns
    # bare None on 0 rows instead of a response object with data=None; guard
    # both shapes rather than relying on result.data alone.
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Feed not found")

    now = datetime.now(timezone.utc).isoformat()
    updated = (
        db.table("feeds")
        .update({"archived_at": now})
        .eq("id", str(feed_id))
        .execute()
    )
    return Feed(**updated.data[0])


@router.patch("/feeds/{feed_id}/unarchive", response_model=Feed, dependencies=[Depends(_require_api_key)])
async def unarchive_feed(
    feed_id: UUID,
    db: Client = Depends(get_client),
) -> Feed:
    result = db.table("feeds").select("*").eq("id", str(feed_id)).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Feed not found")

    updated = (
        db.table("feeds")
        .update({"archived_at": None})
        .eq("id", str(feed_id))
        .execute()
    )
    return Feed(**updated.data[0])


@router.post("/feeds/{feed_id}/refresh", response_model=dict, dependencies=[Depends(_require_api_key)])
async def refresh_feed(
    feed_id: UUID,
    db: Client = Depends(get_client),
) -> dict:
    feed_result = db.table("feeds").select("*").eq("id", str(feed_id)).maybe_single().execute()
    if not feed_result or not feed_result.data:
        raise HTTPException(status_code=404, detail="Feed not found")

    # All the fetch / health / backoff bookkeeping lives in feed_refresh so the
    # scheduler behaves identically to this endpoint.
    result = await refresh_one(db, feed_result.data)
    if result.status == "failed":
        raise HTTPException(status_code=502, detail="Failed to fetch feed")

    # Response key stays "inserted" with its original rows-touched meaning —
    # the browser extension and any external scripts already read it.
    return {
        "inserted": result.upserted,
        "feed_id": str(feed_id),
        "status": result.status,
        "new_articles": result.new_articles,
        "total_articles": result.total_articles,
    }


@router.post("/feeds/from-url", response_model=Feed, dependencies=[Depends(_require_api_key)])
async def import_feed_from_url(
    body: DiscoverImportRequest,
    db: Client = Depends(get_client),
) -> Feed:
    """API-key gated alternative to /discover/import: import a single feed by URL."""
    try:
        safe_url = validate_fetch_url(body.feed_url)
    except DiscoveryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        parsed = await fetch_and_parse(safe_url)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to fetch feed")

    feed_data = {
        "title": parsed.title,
        "url": safe_url,
        "description": parsed.description,
        "website_url": parsed.website_url,
        "language": parsed.language,
        # Metadata only here — the scheduler picks it up immediately and does
        # the first article fetch.
        "next_fetch_at": datetime.now(timezone.utc).isoformat(),
    }
    result = db.table("feeds").upsert(feed_data, on_conflict="url").execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to upsert feed")
    return Feed(**result.data[0])


@router.post(
    "/feeds/refresh-due",
    response_model=RefreshDueSummary,
    dependencies=[Depends(_require_api_key)],
)
async def refresh_due_feeds(
    limit: int | None = Query(None, ge=1, le=500),
    max_concurrency: int | None = Query(None, ge=1, le=20),
    db: Client = Depends(get_client),
) -> RefreshDueSummary:
    """Run one pass over the due queue.

    The worker container does this on a timer; this endpoint exists to kick a
    cycle by hand and to let a deployment without the worker drive refreshes
    from an external scheduler.

    Both bounds default to the FEED_REFRESH_* env values when omitted, so this
    endpoint and the worker behave the same unless deliberately overridden.
    """
    results = await refresh_due(
        db,
        limit=limit or batch_size(),
        max_concurrency=max_concurrency or concurrency(),
    )
    return RefreshDueSummary(**summarize(results))


@router.get("/feeds", response_model=PaginatedFeeds, dependencies=[Depends(_require_api_key)])
async def list_all_feeds(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    archived: bool | None = None,
    db: Client = Depends(get_client),
) -> PaginatedFeeds:
    """Paginated list of every feed, archived included.

    The public GET /feeds hides archived rows and the other admin lists are
    filtered by health or archival, so nothing could enumerate the whole
    catalog — which an external scheduler needs.
    """
    offset = (page - 1) * page_size
    query = db.table("feeds").select("*", count="exact")
    if archived is True:
        query = query.not_.is_("archived_at", "null")
    elif archived is False:
        query = query.is_("archived_at", "null")

    result = (
        query.range(offset, offset + page_size - 1)
        .order("next_fetch_at")
        .execute()
    )
    return PaginatedFeeds(
        items=[Feed(**row) for row in result.data],
        total=result.count or 0,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/feeds/unhealthy",
    response_model=list[FeedHealthSummary],
    dependencies=[Depends(_require_api_key)],
)
async def list_unhealthy_feeds(
    threshold: int = 50,
    limit: int = Query(200, ge=1, le=1000),
    db: Client = Depends(get_client),
) -> list[FeedHealthSummary]:
    """List feeds with health_score below threshold, ordered worst-first."""
    rows = (
        db.table("feeds")
        .select(
            "id,title,url,health_score,consecutive_failures,"
            "last_failure_at,last_failure_reason,last_fetched_at"
        )
        .lte("health_score", threshold)
        .is_("archived_at", "null")
        .order("health_score")
        .limit(limit)
        .execute()
    )
    return [FeedHealthSummary(**row) for row in rows.data]


@router.get("/feeds/archived", response_model=list[Feed], dependencies=[Depends(_require_api_key)])
async def list_archived_feeds(
    limit: int = Query(200, ge=1, le=1000),
    db: Client = Depends(get_client),
) -> list[Feed]:
    result = (
        db.table("feeds")
        .select("*")
        .not_.is_("archived_at", "null")
        .order("archived_at", desc=True)
        .limit(limit)
        .execute()
    )
    return [Feed(**row) for row in result.data]
