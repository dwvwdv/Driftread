import random

from fastapi import APIRouter, Depends, Query
from supabase import Client

from auth import AuthUser, get_optional_user
from database import get_client
from models import Feed

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

# Share of the candidate pool reserved for feeds outside the caller's known
# categories, when there is at least one category signal. Pinned candidate
# pool review (PR #26): querying the whole pool with no category predicate
# means that on a catalog bigger than the fetch limit, the (unordered)
# first batch returned may contain zero matches for a caller's known
# categories, so personalization silently disappears even though matching
# feeds exist further down the table. Splitting the fetch into a
# category-matching slice and a category-excluded slice keeps both
# guarantees: known preferences still reliably reach the scorer, and some
# slots are always reserved for categories the caller doesn't know yet.
_EXPLORATION_POOL_SHARE = 0.3


def _score_candidates(
    candidates: list[dict],
    categories: set[str],
    tags: set[str],
    languages: set[str],
) -> list[dict]:
    scored: list[tuple[int, dict]] = []
    for row in candidates:
        score = 0
        if row.get("category") and row["category"] in categories:
            score += 3
        for t in row.get("tags") or []:
            if t in tags:
                score += 2
        if row.get("language") and row["language"] in languages:
            score += 1
        scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored]


def _signals_from_subscriptions(
    db: Client, user_id: str
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return (subscribed_feed_ids, categories, tags, languages) for a user."""
    sub_rows = (
        db.table("user_feeds")
        .select("feed_id, feeds(category, tags, language)")
        .eq("user_id", user_id)
        .execute()
    )
    subscribed_ids: set[str] = set()
    categories: set[str] = set()
    tags: set[str] = set()
    languages: set[str] = set()
    for row in sub_rows.data:
        subscribed_ids.add(row["feed_id"])
        feed = row.get("feeds") or {}
        if feed.get("category"):
            categories.add(feed["category"])
        for t in feed.get("tags") or []:
            tags.add(t)
        if feed.get("language"):
            languages.add(feed["language"])

    prefs = (
        db.table("user_preferences").select("*").eq("user_id", user_id).execute()
    )
    if prefs.data:
        for c in prefs.data[0].get("preferred_categories") or []:
            categories.add(c)
        for lang in prefs.data[0].get("preferred_languages") or []:
            languages.add(lang)

    return subscribed_ids, categories, tags, languages


def _base_feed_query(db: Client, excluded: set[str]):
    query = db.table("feeds").select("*").is_("archived_at", "null")
    if excluded:
        query = query.not_.in_("id", list(excluded))
    return query


def _fetch_candidate_pool(
    db: Client, excluded: set[str], categories: set[str], pool_size: int
) -> list[dict]:
    if not categories:
        return _base_feed_query(db, excluded).limit(pool_size).execute().data

    exploration_n = max(1, round(pool_size * _EXPLORATION_POOL_SHARE))
    preferred_n = pool_size - exploration_n

    preferred = (
        _base_feed_query(db, excluded)
        .in_("category", list(categories))
        .limit(preferred_n)
        .execute()
        .data
    )
    exploratory = (
        _base_feed_query(db, excluded)
        .not_.in_("category", list(categories))
        .limit(exploration_n)
        .execute()
        .data
    )
    return preferred + exploratory


@router.get("", response_model=list[Feed])
async def get_recommendations(
    liked: list[str] = Query(default=[], max_length=50),
    disliked: list[str] = Query(default=[], max_length=50),
    limit: int = Query(10, ge=1, le=50),
    user: AuthUser | None = Depends(get_optional_user),
    db: Client = Depends(get_client),
) -> list[Feed]:
    excluded: set[str] = set(liked) | set(disliked)
    categories: set[str] = set()
    tags: set[str] = set()
    languages: set[str] = set()

    if user:
        subscribed, sub_cats, sub_tags, sub_langs = _signals_from_subscriptions(
            db, user.id
        )
        excluded |= subscribed
        categories |= sub_cats
        tags |= sub_tags
        languages |= sub_langs

    if liked:
        liked_rows = (
            db.table("feeds")
            .select("category, tags, language")
            .in_("id", liked)
            .execute()
        )
        for row in liked_rows.data:
            if row.get("category"):
                categories.add(row["category"])
            for t in row.get("tags") or []:
                tags.add(t)
            if row.get("language"):
                languages.add(row["language"])

    candidates = _fetch_candidate_pool(db, excluded, categories, limit * 5)

    if candidates and (categories or tags or languages):
        candidates = _score_candidates(candidates, categories, tags, languages)
        top = candidates[:limit]
    else:
        random.shuffle(candidates)
        top = candidates[:limit]

    return [Feed(**row) for row in top]
