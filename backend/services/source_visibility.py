"""Shared source guards for service-role reads and public import paths."""
from fastapi import HTTPException

from services.source_identity import is_comment_feed, new_source_rejection


def require_readable_source(db, feed_id: str) -> dict:
    row = db.table("feeds").select("id").eq("id", feed_id).eq(
        "participation_mode", "normal"
    ).maybe_single().execute()
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Feed not found")
    return row.data


def import_readable_source(db, metadata: dict) -> dict | None:
    # The RPC inserts a new source but never overwrites an existing source.
    # In particular a supplied URL cannot reset an administrator's private
    # role or metadata via ON CONFLICT UPDATE.
    rows = db.rpc("import_readable_source", {"p_metadata": metadata}).execute()
    return rows.data[0] if rows and rows.data else None


def screen_new_source(db, feed_url: str, parsed) -> tuple[str, dict | None] | None:
    """Admission gate for paths that fetched a feed before creating its row.

    An URL already in the catalog keeps its existing behavior. A new URL is
    refused when it is a comment feed, or mapped onto the active source whose
    stored articles it duplicates. Returns ("comment_feed", None),
    ("mirror", feed_row) or None.
    """
    existing = db.table("feeds").select("id").eq("url", feed_url).maybe_single().execute()
    if existing and existing.data:
        return None
    if is_comment_feed(feed_url):
        return ("comment_feed", None)
    rejection = new_source_rejection(db, [article.url for article in parsed.articles])
    if not rejection or rejection[0] == "comment_feed":
        return rejection
    row = db.table("feeds").select("*").eq("id", rejection[1]["feed_id"]).eq(
        "participation_mode", "normal"
    ).is_("archived_at", "null").maybe_single().execute()
    # The source vanished between lookup and read: admit the new URL instead.
    return ("mirror", row.data) if row and row.data else None
