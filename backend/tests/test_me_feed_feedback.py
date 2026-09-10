"""GET/PUT/DELETE /me/feed-feedback (TODO.md 推薦回饋持久化) — the server-side
store for 猜你喜歡's 喜歡／不喜歡／跳過, so it survives across devices instead
of living only in the caller's localStorage (RecommendationService)."""
from __future__ import annotations
import os
import time
from unittest.mock import MagicMock

import jwt

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-please-change-and-make-32-bytes-long")

FEED_ID = "11111111-1111-1111-1111-111111111111"


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


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


def test_list_feed_feedback(client):
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[
            {
                "feed_id": FEED_ID,
                "feedback_type": "liked",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
    )

    resp = c.get("/api/me/feed-feedback", headers=_auth())

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["feed_id"] == FEED_ID
    assert body[0]["feedback_type"] == "liked"


def test_list_feed_feedback_requires_login(client):
    c, _ = client
    resp = c.get("/api/me/feed-feedback")
    assert resp.status_code == 401


def test_set_feed_feedback_upserts_on_conflict(client):
    c, mock_db = client
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    resp = c.put(
        f"/api/me/feed-feedback/{FEED_ID}",
        json={"feedback_type": "disliked"},
        headers=_auth(),
    )

    assert resp.status_code == 204
    upsert_call = mock_db.table.return_value.upsert.call_args
    payload = upsert_call[0][0]
    assert payload["user_id"] == "user-abc"
    assert payload["feed_id"] == FEED_ID
    assert payload["feedback_type"] == "disliked"
    assert "created_at" in payload
    assert upsert_call[1]["on_conflict"] == "user_id,feed_id"


def test_set_feed_feedback_rejects_unknown_type(client):
    """`feedback_type` is a `Literal["liked", "disliked", "skipped"]` — an
    unrecognized value fails request validation before any DB call, matching
    the DB-side CHECK constraint (migration 019) rather than relying on it as
    the only guard."""
    c, mock_db = client

    resp = c.put(
        f"/api/me/feed-feedback/{FEED_ID}",
        json={"feedback_type": "loved"},
        headers=_auth(),
    )

    assert resp.status_code == 422
    mock_db.table.assert_not_called()


def test_clear_feed_feedback(client):
    c, mock_db = client
    chain = mock_db.table.return_value.delete.return_value.eq.return_value
    chain.eq.return_value.execute.return_value = MagicMock(data=[])

    resp = c.delete(f"/api/me/feed-feedback/{FEED_ID}", headers=_auth())

    assert resp.status_code == 204
    chain.eq.assert_called_once_with("feed_id", FEED_ID)
