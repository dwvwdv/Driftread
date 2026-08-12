from __future__ import annotations
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
