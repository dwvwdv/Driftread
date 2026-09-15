from __future__ import annotations
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from database import get_client
from models import Feed, FeedWithArticles, PaginatedFeeds
from utils import escape_postgrest_literal

router = APIRouter(prefix="/feeds", tags=["feeds"])

# Explicit column list, not select("*") — migration 020 added feeds.search_vector,
# a generated tsvector indexing up to 100,000 characters of description. A
# wildcard select would fetch and serialize it from PostgREST on every feed
# listing/detail read even though the Feed response model never uses it
# (PR #59 review, P2) — a real cost here since list_feeds can return up to 100
# rows per page.
_FEED_COLUMNS = (
    "id,title,url,description,website_url,language,category,tags,article_count,"
    "last_fetched_at,archived_at,created_at,updated_at,fetch_interval_minutes,"
    "next_fetch_at,etag,last_modified"
)


@router.get("", response_model=PaginatedFeeds)
async def list_feeds(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = None,
    language: str | None = None,
    tag: str | None = None,
    search: str | None = Query(default=None, max_length=200),
    db: Client = Depends(get_client),
) -> PaginatedFeeds:
    offset = (page - 1) * page_size

    query = db.table("feeds").select(_FEED_COLUMNS, count="exact").is_("archived_at", "null")

    if category:
        query = query.eq("category", category)
    if language:
        query = query.eq("language", language)
    if tag:
        query = query.contains("tags", [tag])
    if search:
        pattern = escape_postgrest_literal(f"%{search}%")
        query = query.or_(f"title.ilike.{pattern},description.ilike.{pattern}")

    result = query.range(offset, offset + page_size - 1).order("created_at", desc=True).execute()

    return PaginatedFeeds(
        items=[Feed(**row) for row in result.data],
        total=result.count or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/categories", response_model=list[str])
async def list_categories(db: Client = Depends(get_client)) -> list[str]:
    result = db.rpc("list_feed_categories", {}).execute()
    return [row["category"] for row in result.data]


@router.get("/languages", response_model=list[str])
async def list_languages(db: Client = Depends(get_client)) -> list[str]:
    result = db.rpc("list_feed_languages", {}).execute()
    return [row["language"] for row in result.data]


@router.get("/{feed_id}", response_model=FeedWithArticles)
async def get_feed(feed_id: UUID, db: Client = Depends(get_client)) -> FeedWithArticles:
    result = db.table("feeds").select(_FEED_COLUMNS).eq("id", str(feed_id)).maybe_single().execute()
    # postgrest-py has shipped versions where maybe_single().execute() returns
    # bare None on 0 rows instead of a response object with data=None; guard
    # both shapes rather than relying on result.data alone.
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Feed not found")

    articles_result = (
        db.table("articles")
        .select("id,feed_id,title,url,summary,author,published_at")
        .eq("feed_id", str(feed_id))
        .order("published_at", desc=True)
        .limit(10)
        .execute()
    )

    feed_data = result.data
    feed_data["articles"] = articles_result.data
    return FeedWithArticles(**feed_data)
