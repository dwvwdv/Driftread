from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from auth import AuthUser, get_current_user
from database import get_client
from models import (
    ArticleSummary,
    Bookmark,
    BookmarkCreate,
    Feed,
    FeedUnreadCount,
    MarkAllReadRequest,
    MarkAllReadResult,
    PaginatedReads,
    PaginatedStream,
    ReadReceipt,
    StreamArticle,
    UnreadSummary,
    UserPreferences,
    UserPreferencesUpdate,
)
from utils import decode_keyset_cursor, encode_keyset_cursor, escape_postgrest_literal

router = APIRouter(prefix="/me", tags=["me"])


# --- Subscriptions -----------------------------------------------------------

@router.get("/feeds", response_model=list[Feed])
async def list_subscriptions(
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> list[Feed]:
    rows = (
        db.table("user_feeds")
        .select("feed_id, feeds(*)")
        .eq("user_id", user.id)
        .execute()
    )
    return [Feed(**row["feeds"]) for row in rows.data if row.get("feeds")]


@router.post("/feeds/{feed_id}", status_code=204)
async def subscribe(
    feed_id: UUID,
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> None:
    feed = db.table("feeds").select("id").eq("id", str(feed_id)).execute()
    if not feed.data:
        raise HTTPException(status_code=404, detail="Feed not found")
    db.table("user_feeds").upsert(
        {"user_id": user.id, "feed_id": str(feed_id)},
        on_conflict="user_id,feed_id",
    ).execute()


@router.delete("/feeds/{feed_id}", status_code=204)
async def unsubscribe(
    feed_id: UUID,
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> None:
    db.table("user_feeds").delete().eq("user_id", user.id).eq(
        "feed_id", str(feed_id)
    ).execute()


# --- Read receipts -----------------------------------------------------------

@router.post("/articles/{article_id}/read", status_code=204)
async def mark_read(
    article_id: UUID,
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> None:
    db.table("user_article_reads").upsert(
        {"user_id": user.id, "article_id": str(article_id)},
        on_conflict="user_id,article_id",
    ).execute()


def _decode_cursor_or_400(cursor: str) -> tuple[str, str]:
    """Shared by /reads and /stream — both are (timestamp, id) keyset pages
    and both want the same "malformed cursor -> 400" behavior rather than a
    decode error leaking through as an unhandled ValueError -> 500."""
    try:
        return decode_keyset_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


@router.get("/reads", response_model=PaginatedReads)
async def list_reads(
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> PaginatedReads:
    # Keyset (not offset) pagination: this table only grows with usage, and a
    # heavy reader can accumulate thousands of rows, so a plain unbounded
    # select (the previous behavior) or OFFSET-based paging would both get
    # slower — and offsets can skip/duplicate rows — as it grows. (read_at,
    # article_id) DESC is a stable sort key even when multiple rows share a
    # read_at timestamp (e.g. a bulk "mark all read").
    query = db.table("user_article_reads").select("article_id, read_at").eq("user_id", user.id)
    if cursor:
        read_at, article_id = _decode_cursor_or_400(cursor)
        read_at_lit = escape_postgrest_literal(read_at)
        article_id_lit = escape_postgrest_literal(article_id)
        query = query.or_(
            f"read_at.lt.{read_at_lit},"
            f"and(read_at.eq.{read_at_lit},article_id.lt.{article_id_lit})"
        )

    rows = (
        query.order("read_at", desc=True)
        .order("article_id", desc=True)
        .limit(limit)
        .execute()
    )

    items = [ReadReceipt(**row) for row in rows.data]
    next_cursor = (
        encode_keyset_cursor(items[-1].read_at, items[-1].article_id)
        if len(items) == limit
        else None
    )
    return PaginatedReads(items=items, next_cursor=next_cursor)


@router.delete("/articles/{article_id}/read", status_code=204)
async def mark_unread(
    article_id: UUID,
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> None:
    """The unmark counterpart of mark_read — lets a reader undo an accidental
    or premature "read" (including the automatic one article-reader fires on
    open) from the reading stream."""
    db.table("user_article_reads").delete().eq("user_id", user.id).eq(
        "article_id", str(article_id)
    ).execute()


@router.post("/reads/mark-all", response_model=MarkAllReadResult)
async def mark_all_read(
    body: MarkAllReadRequest,
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> MarkAllReadResult:
    """Two scopes, matching TODO.md's "目前頁面全部標已讀，以及明確範圍的全部標已讀":

    - `article_ids` given: exactly those articles (the "current page" case —
      the frontend already has the page's ids in hand from GET /me/stream, no
      extra round trip to recompute the same set server-side).
    - `article_ids` omitted: an explicit *filtered* scope — optionally one
      feed, optionally articles at-or-before a timestamp, or (both omitted)
      every subscribed article — evaluated server-side via
      mark_reading_stream_read() so marking, say, "this whole feed" as read
      doesn't require shipping every one of its article ids to the client
      first just to hand them back.
    """
    if body.article_ids is not None:
        if not body.article_ids:
            return MarkAllReadResult(marked=0)
        # Dedupe: a single upsert with the same article_id twice makes
        # Postgres raise "ON CONFLICT DO UPDATE command cannot affect row a
        # second time" (same constraint services/articles.py::upsert_articles
        # already dedupes around for feed ingestion).
        unique_ids = {str(aid) for aid in body.article_ids}
        rows = [{"user_id": user.id, "article_id": aid} for aid in unique_ids]
        db.table("user_article_reads").upsert(rows, on_conflict="user_id,article_id").execute()
        return MarkAllReadResult(marked=len(rows))

    result = db.rpc(
        "mark_reading_stream_read",
        {
            "p_user_id": user.id,
            "p_feed_id": str(body.feed_id) if body.feed_id else None,
            "p_before": body.before.isoformat() if body.before else None,
        },
    ).execute()
    marked = result.data[0]["marked"] if result.data else 0
    return MarkAllReadResult(marked=marked)


# --- Reading stream ------------------------------------------------------


@router.get("/stream", response_model=PaginatedStream)
async def list_stream(
    feed_id: UUID | None = None,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int = Query(30, ge=1, le=100),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> PaginatedStream:
    """The unified article timeline across every feed the caller is
    subscribed to (TODO.md "我的閱讀流") — sorted by published_at (falling
    back to fetched_at for undated articles, see migration 013), cursor
    (keyset) paginated the same way GET /me/reads is, with `unread_only` and
    `feed_id` filters and each row's read state joined in so the frontend
    doesn't need a second request per page to know what's already read."""
    cursor_sort_at: str | None = None
    cursor_id: str | None = None
    if cursor:
        cursor_sort_at, cursor_id = _decode_cursor_or_400(cursor)

    result = db.rpc(
        "list_reading_stream",
        {
            "p_user_id": user.id,
            "p_feed_id": str(feed_id) if feed_id else None,
            "p_unread_only": unread_only,
            "p_cursor_sort_at": cursor_sort_at,
            "p_cursor_id": cursor_id,
            "p_limit": limit,
        },
    ).execute()

    items = [StreamArticle(**row) for row in result.data]
    next_cursor = None
    if len(items) == limit:
        last = items[-1]
        next_cursor = encode_keyset_cursor(last.published_at or last.fetched_at, last.id)
    return PaginatedStream(items=items, next_cursor=next_cursor)


@router.get("/stream/unread-counts", response_model=UnreadSummary)
async def stream_unread_counts(
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> UnreadSummary:
    """Total unread plus a per-feed breakdown, for the stream's header and
    per-source filter chips. One RPC call rather than the client summing a
    fully-loaded stream: the stream itself is paginated and deliberately
    never loads everything at once."""
    result = db.rpc("reading_stream_unread_counts", {"p_user_id": user.id}).execute()
    feeds = [FeedUnreadCount(**row) for row in result.data]
    return UnreadSummary(total_unread=sum(f.unread_count for f in feeds), feeds=feeds)


# --- Bookmarks ---------------------------------------------------------------

_BOOKMARK_ARTICLE_FIELDS = "id,feed_id,title,url,summary,author,published_at"


@router.get("/bookmarks", response_model=list[ArticleSummary])
async def list_bookmarks(
    bookmark_type: str = Query("favorite"),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> list[ArticleSummary]:
    if bookmark_type not in ("favorite", "read_later"):
        raise HTTPException(status_code=400, detail="Invalid bookmark_type")
    rows = (
        db.table("user_bookmarks")
        .select(f"articles({_BOOKMARK_ARTICLE_FIELDS})")
        .eq("user_id", user.id)
        .eq("bookmark_type", bookmark_type)
        .order("created_at", desc=True)
        .execute()
    )
    return [
        ArticleSummary(**row["articles"]) for row in rows.data if row.get("articles")
    ]


@router.post("/bookmarks", status_code=204)
async def add_bookmark(
    body: BookmarkCreate,
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> None:
    if body.bookmark_type not in ("favorite", "read_later"):
        raise HTTPException(status_code=400, detail="Invalid bookmark_type")
    db.table("user_bookmarks").upsert(
        {
            "user_id": user.id,
            "article_id": str(body.article_id),
            "bookmark_type": body.bookmark_type,
        },
        on_conflict="user_id,article_id,bookmark_type",
    ).execute()


@router.delete("/bookmarks/{article_id}", status_code=204)
async def remove_bookmark(
    article_id: UUID,
    bookmark_type: str = Query("favorite"),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> None:
    db.table("user_bookmarks").delete().eq("user_id", user.id).eq(
        "article_id", str(article_id)
    ).eq("bookmark_type", bookmark_type).execute()


# --- Preferences -------------------------------------------------------------

@router.get("/preferences", response_model=UserPreferences)
async def get_preferences(
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> UserPreferences:
    rows = (
        db.table("user_preferences").select("*").eq("user_id", user.id).execute()
    )
    if not rows.data:
        return UserPreferences()
    row = rows.data[0]
    return UserPreferences(
        preferred_categories=row.get("preferred_categories") or [],
        preferred_languages=row.get("preferred_languages") or [],
    )


@router.put("/preferences", response_model=UserPreferences)
async def update_preferences(
    prefs: UserPreferencesUpdate,
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> UserPreferences:
    db.table("user_preferences").upsert(
        {
            "user_id": user.id,
            "preferred_categories": prefs.preferred_categories,
            "preferred_languages": prefs.preferred_languages,
        },
        on_conflict="user_id",
    ).execute()
    return UserPreferences(**prefs.model_dump())
