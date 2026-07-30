"""An in-memory stand-in for the Supabase client, for the discovery services.

tests/test_feed_refresh.py has its own `_FakeDB`, and it is deliberately left
alone: it only knows `feeds` and `articles` and raises on anything else, which is
exactly the property that made it catch bugs. Widening it would blunt that.

This one goes further than recording the filter chain — it actually applies the
filters to in-memory rows, so tests can assert on resulting *state* ("the
rejected row's status is still rejected") rather than only on the ops that were
issued. Both matter, so `db.ops` is still recorded for query-shape assertions
that pin a service call to the partial index it is meant to use.

The same discipline is kept: a table the test didn't declare raises AssertionError
rather than silently answering with an empty list.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

_MISSING = object()


class FakeResult:
    def __init__(self, data: Any = None, count: int | None = None):
        self.data = [] if data is None else data
        self.count = count


class _Not:
    """Backs `query.not_.is_(...)`, the postgrest-py negation form."""

    def __init__(self, query: "FakeQuery"):
        self._q = query

    def is_(self, column: str, value: str) -> "FakeQuery":
        self._q._record("not_.is_", column, value)
        if value == "null":
            self._q._filters.append(lambda row: row.get(column) is not None)
        return self._q

    def in_(self, column: str, values: list) -> "FakeQuery":
        self._q._record("not_.in_", column, values)
        wanted = {str(v) for v in values}
        self._q._filters.append(lambda row: str(row.get(column)) not in wanted)
        return self._q


class FakeQuery:
    def __init__(self, db: "FakeDB", table: str):
        self._db = db
        self._table = table
        self._filters: list = []
        self._orders: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._range: tuple[int, int] | None = None
        self._mode = "select"
        self._payload: Any = None
        self._on_conflict: str | None = None
        self._count_mode: str | None = None
        self._head = False
        self._single = False

    # -- recording ---------------------------------------------------------
    def _record(self, name: str, *args) -> None:
        self._db.ops.append((self._table, name, args))

    # -- verbs -------------------------------------------------------------
    def select(self, *columns, **kwargs) -> "FakeQuery":
        self._record("select", *columns, *sorted(kwargs.items()))
        self._mode = "select"
        self._count_mode = kwargs.get("count")
        self._head = bool(kwargs.get("head"))
        return self

    def insert(self, rows) -> "FakeQuery":
        self._record("insert", len(rows) if isinstance(rows, list) else 1)
        self._mode = "insert"
        self._payload = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, payload: dict) -> "FakeQuery":
        self._record("update", payload)
        self._mode = "update"
        self._payload = payload
        self._db.updates.append((self._table, payload))
        return self

    def upsert(self, rows, on_conflict: str | None = None, **kwargs) -> "FakeQuery":
        self._record("upsert", len(rows) if isinstance(rows, list) else 1,
                     ("on_conflict", on_conflict))
        self._mode = "upsert"
        self._payload = rows if isinstance(rows, list) else [rows]
        self._on_conflict = on_conflict
        self._db.upserts.append((self._table, self._payload, on_conflict))
        return self

    def delete(self) -> "FakeQuery":
        self._record("delete")
        self._mode = "delete"
        return self

    # -- filters -----------------------------------------------------------
    def eq(self, column: str, value) -> "FakeQuery":
        self._record("eq", column, value)
        self._filters.append(lambda row: str(row.get(column)) == str(value))
        return self

    def neq(self, column: str, value) -> "FakeQuery":
        self._record("neq", column, value)
        self._filters.append(lambda row: str(row.get(column)) != str(value))
        return self

    def lte(self, column: str, value) -> "FakeQuery":
        self._record("lte", column, value)
        self._filters.append(
            lambda row: row.get(column) is not None and row[column] <= value
        )
        return self

    def gte(self, column: str, value) -> "FakeQuery":
        self._record("gte", column, value)
        self._filters.append(
            lambda row: row.get(column) is not None and row[column] >= value
        )
        return self

    def in_(self, column: str, values: list) -> "FakeQuery":
        self._record("in_", column, values)
        wanted = {str(v) for v in values}
        self._filters.append(lambda row: str(row.get(column)) in wanted)
        return self

    def is_(self, column: str, value: str) -> "FakeQuery":
        self._record("is_", column, value)
        if value == "null":
            self._filters.append(lambda row: row.get(column) is None)
        elif value in (True, "true"):
            self._filters.append(lambda row: row.get(column) is True)
        elif value in (False, "false"):
            self._filters.append(lambda row: row.get(column) is False)
        return self

    @property
    def not_(self) -> _Not:
        return _Not(self)

    # -- shaping -----------------------------------------------------------
    def order(self, column: str, desc: bool = False, **kwargs) -> "FakeQuery":
        self._record("order", column, ("desc", desc))
        self._orders.append((column, desc))
        return self

    def limit(self, n: int) -> "FakeQuery":
        self._record("limit", n)
        self._limit = n
        return self

    def range(self, start: int, end: int) -> "FakeQuery":
        self._record("range", start, end)
        self._range = (start, end)
        return self

    def maybe_single(self) -> "FakeQuery":
        self._record("maybe_single")
        self._single = True
        return self

    def single(self) -> "FakeQuery":  # pragma: no cover - services must not use it
        raise AssertionError(
            ".single() 500s on zero rows (SECURITY.md #20) — use .maybe_single()"
        )

    # -- execution ---------------------------------------------------------
    def _matching(self) -> list[dict]:
        rows = [r for r in self._db.rows(self._table) if all(f(r) for f in self._filters)]
        for column, desc in reversed(self._orders):
            rows.sort(
                key=lambda r: (r.get(column) is None, r.get(column)), reverse=desc
            )
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def _conflict_key(self, row: dict) -> tuple:
        cols = (self._on_conflict or "").split(",")
        return tuple(str(row.get(c.strip())) for c in cols)

    def execute(self) -> FakeResult:
        store = self._db.rows(self._table)

        if self._mode == "select":
            rows = self._matching()
            if self._head:
                return FakeResult(data=[], count=len(rows))
            if self._single:
                return FakeResult(data=dict(rows[0]) if rows else None)
            count = len(
                [r for r in self._db.rows(self._table) if all(f(r) for f in self._filters)]
            ) if self._count_mode else None
            return FakeResult(data=[dict(r) for r in rows], count=count)

        if self._mode == "insert":
            created = []
            for payload in self._payload:
                row = self._db.new_row(self._table, payload)
                store.append(row)
                created.append(dict(row))
            return FakeResult(data=created)

        if self._mode == "upsert":
            touched = []
            for payload in self._payload:
                existing = None
                if self._on_conflict:
                    key = self._conflict_key(payload)
                    for row in store:
                        if self._conflict_key(row) == key:
                            existing = row
                            break
                if existing is not None:
                    existing.update(payload)
                    touched.append(dict(existing))
                else:
                    row = self._db.new_row(self._table, payload)
                    store.append(row)
                    touched.append(dict(row))
            return FakeResult(data=touched)

        if self._mode == "update":
            updated = []
            for row in self._matching():
                row.update(self._payload)
                updated.append(dict(row))
            return FakeResult(data=updated)

        if self._mode == "delete":
            doomed = self._matching()
            ids = {id(r) for r in doomed}
            self._db._tables[self._table] = [r for r in store if id(r) not in ids]
            return FakeResult(data=[dict(r) for r in doomed])

        raise AssertionError(f"unknown mode {self._mode}")  # pragma: no cover


# Column defaults, so an insert that omits them still round-trips the way the
# migration's DEFAULTs would. Only the columns the services actually read back.
_DEFAULTS: dict[str, dict] = {
    "discovery_targets": {
        "source": "article_link",
        "source_feed_id": None,
        "referring_feed_count": 0,
        "status": "pending",
        "attempts": 0,
        "feeds_found": 0,
        "last_probe_at": None,
        "last_failure_reason": None,
    },
    "discovery_candidates": {
        "target_id": None,
        "title": None,
        "website_url": None,
        "source_host": None,
        "referring_feed_count": 0,
        "status": "pending",
        "feed_id": None,
        "review_note": None,
        "reviewed_at": None,
    },
    "discovery_sources": {
        "kind": "links_page",
        "label": None,
        "enabled": True,
        "interval_hours": 168,
        "attempts": 0,
        "last_harvested_at": None,
        "last_failure_reason": None,
        "targets_created": 0,
    },
    "discovery_target_referrers": {},
    "feeds": {
        "description": None,
        "website_url": None,
        "language": None,
        "category": None,
        "tags": [],
        "article_count": 0,
        "archived_at": None,
    },
    "articles": {},
}

_TIMESTAMP_DEFAULTS = ("created_at", "updated_at")


class FakeDB:
    """A Supabase client stand-in over in-memory row lists.

    Construct with the tables the code under test is allowed to touch:

        db = FakeDB(discovery_targets=[...], feeds=[...])

    Anything else raises, so an unexpected table is a test failure rather than a
    silent empty result.
    """

    def __init__(self, **tables: list[dict]):
        self._tables: dict[str, list[dict]] = {
            name: [dict(r) for r in rows] for name, rows in tables.items()
        }
        self.ops: list[tuple[str, str, tuple]] = []
        self.updates: list[tuple[str, dict]] = []
        self.upserts: list[tuple[str, list, str | None]] = []

    def table(self, name: str) -> FakeQuery:
        if name not in self._tables:
            raise AssertionError(f"unexpected table {name!r}")
        return FakeQuery(self, name)

    def rows(self, name: str) -> list[dict]:
        return self._tables[name]

    def new_row(self, table: str, payload: dict) -> dict:
        row = dict(_DEFAULTS.get(table, {}))
        row.setdefault("id", str(uuid4()))
        for column in _TIMESTAMP_DEFAULTS:
            row.setdefault(column, "2026-01-01T00:00:00+00:00")
        row.update(payload)
        return row

    # -- assertion helpers -------------------------------------------------
    def ops_for(self, table: str) -> list[tuple[str, tuple]]:
        return [(name, args) for t, name, args in self.ops if t == table]

    def op_names(self, table: str) -> list[str]:
        return [name for t, name, _ in self.ops if t == table]
