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
from rate_limit import rate_limit
from rss_parser import fetch_and_parse
from services.articles import upsert_articles
from services.feed_discovery import DiscoveryError, discover_feeds, validate_fetch_url

router = APIRouter(prefix="/discover", tags=["discover"])


@router.post(
    "",
    response_model=DiscoverResponse,
    dependencies=[Depends(rate_limit("discover"))],
)
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


@router.post(
    "/import",
    response_model=Feed,
    dependencies=[Depends(rate_limit("discover_import"))],
)
async def discover_and_import(
    body: DiscoverImportRequest,
    user: AuthUser | None = Depends(get_optional_user),
    db: Client = Depends(get_client),
) -> Feed:
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
    inserted = upsert_articles(db, str(feed.id), parsed.articles)
    db.table("feeds").update(
        {"last_fetched_at": now, "article_count": inserted}
    ).eq("id", str(feed.id)).execute()

    return feed
