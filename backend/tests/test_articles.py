from __future__ import annotations
import os
import time
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest


@pytest.mark.parametrize("execute_return", [MagicMock(data=None), None])
def test_get_article_not_found_returns_404(client, execute_return):
    # Same postgrest-py gotcha as test_feeds.py: .single() raises on 0 rows,
    # and some maybe_single() versions return bare None from execute() rather
    # than a response object with data=None — both shapes must 404 cleanly.
    c, mock_db = client
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.maybe_single.return_value.execute.return_value = execute_return

    resp = c.get(f"/api/articles/{uuid4()}")

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()


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
        "title": "Some Article",
        "url": "https://example.com/a",
        "summary": "short summary",
        "author": "Jane",
        "published_at": "2026-08-14T10:00:00+00:00",
        "fetched_at": "2026-08-14T10:05:00+00:00",
        "is_read": False,
        "is_bookmarked": False,
    }
    row.update(overrides)
    return row


def test_list_feed_articles_calls_rpc_with_feed_id_and_no_user(client):
    # Public endpoint — an anonymous caller must still get a page back, with
    # is_read/is_bookmarked resolved to false rather than the request failing.
    c, mock_db = client
    feed_id = "22222222-2222-2222-2222-222222222222"
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row()])

    resp = c.get(f"/api/feeds/{feed_id}/articles")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["is_read"] is False
    name, params = mock_db.rpc.call_args[0]
    assert name == "list_feed_articles"
    assert params["p_feed_id"] == feed_id
    assert params["p_user_id"] is None


def test_list_feed_articles_passes_user_id_when_authenticated(client):
    c, mock_db = client
    feed_id = "22222222-2222-2222-2222-222222222222"
    mock_db.rpc.return_value.execute.return_value = MagicMock(
        data=[_article_row(is_read=True, is_bookmarked=True)]
    )

    resp = c.get(
        f"/api/feeds/{feed_id}/articles",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["is_read"] is True
    assert body["items"][0]["is_bookmarked"] is True
    _, params = mock_db.rpc.call_args[0]
    assert params["p_user_id"] == "user-abc"


def test_list_feed_articles_returns_next_cursor_on_a_full_page(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row()])

    resp = c.get(
        f"/api/feeds/{uuid4()}/articles",
        params={"limit": 1},
    )

    assert resp.status_code == 200
    assert resp.json()["next_cursor"] is not None


def test_list_feed_articles_omits_next_cursor_on_a_partial_page(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row()])

    resp = c.get(
        f"/api/feeds/{uuid4()}/articles",
        params={"limit": 20},
    )

    assert resp.status_code == 200
    assert resp.json()["next_cursor"] is None


def test_list_feed_articles_rejects_malformed_cursor(client):
    c, _ = client
    resp = c.get(
        f"/api/feeds/{uuid4()}/articles",
        params={"cursor": "not-valid-base64!!"},
    )
    assert resp.status_code == 400


def test_list_feed_articles_decodes_cursor_into_rpc_params(client):
    import base64

    c, mock_db = client
    cursor = base64.urlsafe_b64encode(
        b"2026-08-14T10:00:00+00:00|11111111-1111-1111-1111-111111111111"
    ).decode()
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[])

    resp = c.get(
        f"/api/feeds/{uuid4()}/articles",
        params={"cursor": cursor},
    )

    assert resp.status_code == 200
    _, params = mock_db.rpc.call_args[0]
    assert params["p_cursor_sort_at"] == "2026-08-14T10:00:00+00:00"
    assert params["p_cursor_id"] == "11111111-1111-1111-1111-111111111111"
