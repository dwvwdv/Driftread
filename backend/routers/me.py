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
    UserPreferences,
    UserPreferencesUpdate,
)

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


@router.get("/reads", response_model=list[str])
async def list_reads(
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> list[str]:
    rows = (
        db.table("user_article_reads")
        .select("article_id")
        .eq("user_id", user.id)
        .execute()
    )
    return [row["article_id"] for row in rows.data]


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
