"""Maps PostgREST/PostgreSQL errors (postgrest-py's `APIError`) to HTTP
status codes and safe, generic response bodies.

Without this, any `APIError` raised out of a `.execute()` call — a unique
constraint violation on insert, a check constraint, an RLS denial, ... —
falls straight through to Starlette's default handler as a bare 500 with no
structured detail. See TODO.md's "技術與可靠性優化" entry for the gap this
closes; `main.py` registers `map_postgrest_error` against `APIError` via
`app.exception_handler`.

Only the codes below get a specific status and message. Anything else
(including a missing/unrecognized code) maps to a generic 500 so an
unmapped PostgreSQL error doesn't leak internal detail — table/column
names, raw constraint names — to the client; the caller is still
responsible for logging the real `exc.message`/`exc.details` server-side.
"""
from __future__ import annotations

from typing import Any

# PostgreSQL SQLSTATE codes (stable, defined by Postgres itself:
# https://www.postgresql.org/docs/current/errcodes-appendix.html) plus
# PGRST116, which is in PostgREST's own "PGRSTxxx" namespace rather than
# SQLSTATE — the error `.single()` raises for zero or multiple matching
# rows. This project only uses `.maybe_single()` today (see the
# postgrest-py version-defensive comments in routers/feeds.py and
# routers/admin.py), but the code is well-known and stable enough to map
# proactively rather than leave it defaulting to 500 the day `.single()`
# is first reached for.
_STATUS_BY_CODE: dict[str, tuple[int, str]] = {
    "23505": (409, "Resource already exists"),  # unique_violation
    "23503": (409, "Referenced resource does not exist"),  # foreign_key_violation
    "23502": (400, "Missing required field"),  # not_null_violation
    "23514": (400, "Value violates a data constraint"),  # check_violation
    "22P02": (400, "Invalid input value"),  # invalid_text_representation
    "42501": (403, "Not permitted"),  # insufficient_privilege (RLS denial)
    "PGRST116": (404, "Not found"),  # .single(): zero or multiple rows
}

_DEFAULT_STATUS_CODE = 500
_DEFAULT_DETAIL = "Internal server error"


def map_postgrest_error(exc: Exception) -> tuple[int, dict[str, Any]]:
    """Returns `(status_code, json_body)` for a postgrest-py `APIError`.

    Reads `code` defensively via `getattr` rather than assuming the
    attribute is always present, matching this codebase's existing
    postgrest-py version-tolerance elsewhere.
    """
    code = getattr(exc, "code", None) or ""
    status_code, detail = _STATUS_BY_CODE.get(code, (_DEFAULT_STATUS_CODE, _DEFAULT_DETAIL))
    return status_code, {"detail": detail}
