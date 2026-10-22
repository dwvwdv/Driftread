"""Recent per-source ingest volume and revision rate (#114 D, B).

Display/alert only: nothing here throttles, archives or caps a source. The
thresholds below only decide which rows the admin highlights. See
docs/CRAWLER_OPERATIONS.md「來源入庫量與版本異常」.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from pydantic import BaseModel, Field

WINDOW_HOURS = 24
MAX_LIMIT = 50
# Highlight only; a busy news source is normal. Issue #114 saw ~650/day at most.
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
    alerts: list[str] = []


class ActivityThresholds(BaseModel):
    new_articles: int = INGEST_ALERT_NEW_ARTICLES
    revision_updates: int = REVISION_ALERT_UPDATES
    revisions_per_article: float = REVISION_ALERT_PER_ARTICLE
    revisions_per_article_min_updates: int = REVISION_ALERT_PER_ARTICLE_MIN_UPDATES


class IngestActivity(BaseModel):
    observed_at: datetime
    since: datetime
    window_hours: int = WINDOW_HOURS
    thresholds: ActivityThresholds = ActivityThresholds()
    top_ingest: list[SourceActivity]
    top_revisions: list[SourceActivity]
    flagged_sources: int


def classify(item: dict) -> SourceActivity:
    source = SourceActivity.model_validate(item)
    if source.revised_articles:
        source.revisions_per_article = source.revision_updates / source.revised_articles
    alerts = []
    if source.new_articles >= INGEST_ALERT_NEW_ARTICLES:
        alerts.append("new_articles")
    if source.revision_updates >= REVISION_ALERT_UPDATES:
        alerts.append("revision_updates")
    if (source.revision_updates >= REVISION_ALERT_PER_ARTICLE_MIN_UPDATES
            and (source.revisions_per_article or 0) >= REVISION_ALERT_PER_ARTICLE):
        alerts.append("revisions_per_article")
    source.alerts = alerts
    return source


async def ingest_activity(db, limit: int = 10) -> IngestActivity:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 50")
    data = await asyncio.to_thread(lambda: db.rpc("source_ingest_activity", {
        "p_hours": WINDOW_HOURS, "p_limit": limit,
    }).execute().data)
    top_ingest = [classify(item) for item in data.get("top_ingest") or []]
    top_revisions = [classify(item) for item in data.get("top_revisions") or []]
    flagged = {s.feed_id for s in (*top_ingest, *top_revisions) if s.alerts}
    return IngestActivity(observed_at=data["observed_at"], since=data["since"],
                          top_ingest=top_ingest, top_revisions=top_revisions,
                          flagged_sources=len(flagged))
