from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from supabase import Client

from database import get_client
from routers.admin import require_api_key
from services.retention import (
    ArticleStorageStats,
    RetentionSummary,
    compact_content,
    storage_stats,
)

router = APIRouter(prefix="/admin/retention", tags=["admin", "retention"])


@router.get(
    "/stats",
    response_model=ArticleStorageStats,
    dependencies=[Depends(require_api_key)],
)
async def retention_stats(db: Client = Depends(get_client)) -> ArticleStorageStats:
    """Current article/storage snapshot, without historical growth rates."""
    return await storage_stats(db)


@router.post(
    "/run",
    response_model=RetentionSummary,
    dependencies=[Depends(require_api_key)],
)
async def run_retention(
    retention_days: int | None = Query(None, ge=7, le=3650),
    limit: int | None = Query(None, ge=1, le=1000),
    dry_run: bool = Query(True),
    db: Client = Depends(get_client),
) -> RetentionSummary:
    """Preview by default; dry_run=false explicitly compacts a bounded batch.

    The scheduling flag gates automatic work only. An authenticated operator
    may preview or apply manually while the scheduler remains disabled.
    content_bytes is raw body size, not an estimate of freed disk space.
    """
    return await compact_content(
        db, days=retention_days, limit=limit, dry_run=dry_run,
    )
