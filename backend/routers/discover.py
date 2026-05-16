from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import AuthUser, get_optional_user
from database import get_client
from models import (
    DiscoveredFeed,
    DiscoverImportRequest,
    DiscoverRequest,
    DiscoverResponse,
    Feed,
)
from rss_parser import fetch_and_parse
from services.feed_discovery import DiscoveryError, discover_feeds

router = APIRouter(prefix="/discover", tags=["discover"])


@router.post("", response_model=DiscoverResponse)
async def discover(
    body: DiscoverRequest,
    db: Client = Depends(get_client),
) -> DiscoverResponse:
    try:
        candidates = await discover_feeds(body.url)
    except DiscoveryError as e:
        raise HTTPException(status_code=400, detail=str(e))

    feed_urls = [c.feed_url for c in candidates]
    existing: dict[str, str] = {}
    if feed_urls:
        rows = db.table("feeds").select("id,url").in_("url", feed_urls).execute()
        existing = {row["url"]: row["id"] for row in rows.data}

    result = [
        DiscoveredFeed(
            feed_url=c.feed_url,
            title=c.title,
            website_url=c.website_url,
            already_exists=c.feed_url in existing,
            existing_feed_id=UUID(existing[c.feed_url]) if c.feed_url in existing else None,
        )
        for c in candidates
    ]
    return DiscoverResponse(source_url=body.url, candidates=result)


@router.post("/import", response_model=Feed)
async def discover_and_import(
    body: DiscoverImportRequest,
    user: AuthUser | None = Depends(get_optional_user),
    db: Client = Depends(get_client),
) -> Feed:
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
    feed = Feed(**result.data[0])

    if user:
        db.table("user_feeds").upsert(
            {"user_id": user.id, "feed_id": str(feed.id)},
            on_conflict="user_id,feed_id",
        ).execute()

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for article in parsed.articles:
        if not article.url:
            continue
        article_data = {
            "feed_id": str(feed.id),
            "title": article.title,
            "url": article.url,
            "summary": article.summary,
            "content": article.content,
            "author": article.author,
            "published_at": article.published_at.isoformat()
            if article.published_at
            else None,
        }
        res = db.table("articles").upsert(article_data, on_conflict="url").execute()
        if res.data:
            inserted += 1
    db.table("feeds").update(
        {"last_fetched_at": now, "article_count": inserted}
    ).eq("id", str(feed.id)).execute()

    return feed
