from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from rss_parser import ConditionalFetch, ParsedArticle, ParsedFeed
from services import feed_refresh
from services.feed_refresh import (
    AUTO_ARCHIVE_FAILURE_THRESHOLD,
    next_interval,
    refresh_due,
    refresh_one,
    select_due_feeds,
    summarize,
)


# ---------------------------------------------------------------- fake supabase

class _FakeQuery:
    """Records the filter chain so tests can assert on the composed query, and
    returns a canned result. Mirrors the subset of postgrest-py the service uses.
    """

    def __init__(self, table: "_FakeTable", op_log: list):
        self._table = table
        self._log = op_log

    def _record(self, name, *args):
        self._log.append((name, args))
        return self

    select = lambda self, *a, **k: self._record("select", *a, *sorted(k.items()))  # noqa: E731
    eq = lambda self, *a: self._record("eq", *a)  # noqa: E731
    is_ = lambda self, *a: self._record("is_", *a)  # noqa: E731
    lte = lambda self, *a: self._record("lte", *a)  # noqa: E731
    order = lambda self, *a, **k: self._record("order", *a)  # noqa: E731
    limit = lambda self, *a: self._record("limit", *a)  # noqa: E731
    update = lambda self, *a: self._record("update", *a)  # noqa: E731
    upsert = lambda self, *a, **k: self._record("upsert", *a)  # noqa: E731

    def execute(self):
        return self._table.result


class _FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data if data is not None else []
        self.count = count


class _FakeTable:
    def __init__(self, result):
        self.result = result
        self.ops: list = []

    def query(self):
        return _FakeQuery(self, self.ops)


class _FakeDB:
    """Routes .table(name) to a per-table fake, recording every chain."""

    def __init__(self, feeds_result=None, articles_counts=None, upsert_result=None):
        self.feeds = _FakeTable(feeds_result or _FakeResult([]))
        self._articles_counts = list(articles_counts or [])
        self._upsert_result = upsert_result or _FakeResult([])
        self.articles_ops: list = []
        self.feed_updates: list[dict] = []

    def table(self, name):
        if name == "feeds":
            return _FeedsProxy(self)
        if name == "articles":
            return _ArticlesProxy(self)
        raise AssertionError(f"unexpected table {name}")


class _FeedsProxy(_FakeQuery):
    def __init__(self, db):
        super().__init__(db.feeds, db.feeds.ops)
        self._db = db

    def update(self, payload):
        self._db.feed_updates.append(payload)
        return self._record("update", payload)


class _ArticlesProxy:
    def __init__(self, db):
        self._db = db
        self._is_count = False
        self._rows = None

    def select(self, *args, **kwargs):
        self._is_count = kwargs.get("head") is True
        self._db.articles_ops.append(("select", args, kwargs))
        return self

    def eq(self, *args):
        self._db.articles_ops.append(("eq", args))
        return self

    def upsert(self, rows, **kwargs):
        self._rows = rows
        self._db.articles_ops.append(("upsert", len(rows), kwargs))
        return self

    def execute(self):
        if self._is_count:
            counts = self._db._articles_counts
            value = counts.pop(0) if counts else 0
            return _FakeResult(count=value)
        return self._db._upsert_result


def _feed_row(**overrides) -> dict:
    row = {
        "id": str(uuid4()),
        "url": "https://example.com/feed.xml",
        "consecutive_failures": 0,
        "fetch_interval_minutes": 60,
        "etag": None,
        "last_modified": None,
    }
    row.update(overrides)
    return row


def _parsed(n: int) -> ParsedFeed:
    return ParsedFeed(
        title="Feed",
        url="https://example.com",
        articles=[
            ParsedArticle(title=f"A{i}", url=f"https://example.com/{i}") for i in range(n)
        ],
    )


# ------------------------------------------------------------- next_interval

@pytest.mark.parametrize(
    "current,outcome,expected",
    [
        (60, "new", 30),          # halves toward the floor
        (30, "new", 15),
        (15, "new", 15),          # already at the floor, stays
        (20, "new", 15),          # 20//2 = 10 -> clamped up to the floor
        (60, "unchanged", 120),   # doubles
        (60, "failed", 120),
        (720, "unchanged", 1440),
        (1440, "unchanged", 1440),  # already at the ceiling, stays
        (1000, "failed", 1440),     # 2000 -> clamped down to the ceiling
        (5, "unchanged", 30),       # below floor: max(current, floor) * 2
    ],
)
def test_next_interval(current, outcome, expected):
    assert next_interval(current, outcome) == expected


def test_next_interval_respects_env_bounds(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_MIN_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("FEED_REFRESH_MAX_INTERVAL_MINUTES", "60")
    assert next_interval(20, "new") == 10
    assert next_interval(40, "unchanged") == 60


def test_next_interval_handles_zero_and_none_current():
    # A row written before migration 005 can carry 0/NULL; must not divide to 0
    # or schedule a tight loop.
    assert next_interval(0, "new") >= 15
    assert next_interval(None, "unchanged") >= 15


def test_next_interval_survives_inverted_bounds(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_MIN_INTERVAL_MINUTES", "100")
    monkeypatch.setenv("FEED_REFRESH_MAX_INTERVAL_MINUTES", "10")
    assert next_interval(60, "new") == 100


def test_env_int_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_BATCH_SIZE", "not-a-number")
    assert feed_refresh.batch_size() == 50
    monkeypatch.setenv("FEED_REFRESH_CONCURRENCY", "0")
    assert feed_refresh.concurrency() == 5


# ----------------------------------------------------------- select_due_feeds

def test_select_due_feeds_filters_archived_and_orders_by_due():
    rows = [_feed_row(), _feed_row()]
    db = _FakeDB(feeds_result=_FakeResult(rows))

    result = select_due_feeds(db, limit=25)

    assert result == rows
    ops = dict((name, args) for name, args in db.feeds.ops)
    assert ops["is_"] == ("archived_at", "null")
    assert ops["order"] == ("next_fetch_at",)
    assert ops["limit"] == (25,)
    # The due bound is compared against next_fetch_at, not last_fetched_at.
    assert ops["lte"][0] == "next_fetch_at"


def test_select_due_feeds_handles_empty_data():
    db = _FakeDB(feeds_result=_FakeResult(None))
    assert select_due_feeds(db, limit=10) == []


# --------------------------------------------------------------- refresh_one

@pytest.mark.asyncio
async def test_refresh_one_not_modified_skips_articles():
    db = _FakeDB()
    feed = _feed_row(etag='W/"abc"', fetch_interval_minutes=60)

    with (
        patch("services.feed_refresh.validate_fetch_url", side_effect=lambda u: u),
        patch(
            "services.feed_refresh.fetch_and_parse_conditional",
            return_value=ConditionalFetch(not_modified=True, etag='W/"abc"'),
        ),
        patch("services.feed_refresh.upsert_articles") as upsert,
    ):
        result = await refresh_one(db, feed)

    assert result.status == "not_modified"
    upsert.assert_not_called()
    assert db.articles_ops == []  # no count queries either

    update = db.feed_updates[-1]
    assert update["consecutive_failures"] == 0
    assert update["health_score"] == 100
    assert "article_count" not in update       # untouched
    assert update["fetch_interval_minutes"] == 120  # backed off
    assert update["last_fetched_at"]


@pytest.mark.asyncio
async def test_refresh_one_passes_stored_validators():
    db = _FakeDB(articles_counts=[0, 2])
    feed = _feed_row(etag='W/"tag"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT")

    with (
        patch("services.feed_refresh.validate_fetch_url", side_effect=lambda u: u),
        patch(
            "services.feed_refresh.fetch_and_parse_conditional",
            return_value=ConditionalFetch(not_modified=False, parsed=_parsed(2)),
        ) as fetch,
        patch("services.feed_refresh.upsert_articles", return_value=2),
    ):
        await refresh_one(db, feed)

    assert fetch.await_args.kwargs["etag"] == 'W/"tag"'
    assert fetch.await_args.kwargs["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"


@pytest.mark.asyncio
async def test_refresh_one_new_articles_shortens_interval_and_stores_cumulative_count():
    # 3 already cached, 5 after the upsert -> 2 genuinely new.
    db = _FakeDB(articles_counts=[3, 5])
    feed = _feed_row(fetch_interval_minutes=60)

    with (
        patch("services.feed_refresh.validate_fetch_url", side_effect=lambda u: u),
        patch(
            "services.feed_refresh.fetch_and_parse_conditional",
            return_value=ConditionalFetch(
                not_modified=False, parsed=_parsed(5), etag='W/"new"'
            ),
        ),
        patch("services.feed_refresh.upsert_articles", return_value=5),
    ):
        result = await refresh_one(db, feed)

    assert result.status == "updated"
    assert result.new_articles == 2
    assert result.upserted == 5
    assert result.total_articles == 5

    update = db.feed_updates[-1]
    # article_count is the cumulative total, NOT the 5 rows the upsert touched.
    assert update["article_count"] == 5
    assert update["fetch_interval_minutes"] == 30  # new content -> poll sooner
    assert update["etag"] == 'W/"new"'


@pytest.mark.asyncio
async def test_refresh_one_treats_zero_count_delta_as_unchanged():
    """The regression this pins down: upsert_articles returns rows *touched*, so
    a feed serving the same 5 items every poll reports 5 while nothing is new.
    Only the count delta may drive the backoff decision — otherwise every feed
    looks perpetually active and the interval never backs off.
    """
    db = _FakeDB(articles_counts=[5, 5])  # same count before and after
    feed = _feed_row(fetch_interval_minutes=60)

    with (
        patch("services.feed_refresh.validate_fetch_url", side_effect=lambda u: u),
        patch(
            "services.feed_refresh.fetch_and_parse_conditional",
            return_value=ConditionalFetch(not_modified=False, parsed=_parsed(5)),
        ),
        patch("services.feed_refresh.upsert_articles", return_value=5) as upsert,
    ):
        result = await refresh_one(db, feed)

    assert upsert.return_value == 5   # upsert did report touched rows
    assert result.new_articles == 0   # ...but nothing was actually new
    assert result.upserted == 5
    assert db.feed_updates[-1]["fetch_interval_minutes"] == 120  # backed off


@pytest.mark.asyncio
async def test_refresh_one_failure_records_health_and_backs_off():
    db = _FakeDB()
    feed = _feed_row(consecutive_failures=2, fetch_interval_minutes=60)

    with (
        patch("services.feed_refresh.validate_fetch_url", side_effect=lambda u: u),
        patch(
            "services.feed_refresh.fetch_and_parse_conditional",
            side_effect=RuntimeError("connection reset"),
        ),
    ):
        result = await refresh_one(db, feed)

    assert result.status == "failed"
    assert result.archived is False

    update = db.feed_updates[-1]
    assert update["consecutive_failures"] == 3
    assert update["health_score"] == 70  # 100 - 3*10
    assert "connection reset" in update["last_failure_reason"]
    assert update["fetch_interval_minutes"] == 120
    assert "archived_at" not in update


@pytest.mark.asyncio
async def test_refresh_one_auto_archives_at_threshold():
    db = _FakeDB()
    feed = _feed_row(consecutive_failures=AUTO_ARCHIVE_FAILURE_THRESHOLD - 1)

    with (
        patch("services.feed_refresh.validate_fetch_url", side_effect=lambda u: u),
        patch(
            "services.feed_refresh.fetch_and_parse_conditional",
            side_effect=RuntimeError("gone"),
        ),
    ):
        result = await refresh_one(db, feed)

    assert result.archived is True
    update = db.feed_updates[-1]
    assert update["consecutive_failures"] == AUTO_ARCHIVE_FAILURE_THRESHOLD
    assert update["health_score"] == 0
    assert update["archived_at"]


@pytest.mark.asyncio
async def test_refresh_one_truncates_long_failure_reason():
    db = _FakeDB()
    with (
        patch("services.feed_refresh.validate_fetch_url", side_effect=lambda u: u),
        patch(
            "services.feed_refresh.fetch_and_parse_conditional",
            side_effect=RuntimeError("x" * 900),
        ),
    ):
        await refresh_one(db, _feed_row())

    assert len(db.feed_updates[-1]["last_failure_reason"]) == 500


@pytest.mark.asyncio
async def test_refresh_one_records_ssrf_rejection_as_failure():
    """A feed whose URL now resolves to a private address must be counted as a
    failure (and eventually archived), not raise out of the batch.
    """
    from services.feed_discovery import DiscoveryError

    db = _FakeDB()
    with patch(
        "services.feed_refresh.validate_fetch_url",
        side_effect=DiscoveryError("Refusing to fetch private/loopback address"),
    ):
        result = await refresh_one(db, _feed_row())

    assert result.status == "failed"
    assert "private" in db.feed_updates[-1]["last_failure_reason"]


# --------------------------------------------------------------- refresh_due

@pytest.mark.asyncio
async def test_refresh_due_caps_concurrency():
    rows = [_feed_row() for _ in range(12)]
    db = _FakeDB(feeds_result=_FakeResult(rows))

    in_flight = 0
    peak = 0

    async def fake_refresh_one(_db, feed):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return feed_refresh.RefreshResult(feed_id=feed["id"], status="updated")

    with patch("services.feed_refresh.refresh_one", side_effect=fake_refresh_one):
        results = await refresh_due(db, limit=12, max_concurrency=3)

    assert len(results) == 12
    assert peak <= 3


@pytest.mark.asyncio
async def test_refresh_due_isolates_unexpected_errors():
    rows = [_feed_row(), _feed_row(), _feed_row()]
    db = _FakeDB(feeds_result=_FakeResult(rows))

    async def flaky(_db, feed):
        if feed["id"] == rows[1]["id"]:
            raise KeyError("malformed row")
        return feed_refresh.RefreshResult(feed_id=feed["id"], status="updated")

    with patch("services.feed_refresh.refresh_one", side_effect=flaky):
        results = await refresh_due(db, limit=10, max_concurrency=5)

    assert len(results) == 3
    assert [r.status for r in results] == ["updated", "failed", "updated"]
    assert results[1].feed_id == rows[1]["id"]


@pytest.mark.asyncio
async def test_refresh_due_returns_empty_when_nothing_due():
    db = _FakeDB(feeds_result=_FakeResult([]))
    with patch("services.feed_refresh.refresh_one") as one:
        assert await refresh_due(db) == []
    one.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_due_uses_env_defaults(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_BATCH_SIZE", "7")
    db = _FakeDB(feeds_result=_FakeResult([]))
    await refresh_due(db)
    assert dict(db.feeds.ops)["limit"] == (7,)


def test_summarize_counts_by_status():
    results = [
        feed_refresh.RefreshResult(feed_id="a", status="updated", new_articles=3),
        feed_refresh.RefreshResult(feed_id="b", status="not_modified"),
        feed_refresh.RefreshResult(feed_id="c", status="failed", archived=True),
    ]
    assert summarize(results) == {
        "processed": 3,
        "updated": 1,
        "not_modified": 1,
        "failed": 1,
        "archived": 1,
        "new_articles": 3,
    }
