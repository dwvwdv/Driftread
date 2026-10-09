"""Shared source guards for service-role reads and public import paths."""
from fastapi import HTTPException


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
