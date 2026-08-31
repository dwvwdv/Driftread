from __future__ import annotations
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from auth import AuthUser, get_optional_user
from database import get_client
from models import Article, FeedArticle, PaginatedFeedArticles
from utils import decode_keyset_cursor, encode_keyset_cursor

router = APIRouter(tags=["articles"])


@router.get("/feeds/{feed_id}/articles", response_model=PaginatedFeedArticles)
async def list_articles(
    feed_id: UUID,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    user: AuthUser | None = Depends(get_optional_user),
    db: Client = Depends(get_client),
) -> PaginatedFeedArticles:
    """Feed detail's full article list (TODO.md "Feed 完整文章列表") — cursor
    (keyset) paginated the same way GET /me/stream is, rather than the old
    offset pagination (page/page_size), which could repeat or skip articles
    across pages as new ones were ingested. Public endpoint; when the caller
    is signed in, each row also carries their own read/bookmark state (both
    false when anonymous)."""
    cursor_sort_at: str | None = None
    cursor_id: str | None = None
    if cursor:
        try:
            cursor_sort_at, cursor_id = decode_keyset_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc

    result = db.rpc(
        "list_feed_articles",
        {
            "p_feed_id": str(feed_id),
            "p_user_id": user.id if user else None,
            "p_cursor_sort_at": cursor_sort_at,
            "p_cursor_id": cursor_id,
            "p_limit": limit,
        },
    ).execute()

    items = [FeedArticle(**row) for row in result.data]
    next_cursor = None
    if len(items) == limit:
        last = items[-1]
        next_cursor = encode_keyset_cursor(last.published_at or last.fetched_at, last.id)
    return PaginatedFeedArticles(items=items, next_cursor=next_cursor)


@router.get("/articles/{article_id}", response_model=Article)
async def get_article(article_id: UUID, db: Client = Depends(get_client)) -> Article:
    result = (
        db.table("articles")
        .select("*")
        .eq("id", str(article_id))
        .maybe_single()
        .execute()
    )
    # postgrest-py has shipped versions where maybe_single().execute() returns
    # bare None on 0 rows instead of a response object with data=None; guard
    # both shapes rather than relying on result.data alone.
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Article not found")
    return Article(**result.data)
