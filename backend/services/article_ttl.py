"""Opt-in, bounded expiry of whole article rows.

Eligibility, bookmark/curation protection and deletion live in one database
transaction. Preview is the default; neither cap nor body retention controls
this schedule. See docs/CRAWLER_OPERATIONS.md「文章到期刪除（TTL）」.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field
from supabase import Client

from services.settings import load_article_ttl_settings

MIN_DAYS = 1
MAX_DAYS = 3650
MAX_BATCH_SIZE = 1000
BATCH_SIZE = 500
TICK_SECONDS = 3600


class ArticleTtlSummary(BaseModel):
    dry_run: bool
    expired: int = Field(ge=0)
    protected: int = Field(ge=0)
    eligible: int = Field(ge=0)
    deleted: int = Field(ge=0)
    reads_affected: int = Field(ge=0)
    skipped_feeds: int = Field(ge=0)
    # Total elapsed seconds beyond the TTL deadline for rows actually deleted.
    deletion_delay_seconds: float = Field(ge=0, allow_inf_nan=False)


async def enforce_article_ttl(
    db: Client,
    *,
    days: int | None = None,
    limit: int | None = None,
    dry_run: bool = True,
) -> ArticleTtlSummary:
    """Preview/apply one batch with identical eligibility and a strict cutoff.

    The enabled flag gates automatic expiry and TTL admission filtering;
    administrators can still preview or apply manually before opting in.
    Bookmarks (favorite/read_later), manual event links and
    staged articles survive; read receipts cascade with a deleted article.
    """
    if days is None:
        days = (await asyncio.to_thread(load_article_ttl_settings, db)).days
    limit = BATCH_SIZE if limit is None else limit
    if type(days) is not int or not MIN_DAYS <= days <= MAX_DAYS:
        raise ValueError("Article TTL days must be between 1 and 3650")
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError("Article TTL batch size must be between 1 and 1000")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")

    result = await asyncio.to_thread(
        lambda: db.rpc("enforce_article_ttl", {
            "p_days": days, "p_limit": limit, "p_dry_run": dry_run,
        }).execute()
    )
    summary = ArticleTtlSummary.model_validate(result.data)
    if (summary.dry_run != dry_run or summary.deleted > summary.eligible
            or summary.protected > summary.expired):
        raise RuntimeError("Invalid article TTL RPC result")
    if (summary.eligible > limit or (dry_run and (summary.deleted or summary.deletion_delay_seconds))
            or (not summary.deleted and summary.deletion_delay_seconds)):
        raise RuntimeError("Article TTL RPC violated its batch/preview contract")
    return summary


async def article_ttl_enabled(db: Client) -> bool:
    settings = await asyncio.to_thread(load_article_ttl_settings, db)
    return settings.enabled


async def scheduled_expiry(db: Client) -> ArticleTtlSummary | None:
    """Apply at most one bounded batch after re-reading the opt-in setting."""
    settings = await asyncio.to_thread(load_article_ttl_settings, db)
    if not settings.enabled:
        return None
    return await enforce_article_ttl(db, days=settings.days, dry_run=False)
