from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

from services.articles import CHUNK_SIZE, upsert_articles


@dataclass
class _FakeArticle:
    url: str | None
    title: str = "title"
    summary: str | None = None
    content: str | None = None
    author: str | None = None
    published_at: datetime | None = None


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self):
        self.calls: list[list[dict]] = []


class _FakeDB:
    def __init__(self):
        self.articles = _FakeTable()
        self.touched = None

    def rpc(self, name, params):
        assert name == "ingest_article_batch"
        assert params["p_feed_id"] == "feed-1"
        rows = params["p_articles"]
        self.articles.calls.append(rows)
        count = len(rows) if self.touched is None else self.touched
        class Request:
            def execute(self):
                return _FakeResult(count)
        return Request()


def test_upsert_articles_empty_list_makes_no_call():
    db = _FakeDB()
    assert upsert_articles(db, "feed-1", []) == 0
    assert db.articles.calls == []


def test_upsert_articles_skips_missing_url():
    db = _FakeDB()
    articles = [_FakeArticle(url=""), _FakeArticle(url=None), _FakeArticle(url="https://c")]
    assert upsert_articles(db, "feed-1", articles) == 1
    assert len(db.articles.calls[0]) == 1


def test_upsert_articles_dedupes_by_url_keeping_last_write():
    db = _FakeDB()
    articles = [
        _FakeArticle(url="https://a", title="first"),
        _FakeArticle(url="https://a", title="corrected"),
        _FakeArticle(url="https://b"),
    ]
    count = upsert_articles(db, "feed-1", articles)
    assert count == 2
    assert len(db.articles.calls) == 1
    rows = db.articles.calls[0]
    urls = [row["url"] for row in rows]
    assert urls == ["https://a", "https://b"]
    assert next(r for r in rows if r["url"] == "https://a")["title"] == "corrected"


def test_upsert_articles_chunks_large_batches():
    db = _FakeDB()
    articles = [_FakeArticle(url=f"https://x/{i}") for i in range(CHUNK_SIZE + 5)]
    count = upsert_articles(db, "feed-1", articles)
    assert count == CHUNK_SIZE + 5
    assert len(db.articles.calls) == 2
    assert len(db.articles.calls[0]) == CHUNK_SIZE
    assert len(db.articles.calls[1]) == 5


def test_upsert_articles_serializes_published_at():
    db = _FakeDB()
    when = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    upsert_articles(db, "feed-1", [_FakeArticle(url="https://a", published_at=when)])
    row = db.articles.calls[0][0]
    assert row["published_at"] == when.isoformat()
    assert row["feed_id"] == "feed-1"


def test_unchanged_ingestion_returns_actual_changed_count(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")
    db = _FakeDB()
    db.touched = 0
    assert upsert_articles(db, "feed-1", [_FakeArticle(url="https://a")]) == 0


def test_source_hash_tracks_content_and_summary_not_title(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")
    db = _FakeDB()
    upsert_articles(db, "feed-1", [_FakeArticle(url="https://a", content="body")])
    first = db.articles.calls[-1][0]["content_hash"]
    upsert_articles(db, "feed-1", [_FakeArticle(url="https://a", content="body", title="new")])
    assert db.articles.calls[-1][0]["content_hash"] == first
    upsert_articles(db, "feed-1", [_FakeArticle(url="https://a", content="changed")])
    assert db.articles.calls[-1][0]["content_hash"] != first


def test_disabled_discovery_ingestion_never_creates_network_client(monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")
    with patch("httpx.AsyncClient", side_effect=AssertionError("network")):
        assert upsert_articles(_FakeDB(), "feed-1", [
            _FakeArticle(url="https://a", content='<a href="https://b.example.org">x</a>')
        ]) == 1


def test_discovery_failure_does_not_undo_durable_ingestion(monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")
    db = _FakeDB()
    with patch("services.articles.build_host_index", side_effect=RuntimeError("frontier offline")):
        assert upsert_articles(db, "feed-1", [_FakeArticle(url="https://a")]) == 1
    assert len(db.articles.calls) == 1


def test_incoming_html_is_parsed_before_ingestion(monkeypatch):
    from unittest.mock import patch
    from services.link_harvest import HostIndex, DocumentExtraction
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")
    db = _FakeDB()
    class FeedQuery:
        def select(self, *args): return self
        def eq(self, *args): return self
        def limit(self, *args): return self
        def execute(self): return _FakeResult([{"id": "feed-1"}])
    db.table = lambda name: FeedQuery()
    original_rpc = db.rpc
    observed = []
    def rpc(name, params):
        assert observed == ["parse"]
        observed.append("store")
        return original_rpc(name, params)
    db.rpc = rpc
    def extract(*args):
        observed.append("parse")
        return DocumentExtraction([("friend.example.org", "https://friend.example.org/x")], True)
    with (
        patch("services.articles.extract_document_hosts", side_effect=extract),
        patch("services.articles.build_host_index", return_value=HostIndex(frozenset(), {})),
        patch("services.articles.harvest_pending_articles") as harvest,
    ):
        upsert_articles(db, "feed-1", [_FakeArticle(url="https://a", content="source HTML")])
    assert observed == ["parse", "store"]
    prepared = harvest.call_args.kwargs["prepared"]
    assert next(iter(prepared.values())).pairs == [("friend.example.org", "https://friend.example.org/x")]


def test_disabled_discovery_does_not_parse_source_html(monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")
    with patch("services.articles.extract_document_hosts", side_effect=AssertionError("parse")):
        assert upsert_articles(_FakeDB(), "feed-1", [_FakeArticle(url="https://a")]) == 1
