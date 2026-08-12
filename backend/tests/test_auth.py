from __future__ import annotations
import os
import time
from unittest.mock import MagicMock

import jwt

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-please-change-and-make-32-bytes-long")


def _token(
    user_id: str = "user-abc",
    secret: str | None = None,
    is_anonymous: bool | None = False,
) -> str:
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "email": "u@example.com",
        "exp": int(time.time()) + 3600,
    }
    if is_anonymous is not None:
        payload["is_anonymous"] = is_anonymous
    return jwt.encode(
        payload,
        secret or os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def test_me_feeds_requires_auth(client):
    c, _ = client
    resp = c.get("/api/me/feeds")
    assert resp.status_code == 401


def test_me_feeds_invalid_token(client):
    c, _ = client
    resp = c.get("/api/me/feeds", headers={"Authorization": "Bearer bogus"})
    assert resp.status_code == 401


def test_me_feeds_wrong_secret(client):
    c, _ = client
    bad = _token(secret="another-different-secret-of-sufficient-length")
    resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {bad}"})
    assert resp.status_code == 401


def test_me_feeds_valid_token(client):
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[]
    )
    resp = c.get(
        "/api/me/feeds",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_me_feeds_rejects_anonymous_supabase_user(client):
    c, mock_db = client
    resp = c.get(
        "/api/me/feeds",
        headers={"Authorization": f"Bearer {_token(is_anonymous=True)}"},
    )

    assert resp.status_code == 403
    assert resp.json() == {"detail": "A permanent account is required"}
    mock_db.table.assert_not_called()


def test_me_feeds_rejects_token_without_permanent_user_claim(client):
    c, mock_db = client
    resp = c.get(
        "/api/me/feeds",
        headers={"Authorization": f"Bearer {_token(is_anonymous=None)}"},
    )

    assert resp.status_code == 403
    mock_db.table.assert_not_called()
