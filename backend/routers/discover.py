from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from auth import AuthUser, get_current_user
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

    # feed_url is lifted straight out of remote HTML (<link rel="alternate">
    # hrefs on whatever page body.url points at — see _extract_feed_links()),
    # so it's attacker-influenced third-party text, not something this app
    # authored. A single .in_("url", feed_urls) call would splice every one of
    # those strings into one PostgREST filter's comma/paren-delimited value
    # list — the same class of filter-injection PR #14 fixed for .or_(), just
    # reachable through .in_()'s list serialization instead of hand-built
    # string concatenation (docs/SECURITY.md #24). Looking each URL up on its
    # own via .eq().maybe_single() keeps every third-party string out of
    # PostgREST's filter mini-language entirely, matching the pattern
    # services/discovery_candidates.py already uses for the same reason.
    existing: dict[str, str] = {}
    for feed_url in {c.feed_url for c in candidates}:
        row = db.table("feeds").select("id,url").eq("url", feed_url).maybe_single().execute()
        if row and row.data:
            existing[row.data["url"]] = row.data["id"]

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
    # Requires a permanent account: this writes straight into the global
    # `feeds` catalog (title/description/language are attacker-influenced
    # third-party text lifted from whatever body.feed_url serves — see the
    # comment on discover() above). Rate limiting alone doesn't stop
    # catalog pollution across rotated IPs; requiring a real account does
    # (docs/FEATURES.md, TODO.md "Auth 與安全"). Anonymous readers can still
    # explore results via POST /discover, which never writes.
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> Feed:
    try:
        safe_url = await validate_fetch_url(body.feed_url)
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
