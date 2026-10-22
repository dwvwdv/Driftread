"""Per-table heap/TOAST/index sizes and MVCC counters (#114 E).

Separates real data growth from reusable space: cumulative pg_stat counters
become per-interval INSERT/UPDATE/DELETE deltas between adjacent daily samples,
and dead tuples show what VACUUM can reuse without shrinking the file. Reads
catalog statistics only; see docs/CRAWLER_OPERATIONS.md「每表容量與 MVCC 觀測」.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, computed_field

from services.storage_history import HISTORY_DAYS

COUNTERS = ("n_tup_ins", "n_tup_upd", "n_tup_del", "n_tup_hot_upd")


class TableStat(BaseModel):
    name: str
    heap_bytes: int
    toast_bytes: int
    index_bytes: int
    total_bytes: int
    n_live_tup: int | None = None
    n_dead_tup: int | None = None
    n_tup_ins: int | None = None
    n_tup_upd: int | None = None
    n_tup_del: int | None = None
    n_tup_hot_upd: int | None = None
    last_vacuum: datetime | None = None
    last_autovacuum: datetime | None = None
    vacuum_count: int | None = None
    autovacuum_count: int | None = None

    @computed_field
    @property
    def dead_ratio(self) -> float | None:
        if self.n_live_tup is None or self.n_dead_tup is None:
            return None
        total = self.n_live_tup + self.n_dead_tup
        return self.n_dead_tup / total if total else None


class TableSnapshot(BaseModel):
    observed_at: datetime
    stats_reset: datetime | None = None
    tables: list[TableStat]


class TableDelta(BaseModel):
    name: str
    # False when statistics were reset (or the table is new) inside the
    # interval: cumulative counters cannot be differenced, so they stay null.
    comparable: bool
    inserted: int | None = None
    updated: int | None = None
    deleted: int | None = None
    hot_updated: int | None = None
    total_bytes_delta: int | None = None
    n_dead_tup: int | None = None


class TableInterval(BaseModel):
    start_at: datetime
    end_at: datetime
    elapsed_hours: float
    stats_reset_changed: bool
    tables: list[TableDelta]


class TableStorageHistory(BaseModel):
    observed_at: datetime
    retention_days: int = HISTORY_DAYS
    current: TableSnapshot
    # Newest first; adjacent saved samples only, gaps keep their real length.
    intervals: list[TableInterval]


def table_delta(previous: TableStat | None, current: TableStat, reset_changed: bool) -> TableDelta:
    size = None if previous is None else current.total_bytes - previous.total_bytes
    base = {"name": current.name, "total_bytes_delta": size, "n_dead_tup": current.n_dead_tup}
    if previous is None or reset_changed:
        return TableDelta(comparable=False, **base)
    before = [getattr(previous, field) for field in COUNTERS]
    after = [getattr(current, field) for field in COUNTERS]
    # A per-table reset or crash recovery shrinks a counter: unknown, not negative.
    if None in before or None in after or any(b > a for b, a in zip(before, after)):
        return TableDelta(comparable=False, **base)
    inserted, updated, deleted, hot = (a - b for b, a in zip(before, after))
    return TableDelta(comparable=True, inserted=inserted, updated=updated,
                      deleted=deleted, hot_updated=hot, **base)


def interval(previous: TableSnapshot, current: TableSnapshot) -> TableInterval:
    reset_changed = previous.stats_reset != current.stats_reset
    earlier = {table.name: table for table in previous.tables}
    return TableInterval(
        start_at=previous.observed_at, end_at=current.observed_at,
        elapsed_hours=(current.observed_at - previous.observed_at).total_seconds() / 3600,
        stats_reset_changed=reset_changed,
        tables=[table_delta(earlier.get(table.name), table, reset_changed)
                for table in current.tables],
    )


async def table_storage_history(db, days: int = 7) -> TableStorageHistory:
    if type(days) is not int or not 1 <= days <= HISTORY_DAYS:
        raise ValueError("History days must be between 1 and 90")
    now = datetime.now(timezone.utc)
    # One extra day so the oldest interval in the window has its left edge.
    cutoff: date = now.date() - timedelta(days=days)
    rows = await asyncio.to_thread(lambda: db.table("article_storage_snapshots").select(
        "snapshot_date,tables"
    ).gte("snapshot_date", cutoff.isoformat()).not_.is_("tables", "null").order(
        "snapshot_date"
    ).limit(days + 1).execute().data or [])
    live = await asyncio.to_thread(lambda: db.rpc("table_storage_stats", {}).execute().data)
    samples = [TableSnapshot.model_validate(row["tables"]) for row in rows]
    intervals = [interval(prev, cur) for prev, cur in zip(samples, samples[1:])]
    intervals.reverse()
    return TableStorageHistory(observed_at=now, current=TableSnapshot.model_validate(live),
                               intervals=intervals)
