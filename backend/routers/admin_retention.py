from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from supabase import Client

from database import get_client
from routers.admin import require_api_key
from services.storage_history import StorageHistory, storage_history
from services.table_storage import TableStorageHistory, table_storage_history
from services.article_cap import ArticleCapSummary, enforce_article_cap
from services.retention import (
    ArticleStorageStats,
    RetentionSummary,
    compact_content,
    storage_stats,
)

router = APIRouter(prefix="/admin/retention", tags=["admin", "retention"])


@router.get("/history", response_model=StorageHistory, dependencies=[Depends(require_api_key)])
async def retention_history(
    days: int = Query(30, ge=1, le=90),
    db: Client = Depends(get_client),
) -> StorageHistory:
    """Read saved daily aggregates and elapsed-time growth; never sample on GET."""
    return await storage_history(db, days)


@router.get("/tables", response_model=TableStorageHistory, dependencies=[Depends(require_api_key)])
async def retention_tables(
    days: int = Query(7, ge=1, le=90),
    db: Client = Depends(get_client),
) -> TableStorageHistory:
    """Live per-table sizes/MVCC counters plus deltas between saved daily samples.

    Catalog statistics only; never scans table contents or samples on GET.
    """
    return await table_storage_history(db, days)


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


@router.post(
    "/article-cap/run",
    response_model=ArticleCapSummary,
    dependencies=[Depends(require_api_key)],
)
async def run_article_cap(
    max_per_feed: int | None = Query(None, ge=100, le=100000),
    limit: int | None = Query(None, ge=1, le=1000),
    dry_run: bool = Query(True),
    db: Client = Depends(get_client),
) -> ArticleCapSummary:
    """Preview by default; dry_run=false explicitly deletes a bounded batch.

    max_per_feed defaults to the article.cap setting and applies only to
    sources without their own override. The setting's enabled flag gates the
    scheduler only, like retention: an operator may still preview or apply.
    Deleted rows cannot be restored.
    """
    return await enforce_article_cap(db, cap=max_per_feed, limit=limit, dry_run=dry_run)
