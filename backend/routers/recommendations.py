import random

from fastapi import APIRouter, Depends, Query
from supabase import Client

from auth import AuthUser, get_optional_user
from database import get_client
from models import Feed

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

# Share reserved for feeds outside the caller's known categories, when
# there is at least one category signal — applied twice (PR #26 review):
#
# 1. To the candidate *pool* fetch. Querying the whole pool with no
#    category predicate means that on a catalog bigger than the fetch
#    limit, the (unordered) first batch returned may contain zero matches
#    for a caller's known categories, so personalization silently
#    disappears even though matching feeds exist further down the table.
#    Splitting the fetch into a category-matching slice and a
#    category-excluded slice guarantees known preferences still reliably
#    reach the scorer.
# 2. To the final scored *output*. A preferred-slice row always outscores
#    an exploratory one with no other matching signal (+3 vs 0), so once
#    the pool is merged and re-sorted, `top[:limit]` would be 100%
#    preferred rows on any catalog with at least `limit` matches — the
#    pool-level split alone never actually surfaces to the caller. Slots
#    have to be reserved after scoring too, not just in the fetch.
_EXPLORATION_SHARE = 0.3


def _score(row: dict, categories: set[str], tags: set[str], languages: set[str]) -> int:
    score = 0
    if row.get("category") and row["category"] in categories:
        score += 3
    for t in row.get("tags") or []:
        if t in tags:
            score += 2
    if row.get("language") and row["language"] in languages:
        score += 1
    return score


def _score_candidates(
    candidates: list[dict],
    categories: set[str],
    tags: set[str],
    languages: set[str],
) -> list[dict]:
    scored = [(_score(row, categories, tags, languages), row) for row in candidates]
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
) -> tuple[list[dict], list[dict]]:
    """Return (preferred, exploratory) candidate rows as separate lists —
    kept apart (rather than merged here) so the caller can enforce the
    exploration share on the scored output too, not just on this fetch."""
    if not categories:
        pool = _base_feed_query(db, excluded).limit(pool_size).execute().data
        return pool, []

    exploration_n = max(1, round(pool_size * _EXPLORATION_SHARE))
    preferred_n = pool_size - exploration_n

    preferred = (
        _base_feed_query(db, excluded)
        .in_("category", list(categories))
        .limit(preferred_n)
        .execute()
        .data
    )
    # Exploratory candidates come from two sources: a known-but-different
    # category, and no category at all. `not_.in_("category", ...)`
    # compiles to SQL's NOT IN, which never matches a NULL column, so a
    # feed with no category (a normal catalog state — nullable per
    # migration 001, and discovery promotion writes it when no category
    # was approved) would otherwise be invisible to every personalized
    # caller — hence the separate .is_("category", "null") query rather
    # than relying on the first to cover it. Folding both into one query
    # via .or_() instead would mean concatenating `categories` — which can
    # include a caller's own free-text `preferred_categories` — into a
    # single filter string, reopening the PostgREST filter-injection class
    # SECURITY.md #14 fixed.
    #
    # Each is capped at the *full* exploration_n rather than a pre-split
    # half each: if one source is empty (e.g. a catalog with no
    # uncategorized feeds at all), the other must still be able to fill
    # the whole budget on its own, or `limit` results go undelivered even
    # though enough eligible feeds exist.
    other_category = (
        _base_feed_query(db, excluded)
        .not_.in_("category", list(categories))
        .limit(exploration_n)
        .execute()
        .data
    )
    uncategorized = (
        _base_feed_query(db, excluded)
        .is_("category", "null")
        .limit(exploration_n)
        .execute()
        .data
    )
    # `other_category` is listed first, so a plain concatenate-then-cap
    # — (other_category + uncategorized)[:exploration_n] — would
    # deterministically drop every uncategorized row whenever
    # other_category alone already fills exploration_n (the common case
    # in a populated catalog): the exact "personalized users never see
    # uncategorized feeds" bug the .is_() query above exists to fix, just
    # reappearing one step later. Shuffling *before* capping (not after —
    # shuffling post-cap can't recover rows the cap already dropped) gives
    # every combined row an equal chance regardless of which subtype
    # query happened to list it first.
    combined = other_category + uncategorized
    random.shuffle(combined)
    return preferred, combined[:exploration_n]


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

    preferred_rows, exploratory_rows = _fetch_candidate_pool(
        db, excluded, categories, limit * 5
    )
    candidates = preferred_rows + exploratory_rows

    if candidates and (categories or tags or languages):
        scored_preferred = _score_candidates(preferred_rows, categories, tags, languages)
        scored_exploratory = _score_candidates(
            exploratory_rows, categories, tags, languages
        )

        exploration_slots = (
            min(len(scored_exploratory), max(1, round(limit * _EXPLORATION_SHARE)))
            if scored_exploratory
            else 0
        )
        preferred_slots = limit - exploration_slots

        top = scored_preferred[:preferred_slots] + scored_exploratory[:exploration_slots]
        if len(top) < limit:
            # one side came up short of its reserved slots (small catalog,
            # or few matches) — backfill from whatever the other side has
            # left over rather than returning fewer than `limit` results.
            leftover = scored_preferred[preferred_slots:] + scored_exploratory[exploration_slots:]
            top += leftover[: limit - len(top)]
        # The quota above picks *which* rows make the page — every
        # preferred row before every exploratory one — but that's not a
        # score ordering: an exploratory row matching several tags can
        # outscore a preferred row that only matches category. Re-sort
        # the selected rows by their real score so the response order
        # actually reflects it, without disturbing which rows were picked.
        top.sort(key=lambda row: _score(row, categories, tags, languages), reverse=True)
    else:
        random.shuffle(candidates)
        top = candidates[:limit]

    return [Feed(**row) for row in top]
