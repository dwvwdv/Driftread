from __future__ import annotations
import os
import time
from unittest.mock import MagicMock

import jwt


def _token() -> str:
    return jwt.encode(
        {
            "sub": "user-abc",
            "aud": "authenticated",
            "is_anonymous": False,
            "exp": int(time.time()) + 3600,
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def _article_row(**overrides):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "feed_id": "22222222-2222-2222-2222-222222222222",
        "feed_title": "Some Feed",
        "title": "Some Article",
        "url": "https://example.com/a",
        "summary": "short summary",
        "snippet": "a <b>match</b> snippet",
        "author": "Jane",
        "published_at": "2026-08-14T10:00:00+00:00",
        "fetched_at": "2026-08-14T10:05:00+00:00",
        "is_read": False,
        "is_bookmarked": False,
        "rank": 0.5,
    }
    row.update(overrides)
    return row


def _feed_row(**overrides):
    row = {
        "id": "22222222-2222-2222-2222-222222222222",
        "title": "Some Feed",
        "url": "https://example.com/feed.xml",
        "description": "a feed about things",
        "snippet": "a feed about <b>things</b>",
        "website_url": "https://example.com",
        "language": "en",
        "category": "Tech",
        "tags": [],
        "article_count": 3,
        "created_at": "2026-08-14T10:00:00+00:00",
        "rank": 0.5,
    }
    row.update(overrides)
    return row


# --- /search/articles -----------------------------------------------------


def test_search_articles_calls_rpc_with_query_and_no_user(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row()])

    resp = c.get("/api/search/articles", params={"q": "hello world"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["is_read"] is False
    assert body["items"][0]["feed_title"] == "Some Feed"
    name, params = mock_db.rpc.call_args[0]
    assert name == "search_articles"
    assert params["p_query"] == "hello world"
    assert params["p_user_id"] is None


def test_search_articles_passes_user_id_when_authenticated(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(
        data=[_article_row(is_read=True, is_bookmarked=True)]
    )

    resp = c.get(
        "/api/search/articles",
        params={"q": "hello"},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["is_read"] is True
    assert body["items"][0]["is_bookmarked"] is True
    _, params = mock_db.rpc.call_args[0]
    assert params["p_user_id"] == "user-abc"


def test_search_articles_passes_language_filter(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[])

    resp = c.get("/api/search/articles", params={"q": "hello", "language": "en"})

    assert resp.status_code == 200
    _, params = mock_db.rpc.call_args[0]
    assert params["p_language"] == "en"


def test_search_articles_requires_non_empty_query(client):
    c, _ = client
    resp = c.get("/api/search/articles", params={"q": ""})
    assert resp.status_code == 422


def test_search_articles_returns_next_cursor_on_a_full_page(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row()])

    resp = c.get("/api/search/articles", params={"q": "hello", "limit": 1})

    assert resp.status_code == 200
    assert resp.json()["next_cursor"] is not None


def test_search_articles_omits_next_cursor_on_a_partial_page(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row()])

    resp = c.get("/api/search/articles", params={"q": "hello", "limit": 20})

    assert resp.status_code == 200
    assert resp.json()["next_cursor"] is None


def test_search_articles_rejects_malformed_cursor(client):
    c, _ = client
    resp = c.get(
        "/api/search/articles",
        params={"q": "hello", "cursor": "not-valid-base64!!"},
    )
    assert resp.status_code == 400


def test_search_articles_decodes_cursor_into_rpc_params(client):
    from utils import encode_rank_cursor
    from datetime import datetime, timezone

    c, mock_db = client
    cursor = encode_rank_cursor(
        0.75,
        datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc),
        "11111111-1111-1111-1111-111111111111",
    )
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[])

    resp = c.get("/api/search/articles", params={"q": "hello", "cursor": cursor})

    assert resp.status_code == 200
    _, params = mock_db.rpc.call_args[0]
    assert params["p_cursor_rank"] == 0.75
    assert params["p_cursor_sort_at"] == "2026-08-14T10:00:00+00:00"
    assert params["p_cursor_id"] == "11111111-1111-1111-1111-111111111111"


# --- /search/feeds ----------------------------------------------------------


def test_search_feeds_calls_rpc_with_query(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_feed_row()])

    resp = c.get("/api/search/feeds", params={"q": "things"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "Some Feed"
    name, params = mock_db.rpc.call_args[0]
    assert name == "search_feeds"
    assert params["p_query"] == "things"


def test_search_feeds_requires_non_empty_query(client):
    c, _ = client
    resp = c.get("/api/search/feeds", params={"q": ""})
    assert resp.status_code == 422


def test_search_feeds_returns_next_cursor_on_a_full_page(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_feed_row()])

    resp = c.get("/api/search/feeds", params={"q": "things", "limit": 1})

    assert resp.status_code == 200
    assert resp.json()["next_cursor"] is not None


def test_search_feeds_rejects_malformed_cursor(client):
    c, _ = client
    resp = c.get(
        "/api/search/feeds",
        params={"q": "things", "cursor": "not-valid-base64!!"},
    )
    assert resp.status_code == 400
