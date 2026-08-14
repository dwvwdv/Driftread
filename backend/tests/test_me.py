from __future__ import annotations
import base64
import os
import time
from unittest.mock import MagicMock

import jwt

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-please-change-and-make-32-bytes-long")


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


def test_list_bookmarks_omits_article_content(client):
    """Bookmark rows only need summary fields for the list view; returning the
    full cached article HTML in `content` would bloat every fetch for data the
    list never renders."""
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(
        data=[
            {
                "articles": {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "feed_id": "22222222-2222-2222-2222-222222222222",
                    "title": "Some Article",
                    "url": "https://example.com/a",
                    "summary": "short summary",
                    "author": "Jane",
                    "published_at": None,
                }
            }
        ]
    )
    resp = c.get(
        "/api/me/bookmarks",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["title"] == "Some Article"
    assert "content" not in body[0]
    assert "fetched_at" not in body[0]

    select_args = mock_db.table.return_value.select.call_args[0][0]
    assert "content" not in select_args


def test_list_bookmarks_rejects_invalid_type(client):
    c, _ = client
    resp = c.get(
        "/api/me/bookmarks",
        params={"bookmark_type": "not-a-real-type"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 400


def _reads_chain(mock_db, with_cursor: bool = False):
    query = mock_db.table.return_value.select.return_value.eq.return_value
    if with_cursor:
        query = query.or_.return_value
    return query.order.return_value.order.return_value.limit.return_value


def test_list_reads_returns_next_cursor_on_a_full_page(client):
    # A full page (len(items) == limit) means there may be more rows, so the
    # route must hand back a cursor the caller can use to fetch the next one.
    c, mock_db = client
    _reads_chain(mock_db).execute.return_value = MagicMock(
        data=[
            {
                "article_id": "11111111-1111-1111-1111-111111111111",
                "read_at": "2026-08-14T10:00:00+00:00",
            }
        ]
    )

    resp = c.get(
        "/api/me/reads",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] is not None
    mock_db.table.return_value.select.return_value.eq.return_value.or_.assert_not_called()


def test_list_reads_omits_next_cursor_on_a_partial_page(client):
    # Fewer rows than `limit` means this is the last page.
    c, mock_db = client
    _reads_chain(mock_db).execute.return_value = MagicMock(data=[])

    resp = c.get(
        "/api/me/reads",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "next_cursor": None}


def test_list_reads_applies_keyset_filter_from_cursor(client):
    c, mock_db = client
    cursor = base64.urlsafe_b64encode(
        b"2026-08-14T10:00:00+00:00|11111111-1111-1111-1111-111111111111"
    ).decode()
    _reads_chain(mock_db, with_cursor=True).execute.return_value = MagicMock(data=[])

    resp = c.get(
        "/api/me/reads",
        params={"cursor": cursor},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    or_query = mock_db.table.return_value.select.return_value.eq.return_value.or_
    or_query.assert_called_once()
    filter_expr = or_query.call_args[0][0]
    assert "read_at.lt." in filter_expr
    assert "11111111-1111-1111-1111-111111111111" in filter_expr


def test_list_reads_rejects_malformed_cursor(client):
    c, _ = client
    resp = c.get(
        "/api/me/reads",
        params={"cursor": "not-valid-base64!!"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 400
