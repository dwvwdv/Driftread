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

    def upsert(self, rows, on_conflict=None):
        assert on_conflict == "url"
        self.calls.append(rows)
        return self

    def execute(self):
        return _FakeResult(self.calls[-1])


class _FakeDB:
    def __init__(self):
        self.articles = _FakeTable()

    def table(self, name):
        assert name == "articles"
        return self.articles


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
