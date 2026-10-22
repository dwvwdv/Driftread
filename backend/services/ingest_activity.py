"""Recent per-source ingest volume and revision rate (#114 D, B).

Display/alert only: nothing here throttles, archives or caps a source. The
thresholds below only decide which rows the admin highlights. See
docs/CRAWLER_OPERATIONS.md「來源入庫量與版本異常」.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

WINDOW_HOURS = 24
# 24h and 7d (#117); source_ingest_activity accepts at most 168 hours.
WINDOW_CHOICES = (24, 168)
MAX_LIMIT = 50
# Highlight only; a busy news source is normal. Issue #114 saw ~650/day at most.
# Only the new-article count scales with the window. Revision counts are capped
# by the 10-version history, so a scaled update threshold would hide sources
# the 24h view flags; revision thresholds therefore stay fixed and the 7d view
# also flags slower churn (e.g. one revision per article per day).
INGEST_ALERT_NEW_ARTICLES = 500
# Updates are a lower bound (each article keeps its latest 10 revisions).
REVISION_ALERT_UPDATES = 200
REVISION_ALERT_PER_ARTICLE = 3.0
# Below this many updates an average is too noisy to flag on its own.
REVISION_ALERT_PER_ARTICLE_MIN_UPDATES = 30


class SourceActivity(BaseModel):
    feed_id: str
    title: str | None = None
    url: str
    archived: bool
    new_articles: int = Field(ge=0)
    backfill_articles: int = Field(ge=0)
    revision_updates: int = Field(ge=0)
    revised_articles: int = Field(ge=0)
    content_changes: int = Field(ge=0)
    metadata_only_changes: int = Field(ge=0)
    revisions_per_article: float | None = None
    # Decided in SQL (source_ingest_activity), the only copy of the predicate.
    alerts: list[Literal["new_articles", "revision_updates", "revisions_per_article"]] = []


class ActivityThresholds(BaseModel):
    new_articles: int = INGEST_ALERT_NEW_ARTICLES
    revision_updates: int = REVISION_ALERT_UPDATES
    revisions_per_article: float = REVISION_ALERT_PER_ARTICLE
    revisions_per_article_min_updates: int = REVISION_ALERT_PER_ARTICLE_MIN_UPDATES


class IngestActivity(BaseModel):
    observed_at: datetime
    since: datetime
    window_hours: int
    thresholds: ActivityThresholds
    top_ingest: list[SourceActivity]
    top_revisions: list[SourceActivity]
    # Every source over a threshold in the window (SQL applies the thresholds
    # before truncating), not only those that made the top lists.
    flagged: list[SourceActivity]
    flagged_sources: int


def classify(item: dict) -> SourceActivity:
    """Validate one RPC row and add the display-only average. The alert
    reasons come from SQL; thresholds are passed in by ingest_activity()."""
    source = SourceActivity.model_validate(item)
    if source.revised_articles:
        source.revisions_per_article = source.revision_updates / source.revised_articles
    return source


def window_error(hours) -> str | None:
    """The only copy of the allowed-window rule; the router reuses it for 422."""
    if type(hours) is not int or hours not in WINDOW_CHOICES:
        return f"hours must be one of {', '.join(map(str, WINDOW_CHOICES))}"
    return None


def thresholds_for(hours: int) -> ActivityThresholds:
    return ActivityThresholds(new_articles=INGEST_ALERT_NEW_ARTICLES * (hours // 24))


async def ingest_activity(db, limit: int = 10, hours: int = WINDOW_HOURS) -> IngestActivity:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 50")
    if error := window_error(hours):
        raise ValueError(error)
    thresholds = thresholds_for(hours)
    data = await asyncio.to_thread(lambda: db.rpc("source_ingest_activity", {
        "p_hours": hours, "p_limit": limit,
        "p_alert_new_articles": thresholds.new_articles,
        "p_alert_revision_updates": thresholds.revision_updates,
        "p_alert_per_article": thresholds.revisions_per_article,
        "p_alert_min_updates": thresholds.revisions_per_article_min_updates,
    }).execute().data)
    return IngestActivity(
        observed_at=data["observed_at"], since=data["since"],
        window_hours=hours, thresholds=thresholds,
        top_ingest=[classify(item) for item in data.get("top_ingest") or []],
        top_revisions=[classify(item) for item in data.get("top_revisions") or []],
        flagged=[classify(item) for item in data.get("flagged") or []],
        flagged_sources=data.get("flagged_total") or 0)
