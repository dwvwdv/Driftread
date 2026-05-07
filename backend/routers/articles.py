from __future__ import annotations
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from backend.database import get_client
from backend.models import Article, ArticleSummary, PaginatedArticles

router = APIRouter(tags=["articles"])


@router.get("/feeds/{feed_id}/articles", response_model=PaginatedArticles)
async def list_articles(
    feed_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Client = Depends(get_client),
) -> PaginatedArticles:
    offset = (page - 1) * page_size

    result = (
        db.table("articles")
        .select("id,feed_id,title,url,summary,author,published_at", count="exact")
        .eq("feed_id", str(feed_id))
        .range(offset, offset + page_size - 1)
        .order("published_at", desc=True)
        .execute()
    )

    return PaginatedArticles(
        items=[ArticleSummary(**row) for row in result.data],
        total=result.count or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/articles/{article_id}", response_model=Article)
async def get_article(article_id: UUID, db: Client = Depends(get_client)) -> Article:
    result = (
        db.table("articles")
        .select("*")
        .eq("id", str(article_id))
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Article not found")
    return Article(**result.data)
