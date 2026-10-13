"""Daily aggregate telemetry; historical reads never scan article bodies."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel

from services.retention import ArticleStorageStats

HISTORY_DAYS = 90
SAMPLE_CHECK_SECONDS = 3600


class StoragePoint(ArticleStorageStats):
    snapshot_date: date
    observed_at: datetime


class StorageGrowth(BaseModel):
    start_at: datetime
    end_at: datetime
    elapsed_days: float
    article_count_per_day: float
    table_bytes_per_day: float
    index_bytes_per_day: float
    total_bytes_per_day: float
    database_bytes_per_day: float


class StorageHistory(BaseModel):
    observed_at: datetime
    retention_days: int = HISTORY_DAYS
    points: list[StoragePoint]
    growth: StorageGrowth | None


async def record_storage_snapshot(db) -> dict:
    result = await asyncio.to_thread(
        lambda: db.rpc("record_article_storage_snapshot", {}).execute()
    )
    # The database serializes concurrent samplers and skips a day already saved.
    return {"recorded": result.data is True}


async def storage_history(db, days: int = 30) -> StorageHistory:
    if type(days) is not int or not 1 <= days <= HISTORY_DAYS:
        raise ValueError("History days must be between 1 and 90")
    now = datetime.now(timezone.utc)
    cutoff = (now.date() - timedelta(days=days - 1)).isoformat()
    result = await asyncio.to_thread(lambda: db.table("article_storage_snapshots").select(
        "snapshot_date,observed_at,stats"
    ).gte("snapshot_date", cutoff).order("snapshot_date").limit(days).execute())
    points = [StoragePoint.model_validate({**row["stats"],
        "snapshot_date": row["snapshot_date"], "observed_at": row["observed_at"]})
        for row in result.data or []]
    growth = None
    if len(points) >= 2:
        first, last = points[0], points[-1]
        elapsed = (last.observed_at - first.observed_at).total_seconds() / 86400
        if elapsed >= 1:
            growth = StorageGrowth(start_at=first.observed_at, end_at=last.observed_at,
                elapsed_days=elapsed, **{f"{field}_per_day":
                    (getattr(last, field) - getattr(first, field)) / elapsed
                    for field in ("article_count", "table_bytes", "index_bytes",
                                  "total_bytes", "database_bytes")})
    return StorageHistory(observed_at=now, points=points, growth=growth)
