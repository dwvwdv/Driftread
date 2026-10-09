"""Deterministic, private reading surfaces; no model or delivery provider."""
from datetime import date, datetime, time, timedelta, timezone
from email.utils import format_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from xml.sax.saxutils import escape

from fastapi import HTTPException

# Imported lazily so the publication dependency remains the sole read model.
def publications(db, user_id: str, **kwargs) -> list[dict]:
    from services.publications import list_personal_publications
    return list_personal_publications(db, user_id, **kwargs)


def excerpt(row: dict) -> dict:
    # Explicit allowlist: never leak raw content, service-only state or provenance.
    return {key: row.get(key) for key in (
        "id", "feed_id", "title", "url", "summary", "author", "feed_title",
        "published_at", "timeline_at", "fulltext_allowed",
    )}


def day_bounds(day: date | None, timezone_name: str) -> tuple[date, datetime, datetime]:
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "Invalid IANA timezone") from exc
    day = day or datetime.now(zone).date()
    start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    return day, start, end


def daily_digest(db, user_id: str, day: date | None = None,
                 timezone_name: str = "UTC", limit: int = 100) -> dict:
    day, start, end = day_bounds(day, timezone_name)
    rows = publications(db, user_id, start=start, end=end,
                        exclude_backfill=True, limit=limit + 1)
    return {"date": day.isoformat(), "timezone": timezone_name,
            "start": start.isoformat(), "end": end.isoformat(),
            "summary_source": "feed", "items": [excerpt(r) for r in rows[:limit]],
            "truncated": len(rows) > limit}


def _xml(value) -> str:
    # Feed text can contain XML-illegal C0 characters even when escaped.
    text = str(value or "")
    return escape("".join(c for c in text if c in "\t\n\r" or
                          0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or
                          0x10000 <= ord(c) <= 0x10FFFF))


def rss_remix(db, user_id: str, limit: int = 50) -> str:
    rows = publications(db, user_id, exclude_backfill=True, limit=limit)
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<rss version="2.0"><channel><title>Driftread 個人閱讀</title>',
             '<description>訂閱來源提供的摘要與原文連結</description><link>https://github.com/dwvwdv/Driftread</link>']
    for row in rows:
        parts.append('<item><guid isPermaLink="false">urn:driftread:article:' +
                     _xml(row["id"]) + '</guid><title>' + _xml(row["title"]) +
                     '</title><link>' + _xml(row["url"]) + '</link><description>' +
                     _xml(row.get("summary")) + '</description>')
        stamp = row.get("timeline_at") or row.get("published_at")
        if stamp:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if isinstance(stamp, str) else stamp
            parts.append('<pubDate>' + format_datetime(stamp.astimezone(timezone.utc), usegmt=True) + '</pubDate>')
        parts.append('</item>')
    parts.append('</channel></rss>')
    return "".join(parts)
