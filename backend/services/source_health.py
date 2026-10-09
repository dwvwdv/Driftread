"""Deterministic source freshness and independent-participant identity.

Completeness concerns the selected source cohort, never article volume. Callers
must pass all eligible sources, including sources with no recent articles.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Any, Iterable


def participant_key(source: Mapping[str, Any]) -> str:
    group = (source.get("signal_group") or "").strip().casefold()
    return f"group:{group}" if group else f"feed:{source['id']}"


def _timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def source_health(source: Mapping[str, Any], now: datetime) -> dict:
    last_ok = _timestamp(source.get("last_ok_at"))
    now = _timestamp(now)
    interval = max(1, source.get("fetch_interval_minutes") or 60) * 60
    age = max(0.0, (now - last_ok).total_seconds()) if last_ok else None
    lag = max(0.0, age - interval) if age is not None else None
    return {
        "feed_id": str(source["id"]),
        "participant_key": participant_key(source),
        "last_fetch_at": source.get("last_fetch_at"),
        "last_ok_at": source.get("last_ok_at"),
        "age_seconds": age,
        "lag_seconds": lag,
        "behind": last_ok is None or lag > 0,
    }


def summarize_source_health(sources: Iterable[Mapping[str, Any]], now: datetime) -> dict:
    eligible = [source for source in sources if not source.get("archived_at")
                and source.get("participation_mode", "normal") != "private"]
    rows = [source_health(source, now) for source in eligible]
    behind = [row for row in rows if row["behind"]]
    return {
        # An empty cohort supplies no evidence of completeness.
        "complete": bool(rows) and not behind,
        "source_count": len(rows),
        "behind_source_count": len(behind),
        "participant_count": len({row["participant_key"] for row in rows}),
        "behind_participant_count": len({row["participant_key"] for row in behind}),
        "sources": rows,
    }
