from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from auth import AuthUser, get_optional_user
from database import get_client
from models import (
    ArticleSearchResult,
    FeedSearchResult,
    PaginatedArticleSearchResults,
    PaginatedFeedSearchResults,
)
from utils import decode_rank_cursor, encode_rank_cursor

router = APIRouter(prefix="/search", tags=["search"])

_MAX_QUERY_LEN = 200


@router.get("/articles", response_model=PaginatedArticleSearchResults)
async def search_articles(
    q: str = Query(..., min_length=1, max_length=_MAX_QUERY_LEN),
    language: str | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    user: AuthUser | None = Depends(get_optional_user),
    db: Client = Depends(get_client),
) -> PaginatedArticleSearchResults:
    """Article full-text search (TODO.md P2 「全文搜尋」) — title/summary/author/
    content, public endpoint like GET /feeds/{id}/articles: an authenticated
    caller gets their own read/bookmark state resolved per row, an anonymous
    one gets both false rather than the request failing. Kept separate from
    search_feeds below per that item's "Feed 名稱／描述搜尋與文章搜尋分開呈現"."""
    cursor_rank: float | None = None
    cursor_sort_at: str | None = None
    cursor_id: str | None = None
    if cursor:
        try:
            cursor_rank, cursor_sort_at, cursor_id = decode_rank_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc

    result = db.rpc(
        "search_articles",
        {
            "p_query": q,
            "p_user_id": user.id if user else None,
            "p_language": language,
            "p_cursor_rank": cursor_rank,
            "p_cursor_sort_at": cursor_sort_at,
            "p_cursor_id": cursor_id,
            "p_limit": limit,
        },
    ).execute()

    items = [ArticleSearchResult(**row) for row in result.data]
    next_cursor = None
    if len(items) == limit:
        last = items[-1]
        next_cursor = encode_rank_cursor(
            last.rank, last.published_at or last.fetched_at, last.id
        )
    return PaginatedArticleSearchResults(items=items, next_cursor=next_cursor)


@router.get("/feeds", response_model=PaginatedFeedSearchResults)
async def search_feeds(
    q: str = Query(..., min_length=1, max_length=_MAX_QUERY_LEN),
    language: str | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Client = Depends(get_client),
) -> PaginatedFeedSearchResults:
    """Feed name/description search — public, excludes archived feeds like
    GET /feeds already does. Separate result shape and endpoint from
    search_articles above, not a shared "search everything" response."""
    cursor_rank: float | None = None
    cursor_created_at: str | None = None
    cursor_id: str | None = None
    if cursor:
        try:
            cursor_rank, cursor_created_at, cursor_id = decode_rank_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc

    result = db.rpc(
        "search_feeds",
        {
            "p_query": q,
            "p_language": language,
            "p_cursor_rank": cursor_rank,
            "p_cursor_created_at": cursor_created_at,
            "p_cursor_id": cursor_id,
            "p_limit": limit,
        },
    ).execute()

    items = [FeedSearchResult(**row) for row in result.data]
    next_cursor = None
    if len(items) == limit:
        last = items[-1]
        next_cursor = encode_rank_cursor(last.rank, last.created_at, last.id)
    return PaginatedFeedSearchResults(items=items, next_cursor=next_cursor)
