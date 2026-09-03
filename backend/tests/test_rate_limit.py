from __future__ import annotations
import os
import time
from unittest.mock import patch

import jwt

import rate_limit

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


def test_bounds_total_tracked_clients(client):
    c, _mock_db = client
    body = {"feed_url": "http://127.0.0.1/x"}
    auth = f"Bearer {_token()}"
    with patch.object(rate_limit, "MAX_TRACKED_CLIENTS", 3):
        for i in range(10):
            c.post(
                "/api/discover/import",
                json=body,
                headers={"x-forwarded-for": f"203.0.113.{i}", "Authorization": auth},
            )
            assert len(rate_limit._hits) <= 3


def test_active_client_is_not_evicted_by_others(client):
    """A client that keeps making requests must stay in its own quota window
    (LRU touch), even while enough *other* distinct clients pass through to
    have overflowed the cap several times over."""
    c, _mock_db = client
    body = {"feed_url": "http://127.0.0.1/x"}
    auth = f"Bearer {_token()}"
    active_headers = {"x-forwarded-for": "198.51.100.1", "Authorization": auth}
    with patch.object(rate_limit, "MAX_TRACKED_CLIENTS", 3):
        c.post("/api/discover/import", json=body, headers=active_headers)
        for i in range(20):
            c.post(
                "/api/discover/import",
                json=body,
                headers={"x-forwarded-for": f"203.0.113.{i}", "Authorization": auth},
            )
            # Touch the active client again so it's never the LRU victim.
            c.post("/api/discover/import", json=body, headers=active_headers)
        assert ("discover_import", "198.51.100.1") in rate_limit._hits
