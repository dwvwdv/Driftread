from __future__ import annotations
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from database import get_client
from models import Feed, FeedWithArticles, PaginatedFeeds
from utils import escape_postgrest_literal

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("", response_model=PaginatedFeeds)
async def list_feeds(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = None,
    tag: str | None = None,
    search: str | None = Query(default=None, max_length=200),
    db: Client = Depends(get_client),
) -> PaginatedFeeds:
    offset = (page - 1) * page_size

    query = db.table("feeds").select("*", count="exact").is_("archived_at", "null")

    if category:
        query = query.eq("category", category)
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
    result = db.table("feeds").select("category").is_("archived_at", "null").execute()
    categories = {row["category"] for row in result.data if row.get("category")}
    return sorted(categories)


@router.get("/{feed_id}", response_model=FeedWithArticles)
async def get_feed(feed_id: UUID, db: Client = Depends(get_client)) -> FeedWithArticles:
    result = db.table("feeds").select("*").eq("id", str(feed_id)).maybe_single().execute()
    if not result.data:
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
