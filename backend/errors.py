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

import re
from typing import Any

# PostgreSQL SQLSTATE codes (stable, defined by Postgres itself:
# https://www.postgresql.org/docs/current/errcodes-appendix.html).
_STATUS_BY_CODE: dict[str, tuple[int, str]] = {
    "23505": (409, "Resource already exists"),  # unique_violation
    "23503": (409, "Referenced resource does not exist"),  # foreign_key_violation
    "23502": (400, "Missing required field"),  # not_null_violation
    "23514": (400, "Value violates a data constraint"),  # check_violation
    "22P02": (400, "Invalid input value"),  # invalid_text_representation
    "42501": (403, "Not permitted"),  # insufficient_privilege (RLS denial)
}

# PGRST116 is in PostgREST's own "PGRSTxxx" namespace, not SQLSTATE — it
# covers *both* zero rows and multiple rows matching a query that asked for
# exactly one (`.single()`, or `.maybe_single()` once more than one row
# matches — maybe_single() only suppresses the zero-row case, see
# routers/feeds.py's version-defensive comments). Those two cases aren't the
# same problem: zero rows is an ordinary 404, but multiple rows means a
# query written to expect at most one match found several — e.g.
# routers/admin_discovery.py's seed_targets does `.eq("host", host)
# .maybe_single()`, and migration 006 explicitly allows several
# discovery_targets rows per host (unique on url, not host). That's a real
# bug/data-integrity condition worth investigating, not "not found", so it
# falls through to the generic 500 (and gets logged) instead.
_ROWS_IN_DETAILS = re.compile(r"Results contain (\d+) rows?")

_DEFAULT_STATUS_CODE = 500
_DEFAULT_DETAIL = "Internal server error"
_NOT_FOUND = (404, "Not found")


def map_postgrest_error(exc: Exception) -> tuple[int, dict[str, Any]]:
    """Returns `(status_code, json_body)` for a postgrest-py `APIError`.

    Reads `code`/`details` defensively via `getattr` rather than assuming
    the attributes are always present, matching this codebase's existing
    postgrest-py version-tolerance elsewhere.
    """
    code = getattr(exc, "code", None) or ""
    if code == "PGRST116":
        match = _ROWS_IN_DETAILS.search(getattr(exc, "details", None) or "")
        # Unparseable details is treated the same as "not zero" — safer to
        # fall through to the generic 500 than to assume a routine miss.
        status_code, detail = _NOT_FOUND if match and match.group(1) == "0" else (
            _DEFAULT_STATUS_CODE,
            _DEFAULT_DETAIL,
        )
    else:
        status_code, detail = _STATUS_BY_CODE.get(code, (_DEFAULT_STATUS_CODE, _DEFAULT_DETAIL))
    return status_code, {"detail": detail}
