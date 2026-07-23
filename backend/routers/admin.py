from __future__ import annotations
import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from supabase import Client

from database import get_client
from models import (
    DiscoverImportRequest,
    Feed,
    FeedCreate,
    FeedHealthSummary,
    ImportFeedsRequest,
)
from rss_parser import fetch_and_parse
from services.articles import upsert_articles

router = APIRouter(prefix="/admin", tags=["admin"])

AUTO_ARCHIVE_FAILURE_THRESHOLD = 10


def _require_api_key(x_api_key: str = Header(...)) -> None:
    expected = os.getenv("ADMIN_API_KEY", "")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid API key")


@router.post("/feeds", response_model=list[Feed], dependencies=[Depends(_require_api_key)])
async def import_feeds(
    body: ImportFeedsRequest,
    db: Client = Depends(get_client),
) -> list[Feed]:
    created: list[Feed] = []
    for feed_in in body.feeds:
        data = feed_in.model_dump()
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
    result = db.table("feeds").select("*").eq("id", str(feed_id)).single().execute()
    if not result.data:
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
    result = db.table("feeds").select("*").eq("id", str(feed_id)).single().execute()
    if not result.data:
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
    feed_result = db.table("feeds").select("*").eq("id", str(feed_id)).single().execute()
    if not feed_result.data:
        raise HTTPException(status_code=404, detail="Feed not found")

    feed_url: str = feed_result.data["url"]
    current_failures: int = feed_result.data.get("consecutive_failures") or 0
    try:
        parsed = await fetch_and_parse(feed_url)
    except Exception as e:
        failures = current_failures + 1
        update = {
            "consecutive_failures": failures,
            "last_failure_at": datetime.now(timezone.utc).isoformat(),
            "last_failure_reason": str(e)[:500],
            "health_score": max(0, 100 - failures * 10),
        }
        if failures >= AUTO_ARCHIVE_FAILURE_THRESHOLD:
            update["archived_at"] = datetime.now(timezone.utc).isoformat()
        db.table("feeds").update(update).eq("id", str(feed_id)).execute()
        raise HTTPException(status_code=502, detail=f"Failed to fetch feed: {e}")

    now = datetime.now(timezone.utc).isoformat()
    inserted = upsert_articles(db, str(feed_id), parsed.articles)

    db.table("feeds").update({
        "last_fetched_at": now,
        "article_count": inserted,
        "consecutive_failures": 0,
        "health_score": 100,
        "last_failure_reason": None,
    }).eq("id", str(feed_id)).execute()

    return {"inserted": inserted, "feed_id": str(feed_id)}


@router.post("/feeds/from-url", response_model=Feed, dependencies=[Depends(_require_api_key)])
async def import_feed_from_url(
    body: DiscoverImportRequest,
    db: Client = Depends(get_client),
) -> Feed:
    """API-key gated alternative to /discover/import: import a single feed by URL."""
    try:
        parsed = await fetch_and_parse(body.feed_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch feed: {e}")

    feed_data = {
        "title": parsed.title,
        "url": body.feed_url,
        "description": parsed.description,
        "website_url": parsed.website_url,
        "language": parsed.language,
    }
    result = db.table("feeds").upsert(feed_data, on_conflict="url").execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to upsert feed")
    return Feed(**result.data[0])


@router.get(
    "/feeds/unhealthy",
    response_model=list[FeedHealthSummary],
    dependencies=[Depends(_require_api_key)],
)
async def list_unhealthy_feeds(
    threshold: int = 50,
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
        .execute()
    )
    return [FeedHealthSummary(**row) for row in rows.data]


@router.get("/feeds/archived", response_model=list[Feed], dependencies=[Depends(_require_api_key)])
async def list_archived_feeds(db: Client = Depends(get_client)) -> list[Feed]:
    result = (
        db.table("feeds")
        .select("*")
        .not_.is_("archived_at", "null")
        .order("archived_at", desc=True)
        .execute()
    )
    return [Feed(**row) for row in result.data]
