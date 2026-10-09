"""Canonical article read projection shared by API and consumption surfaces."""
from datetime import datetime

PUBLICATION_COLUMNS = (
    "id,feed_id,title,url,summary,content,author,published_at,fetched_at,"
    "content_compacted_at,timeline_at,discovered_at,backfill,backfill_reason,current_revision_id,"
    "feed_title,feed_language,feed_archived_at,fulltext_allowed"
)


def get_publication(db, article_id: str) -> dict | None:
    result = (db.table("article_publications").select(PUBLICATION_COLUMNS)
              .eq("id", str(article_id)).maybe_single().execute())
    return result.data if result and result.data else None


def list_personal_publications(
    db, user_id: str, *, start: datetime | None = None, end: datetime | None = None,
    exclude_backfill: bool = False, limit: int = 100,
) -> list[dict]:
    result = db.rpc("list_personal_publications", {
        "p_user_id": user_id, "p_start": start.isoformat() if start else None,
        "p_end": end.isoformat() if end else None,
        "p_exclude_backfill": exclude_backfill, "p_limit": limit,
    }).execute()
    return [{k: v for k, v in row.items() if k != "search_vector"} for row in result.data]
