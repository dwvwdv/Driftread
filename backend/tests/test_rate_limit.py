from __future__ import annotations
from unittest.mock import patch

import rate_limit


def test_bounds_total_tracked_clients(client):
    c, _mock_db = client
    body = {"feed_url": "http://127.0.0.1/x"}
    with patch.object(rate_limit, "MAX_TRACKED_CLIENTS", 3):
        for i in range(10):
            c.post(
                "/api/discover/import",
                json=body,
                headers={"x-forwarded-for": f"203.0.113.{i}"},
            )
            assert len(rate_limit._hits) <= 3


def test_active_client_is_not_evicted_by_others(client):
    """A client that keeps making requests must stay in its own quota window
    (LRU touch), even while enough *other* distinct clients pass through to
    have overflowed the cap several times over."""
    c, _mock_db = client
    body = {"feed_url": "http://127.0.0.1/x"}
    active_headers = {"x-forwarded-for": "198.51.100.1"}
    with patch.object(rate_limit, "MAX_TRACKED_CLIENTS", 3):
        c.post("/api/discover/import", json=body, headers=active_headers)
        for i in range(20):
            c.post(
                "/api/discover/import",
                json=body,
                headers={"x-forwarded-for": f"203.0.113.{i}"},
            )
            # Touch the active client again so it's never the LRU victim.
            c.post("/api/discover/import", json=body, headers=active_headers)
        assert ("discover_import", "198.51.100.1") in rate_limit._hits
