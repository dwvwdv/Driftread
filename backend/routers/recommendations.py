import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from supabase import Client

from auth import AuthUser, get_optional_user
from database import get_client
from models import Feed, RecommendedFeed
from rate_limit import rate_limit

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

# Score weights per signal source, tiered per TODO.md 推薦回饋持久化's own
# ordering: "訂閱為強正向訊號；喜歡為正向；收藏／稍後讀文章所屬來源為中度正向"
# (subscribe = strong positive; like = positive; bookmark-source = moderate
# positive), plus explicit dislikes and short-term skips as negative
# signals. `preferred_*` (UserPreferences, an explicit opt-in) is treated at
# the same tier as a subscription.
_WEIGHT_SUBSCRIBED = {"category": 3.0, "tag": 2.0, "language": 1.0}
_WEIGHT_LIKED = {"category": 2.0, "tag": 1.0, "language": 1.0}
_WEIGHT_BOOKMARKED = {"category": 1.0, "tag": 0.5, "language": 0.0}
_WEIGHT_DISLIKED = {"category": -3.0, "tag": -2.0}
_WEIGHT_SKIPPED = {"category": -1.0, "tag": -1.0}

# A skip is a light "not now", not a verdict — TODO.md explicitly
# distinguishes it from a dislike ("只做短期降權，不等同明確不喜歡"). Past this
# window a skipped feed stops being excluded or downweighted at all, so it
# can resurface in a later deck instead of being suppressed forever by one
# old swipe.
_SKIP_DECAY = timedelta(days=14)


@dataclass
class Signals:
    """Per-tier signal membership, kept separate (rather than merged into one
    generic weight map up front) so `_reason()` can explain *which* tier
    actually drove a match, not just that some positive weight applied.
    `category_weight`/`tag_weight`/`language_weight` are the flattened sums
    `_score()` actually reads; `positive_categories` is the narrower set
    `_fetch_candidate_pool` uses to decide which feeds are worth fetching in
    the first place (negative-only signals should never pull a category
    *into* the preferred pool)."""

    subscribed_categories: set[str] = field(default_factory=set)
    subscribed_tags: set[str] = field(default_factory=set)
    subscribed_languages: set[str] = field(default_factory=set)
    liked_categories: set[str] = field(default_factory=set)
    liked_tags: set[str] = field(default_factory=set)
    bookmarked_categories: set[str] = field(default_factory=set)
    bookmarked_tags: set[str] = field(default_factory=set)
    disliked_categories: set[str] = field(default_factory=set)
    disliked_tags: set[str] = field(default_factory=set)
    skipped_categories: set[str] = field(default_factory=set)
    skipped_tags: set[str] = field(default_factory=set)

    category_weight: dict[str, float] = field(default_factory=dict)
    tag_weight: dict[str, float] = field(default_factory=dict)
    language_weight: dict[str, float] = field(default_factory=dict)

    excluded: set[str] = field(default_factory=set)

    def _add(self, row: dict, weight: dict[str, float]) -> None:
        category = row.get("category")
        if category:
            self.category_weight[category] = (
                self.category_weight.get(category, 0.0) + weight["category"]
            )
        for t in row.get("tags") or []:
            self.tag_weight[t] = self.tag_weight.get(t, 0.0) + weight["tag"]
        language = row.get("language")
        if language and weight.get("language"):
            self.language_weight[language] = (
                self.language_weight.get(language, 0.0) + weight["language"]
            )

    def add_subscribed(self, row: dict) -> None:
        self._add(row, _WEIGHT_SUBSCRIBED)
        if row.get("category"):
            self.subscribed_categories.add(row["category"])
        self.subscribed_tags.update(row.get("tags") or [])
        if row.get("language"):
            self.subscribed_languages.add(row["language"])

    def add_liked(self, row: dict) -> None:
        self._add(row, _WEIGHT_LIKED)
        if row.get("category"):
            self.liked_categories.add(row["category"])
        self.liked_tags.update(row.get("tags") or [])

    def add_bookmarked(self, row: dict) -> None:
        self._add(row, _WEIGHT_BOOKMARKED)
        if row.get("category"):
            self.bookmarked_categories.add(row["category"])
        self.bookmarked_tags.update(row.get("tags") or [])

    def add_disliked(self, row: dict) -> None:
        self._add(row, _WEIGHT_DISLIKED)
        if row.get("category"):
            self.disliked_categories.add(row["category"])
        self.disliked_tags.update(row.get("tags") or [])

    def add_skipped(self, row: dict) -> None:
        self._add(row, _WEIGHT_SKIPPED)
        if row.get("category"):
            self.skipped_categories.add(row["category"])
        self.skipped_tags.update(row.get("tags") or [])

    @property
    def positive_categories(self) -> set[str]:
        return {c for c, w in self.category_weight.items() if w > 0}


def _score(row: dict, signals: Signals) -> float:
    score = 0.0
    category = row.get("category")
    if category:
        score += signals.category_weight.get(category, 0.0)
    for t in row.get("tags") or []:
        score += signals.tag_weight.get(t, 0.0)
    language = row.get("language")
    if language:
        score += signals.language_weight.get(language, 0.0)
    return score


def _reason(row: dict, signals: Signals) -> str | None:
    """A short, human-readable explanation for why `row` was recommended
    (TODO.md 推薦回饋持久化's "顯示推薦理由"). Checked in the same strongest-
    signal-first order the weights use, and stops at the first tier that
    actually matches — a candidate that happens to match on several tiers at
    once gets one clear reason, not a list of every contributing signal."""
    category = row.get("category")
    tags = set(row.get("tags") or [])

    if category and category in signals.subscribed_categories:
        return f"因為你訂閱了 {category} 類別的來源"
    matched = sorted(tags & signals.subscribed_tags)
    if matched:
        return f"因為你訂閱的來源也有「{ '、'.join(matched[:3]) }」標籤"

    if category and category in signals.liked_categories:
        return f"因為你喜歡過 {category} 類別的來源"
    matched = sorted(tags & signals.liked_tags)
    if matched:
        return f"因為你喜歡過「{ '、'.join(matched[:3]) }」相關的來源"

    if category and category in signals.bookmarked_categories:
        return f"因為你收藏過 {category} 類別的文章"
    matched = sorted(tags & signals.bookmarked_tags)
    if matched:
        return f"因為你收藏過「{ '、'.join(matched[:3]) }」相關的文章"

    language = row.get("language")
    if language and language in signals.subscribed_languages:
        return f"因為你常讀 {language} 的來源"

    return None


def _score_candidates(candidates: list[dict], signals: Signals) -> list[dict]:
    scored = [(_score(row, signals), row) for row in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored]


def _load_signals(db: Client, user_id: str, signals: Signals) -> None:
    """Populates every signal source TODO.md's 推薦回饋持久化 batch specifies
    onto `signals` (mutated in place, so a caller's own pre-seeded state —
    here, `excluded` from the `liked`/`disliked` query params — doesn't need
    a separate field-by-field merge afterward): subscriptions (+ their
    categories/tags/language), explicit preferences, persisted 猜你喜歡
    feedback (liked/disliked/skipped), and bookmarked articles' source
    feeds."""
    sub_rows = (
        db.table("user_feeds")
        .select("feed_id, feeds(category, tags, language)")
        .eq("user_id", user_id)
        .execute()
    )
    for row in sub_rows.data:
        signals.excluded.add(row["feed_id"])
        feed = row.get("feeds") or {}
        if feed:
            signals.add_subscribed(feed)

    prefs = db.table("user_preferences").select("*").eq("user_id", user_id).execute()
    if prefs.data:
        for c in prefs.data[0].get("preferred_categories") or []:
            signals.category_weight[c] = (
                signals.category_weight.get(c, 0.0) + _WEIGHT_SUBSCRIBED["category"]
            )
            signals.subscribed_categories.add(c)
        for lang in prefs.data[0].get("preferred_languages") or []:
            signals.language_weight[lang] = (
                signals.language_weight.get(lang, 0.0) + _WEIGHT_SUBSCRIBED["language"]
            )
            signals.subscribed_languages.add(lang)

    feedback_rows = (
        db.table("user_feed_feedback")
        .select("feed_id, feedback_type, created_at, feeds(category, tags, language)")
        .eq("user_id", user_id)
        .execute()
    )
    now = datetime.now(timezone.utc)
    for row in feedback_rows.data:
        feed = row.get("feeds") or {}
        feedback_type = row["feedback_type"]
        if feedback_type == "liked":
            signals.excluded.add(row["feed_id"])
            if feed:
                signals.add_liked(feed)
        elif feedback_type == "disliked":
            signals.excluded.add(row["feed_id"])
            if feed:
                signals.add_disliked(feed)
        elif feedback_type == "skipped":
            created_at = row.get("created_at")
            recent = bool(created_at) and (
                now - datetime.fromisoformat(created_at.replace("Z", "+00:00")) < _SKIP_DECAY
            )
            if recent:
                signals.excluded.add(row["feed_id"])
                if feed:
                    signals.add_skipped(feed)
            # An expired skip contributes nothing: no exclusion, no
            # downweight — the feed is eligible to resurface normally.

    bookmark_rows = (
        db.table("user_bookmarks")
        .select("articles(feed_id)")
        .eq("user_id", user_id)
        .execute()
    )
    # One row per bookmark, but a heavily-bookmarked source shouldn't count
    # more than once toward its own moderate-positive weight — that would
    # let bookmark volume alone out-weight an explicit like. Two queries
    # (ids here, then the feed rows below) rather than a doubly-nested
    # `articles(feed_id, feeds(...))` embed: this codebase has no existing
    # precedent for embedding two levels deep through PostgREST, and a
    # feed_id batch lookup is the same shape every other signal source here
    # already uses.
    bookmarked_feed_ids = {
        row["articles"]["feed_id"]
        for row in bookmark_rows.data
        if row.get("articles") and row["articles"].get("feed_id")
    }
    if bookmarked_feed_ids:
        bookmarked_feeds = (
            db.table("feeds")
            .select("category, tags, language")
            .in_("id", list(bookmarked_feed_ids))
            .execute()
        )
        for feed in bookmarked_feeds.data:
            signals.add_bookmarked(feed)


def _sample_feeds(
    db: Client,
    excluded: set[str],
    categories: set[str],
    mode: str,
    sample_limit: int,
) -> list[dict]:
    """Random sample of up to `sample_limit` feed rows matching one of the
    four pool shapes `_fetch_candidate_pool` needs, via the
    `sample_feed_candidates` DB function (migration 007). PostgREST's query
    builder has no `ORDER BY random()`, so a plain `.table(...).limit(n)`
    always returns the same default-ordered first N rows on a catalog
    bigger than the fetch size — the downstream `random.shuffle()` can only
    reshuffle whichever N rows that happened to be, never reach further
    into the table. `mode` is one of a fixed set of literals this function
    controls, never caller-supplied text, so this doesn't reopen the
    PostgREST filter-injection class SECURITY.md #14 fixed."""
    result = db.rpc(
        "sample_feed_candidates",
        {
            "p_excluded_ids": list(excluded),
            "p_categories": list(categories),
            "p_mode": mode,
            "p_limit": sample_limit,
        },
    ).execute()
    return result.data


def _fetch_candidate_pool(
    db: Client, excluded: set[str], categories: set[str], pool_size: int
) -> tuple[list[dict], list[dict]]:
    """Return (preferred, exploratory) candidate rows as separate lists —
    kept apart (rather than merged here) so the caller can enforce the
    exploration share on the scored output too, not just on this fetch."""
    if not categories:
        pool = _sample_feeds(db, excluded, categories, "unfiltered", pool_size)
        return pool, []

    exploration_n = max(1, round(pool_size * _EXPLORATION_SHARE))
    preferred_n = pool_size - exploration_n

    preferred = _sample_feeds(db, excluded, categories, "in_categories", preferred_n)
    # Exploratory candidates come from two sources: a known-but-different
    # category, and no category at all. The `not_in_categories` mode never
    # matches a NULL category column, so a feed with no category (a normal
    # catalog state — nullable per migration 001, and discovery promotion
    # writes it when no category was approved) would otherwise be invisible
    # to every personalized caller — hence the separate `uncategorized`
    # query rather than relying on the first to cover it.
    #
    # Each is capped at the *full* exploration_n rather than a pre-split
    # half each: if one source is empty (e.g. a catalog with no
    # uncategorized feeds at all), the other must still be able to fill
    # the whole budget on its own, or `limit` results go undelivered even
    # though enough eligible feeds exist.
    other_category = _sample_feeds(
        db, excluded, categories, "not_in_categories", exploration_n
    )
    uncategorized = _sample_feeds(
        db, excluded, categories, "uncategorized", exploration_n
    )
    # `other_category` is listed first, so a plain concatenate-then-cap
    # — (other_category + uncategorized)[:exploration_n] — would
    # deterministically drop every uncategorized row whenever
    # other_category alone already fills exploration_n (the common case
    # in a populated catalog): the exact "personalized users never see
    # uncategorized feeds" bug the separate `uncategorized` query above
    # exists to fix, just reappearing one step later. Shuffling *before*
    # capping (not after — shuffling post-cap can't recover rows the cap
    # already dropped) gives
    # every combined row an equal chance regardless of which subtype
    # query happened to list it first.
    combined = other_category + uncategorized
    random.shuffle(combined)
    return preferred, combined[:exploration_n]


# Public and unauthenticated, and each call can run up to three sampling
# queries against `feeds` via sample_feed_candidates — an indexed pivot scan
# per pool since migration 018, not the full scan-and-sort migration 007
# originally shipped, but still real per-request DB work. Restricting the
# RPC's own grants (migration 007) stops a caller bypassing this endpoint
# entirely, but does nothing about plain request-volume flooding straight at
# this route, unlike /discover and /discover/import (rate-limited since
# SECURITY.md #18) which this endpoint had been missing.
@router.get(
    "",
    response_model=list[RecommendedFeed],
    dependencies=[Depends(rate_limit("recommendations"))],
)
async def get_recommendations(
    liked: list[UUID] = Query(default=[], max_length=50),
    disliked: list[UUID] = Query(default=[], max_length=50),
    # A caller-side-only skip list (anonymous callers have no persisted
    # feedback for `_load_signals` to read, and no server-side timestamp to
    # decay by) — kept a distinct param from `disliked` rather than folded
    # into it so a light "not now" swipe never reads back as an explicit
    # dislike anywhere the client also tracks that separately (see
    # RecommendationService/feed-detail.ts on the frontend).
    skipped: list[UUID] = Query(default=[], max_length=50),
    limit: int = Query(10, ge=1, le=50),
    user: AuthUser | None = Depends(get_optional_user),
    db: Client = Depends(get_client),
) -> list[RecommendedFeed]:
    signals = Signals()
    signals.excluded = (
        {str(u) for u in liked} | {str(u) for u in disliked} | {str(u) for u in skipped}
    )

    if user:
        # Persisted, cross-device feedback (TODO.md 推薦回饋持久化) is the
        # source of truth for a signed-in caller; the query params above stay
        # supported too so an action taken this exact moment (before its own
        # POST/PUT round trip lands) still affects the very next fetch.
        # `signals.excluded` (already seeded above) is extended in place, not
        # replaced.
        _load_signals(db, user.id, signals)

    if liked:
        liked_rows = (
            db.table("feeds")
            .select("category, tags, language")
            .in_("id", [str(u) for u in liked])
            .execute()
        )
        for row in liked_rows.data:
            signals.add_liked(row)

    categories = signals.positive_categories
    preferred_rows, exploratory_rows = _fetch_candidate_pool(
        db, signals.excluded, categories, limit * 5
    )
    candidates = preferred_rows + exploratory_rows

    if candidates and (
        signals.category_weight or signals.tag_weight or signals.language_weight
    ):
        scored_preferred = _score_candidates(preferred_rows, signals)
        scored_exploratory = _score_candidates(exploratory_rows, signals)

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
        top.sort(key=lambda row: _score(row, signals), reverse=True)
    else:
        # No signal at all means `_fetch_candidate_pool` ran in "unfiltered"
        # mode, which is already randomly sampled server-side (migration 007,
        # indexed since 018) — no need to reshuffle client-side on top of that.
        top = candidates[:limit]

    return [
        RecommendedFeed(feed=Feed(**row), reason=_reason(row, signals)) for row in top
    ]
