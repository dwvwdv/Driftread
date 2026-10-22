"""Per-source article cap: bounded deletion of the oldest unprotected rows.

Unlike content compaction this removes article identities. Ranking, the
protected set (bookmarks, manual event links) and the re-ingestion watermark
belong in one database RPC so they cannot race with a feed refresh. Preview is
the default even for callers outside the API. See docs/CRAWLER_OPERATIONS.md
「每來源文章上限」.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field
from supabase import Client

from services.discovery_config import discovery_enabled
from services.settings import load_article_cap_settings

MIN_CAP = 100
MAX_CAP = 100000
MAX_BATCH_SIZE = 1000
BATCH_SIZE = 500
TICK_SECONDS = 3600


class ArticleCapSummary(BaseModel):
    dry_run: bool
    feeds_over_cap: int = Field(ge=0)
    # Cached article_count minus cap, protected rows included: an upper bound.
    excess: int = Field(ge=0)
    eligible: int = Field(ge=0)
    deleted: int = Field(ge=0)
    skipped_feeds: int = Field(ge=0)


async def enforce_article_cap(
    db: Client,
    *,
    cap: int | None = None,
    limit: int | None = None,
    dry_run: bool = True,
) -> ArticleCapSummary:
    """Preview/apply one batch using the same eligibility rules in both modes.

    cap is the default for sources without feeds.article_cap. Rows whose
    current version has not been mined for links are kept up to 1.5x the cap
    only while this deployment runs discovery; otherwise nothing would ever
    mark them and the cap would silently become 1.5x.
    """
    if cap is None:
        cap = (await asyncio.to_thread(load_article_cap_settings, db)).max_per_feed
    limit = BATCH_SIZE if limit is None else limit
    if type(cap) is not int or not MIN_CAP <= cap <= MAX_CAP:
        raise ValueError("Article cap must be between 100 and 100000")
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError("Article cap batch size must be between 1 and 1000")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")

    params = {"p_default_cap": cap, "p_limit": limit, "p_dry_run": dry_run,
              "p_keep_unextracted": discovery_enabled()}
    result = await asyncio.to_thread(
        lambda: db.rpc("enforce_article_cap", params).execute()
    )
    summary = ArticleCapSummary.model_validate(result.data)
    if summary.dry_run != dry_run or summary.deleted > summary.eligible:
        raise RuntimeError("Invalid article cap RPC result")
    if summary.eligible > limit or (dry_run and summary.deleted):
        raise RuntimeError("Article cap RPC violated its batch/preview contract")
    return summary


async def article_cap_enabled(db: Client) -> bool:
    settings = await asyncio.to_thread(load_article_cap_settings, db)
    return settings.enabled


async def scheduled_enforcement(db: Client) -> ArticleCapSummary | None:
    """At most one bounded delete; the setting is re-read on every run."""
    settings = await asyncio.to_thread(load_article_cap_settings, db)
    if not settings.enabled:
        return None
    return await enforce_article_cap(db, cap=settings.max_per_feed, dry_run=False)
