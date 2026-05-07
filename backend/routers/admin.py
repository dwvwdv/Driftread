from __future__ import annotations
import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from supabase import Client

from database import get_client
from models import Feed, FeedCreate, ImportFeedsRequest
from rss_parser import fetch_and_parse

router = APIRouter(prefix="/admin", tags=["admin"])


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
    try:
        parsed = await fetch_and_parse(feed_url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch feed: {e}")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    inserted = 0
    for article in parsed.articles:
        if not article.url:
            continue
        data = {
            "feed_id": str(feed_id),
            "title": article.title,
            "url": article.url,
            "summary": article.summary,
            "content": article.content,
            "author": article.author,
            "published_at": article.published_at.isoformat() if article.published_at else None,
        }
        result = db.table("articles").upsert(data, on_conflict="url").execute()
        if result.data:
            inserted += 1

    db.table("feeds").update({
        "last_fetched_at": now,
        "article_count": inserted,
    }).eq("id", str(feed_id)).execute()

    return {"inserted": inserted, "feed_id": str(feed_id)}


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
