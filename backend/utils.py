from __future__ import annotations
import base64
import math
from datetime import datetime
from uuid import UUID


def encode_keyset_cursor(marker: datetime, row_id: UUID | str) -> str:
    """Opaque cursor for a (timestamp, id) keyset-paginated list — the same
    shape `GET /me/reads` already used inline; factored out so the reading
    stream (`GET /me/stream`) can share it instead of duplicating the format.
    """
    raw = f"{marker.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_keyset_cursor(cursor: str) -> tuple[str, str]:
    """Inverse of encode_keyset_cursor. Raises ValueError on anything that
    doesn't decode to a well-formed `<isoformat datetime>|<uuid>` pair —
    callers turn that into a 400 rather than letting a malformed cursor
    reach the database as a filter value."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        marker_raw, id_raw = raw.rsplit("|", 1)
        datetime.fromisoformat(marker_raw)
        UUID(id_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc
    return marker_raw, id_raw


def encode_rank_cursor(rank: float, marker: datetime, row_id: UUID | str) -> str:
    """Opaque cursor for a (rank, timestamp, id) keyset-paginated search result
    list — same shape as encode_keyset_cursor, with a leading relevance rank
    so rows that tie on rank still fall back to the existing (marker, id)
    tie-break. `repr(rank)` round-trips exactly for a Python float parsed
    back out of a Postgres `real`, since `real` -> Python `float` is a lossless
    upcast and `repr` on a `float` is already the shortest string that
    reparses to the same value.
    """
    raw = f"{rank!r}|{marker.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_rank_cursor(cursor: str) -> tuple[float, str, str]:
    """Inverse of encode_rank_cursor. Raises ValueError on anything that
    doesn't decode to a well-formed `<float>|<isoformat datetime>|<uuid>`
    triple — callers turn that into a 400 rather than letting a malformed
    cursor reach the database as a filter value.

    `float()` happily parses 'nan'/'inf'/'-inf', which encode_rank_cursor
    itself never produces (`ts_rank_cd` returns a normal Postgres `real`) —
    but a hand-crafted cursor could still spell one, and forwarding a
    non-finite value as the RPC's `real` parameter is exactly the kind of
    malformed input this cursor is supposed to turn into a clean 400 before
    it ever reaches the database.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        rank_raw, marker_raw, id_raw = raw.split("|", 2)
        rank = float(rank_raw)
        if not math.isfinite(rank):
            raise ValueError("Invalid cursor")
        datetime.fromisoformat(marker_raw)
        UUID(id_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc
    return rank, marker_raw, id_raw


def escape_postgrest_literal(value: str) -> str:
    """Escape a value for safe embedding in a PostgREST filter expression
    (e.g. the string passed to `.or_()`).

    PostgREST's filter mini-language treats `,`, `.`, `(`, `)` as syntax
    (they separate/nest conditions), so a raw user value containing them can
    inject extra filter conditions. Wrapping the value in double quotes turns
    it into a literal; backslash and double-quote must then be escaped so
    they can't terminate the quoted literal early.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
