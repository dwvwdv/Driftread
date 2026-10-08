"""Bounded body compaction; article rows and user references are never deleted.

Eligibility and the update belong in one database RPC, so extraction/version
checks cannot race with a feed refresh. Preview is the default even for callers
outside the API. Byte counts describe uncompressed content, not disk reclaimed.
"""
from __future__ import annotations

import asyncio
import os

from pydantic import BaseModel, Field
from supabase import Client

from env_utils import env_int

MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650
MAX_BATCH_SIZE = 1000


class RetentionSummary(BaseModel):
    dry_run: bool
    eligible: int = Field(ge=0)
    compacted: int = Field(ge=0)
    content_bytes: int = Field(ge=0)


class ArticleStorageStats(BaseModel):
    """A current snapshot; growth rates require comparing separate snapshots."""

    article_count: int = Field(ge=0)
    full_content_count: int = Field(ge=0)
    summary_only_count: int = Field(ge=0)
    pending_discovery_count: int = Field(ge=0)
    compacted_count: int = Field(ge=0)
    table_bytes: int = Field(ge=0)
    index_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    database_bytes: int = Field(ge=0)


async def storage_stats(db: Client) -> ArticleStorageStats:
    """Return aggregate counters and actual relation sizes, never body text."""
    result = await asyncio.to_thread(lambda: db.rpc("article_storage_stats").execute())
    return ArticleStorageStats.model_validate(result.data)


def retention_enabled() -> bool:
    # This operation removes body text. Only explicit opt-in enables scheduled
    # writes; a misspelled boolean must not silently turn the scheduler on.
    return os.getenv("ARTICLE_RETENTION_ENABLED", "").strip().lower() in {
        "true", "1", "yes", "on",
    }


def retention_days() -> int:
    return min(
        env_int("ARTICLE_RETENTION_DAYS", 30, minimum=MIN_RETENTION_DAYS),
        MAX_RETENTION_DAYS,
    )


def batch_size() -> int:
    return min(env_int("ARTICLE_RETENTION_BATCH_SIZE", 200), MAX_BATCH_SIZE)


def tick_seconds() -> int:
    return min(env_int("ARTICLE_RETENTION_INTERVAL_MINUTES", 1440), 10080) * 60


async def compact_content(
    db: Client,
    *,
    days: int | None = None,
    limit: int | None = None,
    dry_run: bool = True,
) -> RetentionSummary:
    """Preview/apply one batch using the same eligibility rules in both modes.

    The RPC protects any bookmark/read/subscription, requires a successful
    extraction of the current content hash, and preserves title/summary/URL.
    No fallback to individual UPDATEs is safe if the migration is missing.
    """
    days = retention_days() if days is None else days
    limit = batch_size() if limit is None else limit
    if type(days) is not int or not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS:
        raise ValueError("Retention days must be between 7 and 3650")
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError("Retention batch size must be between 1 and 1000")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")

    params = {"p_retention_days": days, "p_limit": limit, "p_dry_run": dry_run}
    result = await asyncio.to_thread(
        lambda: db.rpc("compact_article_content", params).execute()
    )
    summary = RetentionSummary.model_validate(result.data)
    if summary.dry_run != dry_run or summary.compacted > summary.eligible:
        raise RuntimeError("Invalid retention RPC result")
    if summary.eligible > limit or (dry_run and summary.compacted):
        raise RuntimeError("Retention RPC violated its batch/preview contract")
    return summary


async def scheduled_compaction(db: Client) -> RetentionSummary | None:
    """At most one bounded write; disabled until an operator opts in."""
    if not retention_enabled():
        return None
    return await compact_content(db, dry_run=False)
