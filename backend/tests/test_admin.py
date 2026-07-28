from __future__ import annotations
import os
from unittest.mock import MagicMock
from uuid import uuid4


def test_admin_endpoint_rejects_missing_key(client):
    c, _ = client
    resp = c.post("/api/admin/feeds/from-url", json={"feed_url": "https://example.com/rss"})
    # x_api_key is a required Header(...), so FastAPI's request validation
    # rejects a missing header (422) before _require_api_key ever runs.
    assert resp.status_code == 422


def test_admin_endpoint_rejects_wrong_key(client):
    c, _ = client
    resp = c.post(
        "/api/admin/feeds/from-url",
        json={"feed_url": "https://example.com/rss"},
        headers={"x-api-key": "wrong-key"},
    )
    assert resp.status_code == 403


def test_admin_endpoint_rejects_non_ascii_key(client):
    # secrets.compare_digest() raises TypeError on non-ASCII str operands;
    # a malformed header must still 403, not 500. httpx's own TestClient
    # refuses to encode a non-ASCII str header value client-side, so send
    # it as a pre-encoded (latin-1, per HTTP header convention) byte header
    # instead — this is what actually reaches the server on the wire.
    c, _ = client
    resp = c.post(
        "/api/admin/feeds/from-url",
        json={"feed_url": "https://example.com/rss"},
        headers=[(b"x-api-key", "wrong-ké".encode("latin-1"))],
    )
    assert resp.status_code == 403


def test_admin_import_from_url_rejects_private_ip(client):
    c, mock_db = client
    resp = c.post(
        "/api/admin/feeds/from-url",
        json={"feed_url": "http://10.0.0.5/internal"},
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )
    assert resp.status_code == 400
    mock_db.table.assert_not_called()


def _mock_feed_lookup_miss(mock_db):
    """Real postgrest-py's .single() raises on 0 rows instead of returning
    data=None (only .maybe_single() does that) — a MagicMock can't reproduce
    the raise, so callers assert .single() was never invoked instead."""
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.maybe_single.return_value.execute.return_value = MagicMock(data=None)
    return chain


def test_archive_feed_not_found_returns_404(client):
    c, mock_db = client
    chain = _mock_feed_lookup_miss(mock_db)

    resp = c.patch(
        f"/api/admin/feeds/{uuid4()}/archive",
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()


def test_unarchive_feed_not_found_returns_404(client):
    c, mock_db = client
    chain = _mock_feed_lookup_miss(mock_db)

    resp = c.patch(
        f"/api/admin/feeds/{uuid4()}/unarchive",
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()


def test_refresh_feed_not_found_returns_404(client):
    c, mock_db = client
    chain = _mock_feed_lookup_miss(mock_db)

    resp = c.post(
        f"/api/admin/feeds/{uuid4()}/refresh",
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()
