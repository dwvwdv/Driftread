from __future__ import annotations

from rate_limit import DEFAULT_MAX_REQUESTS


def test_discover_import_rejects_private_url(client):
    c, mock_db = client
    resp = c.post("/api/discover/import", json={"feed_url": "http://127.0.0.1/secret"})
    assert resp.status_code == 400
    # Must fail before ever touching the database.
    mock_db.table.assert_not_called()


def test_discover_import_rejects_metadata_url(client):
    c, mock_db = client
    resp = c.post(
        "/api/discover/import",
        json={"feed_url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert resp.status_code == 400
    mock_db.table.assert_not_called()


def test_discover_import_rate_limited_after_threshold(client):
    c, mock_db = client
    body = {"feed_url": "http://127.0.0.1/secret"}
    # Each of these is individually rejected as a private URL (400) — the
    # point is that after DEFAULT_MAX_REQUESTS of them, the *next* one never
    # reaches route logic at all and gets 429 instead.
    for _ in range(DEFAULT_MAX_REQUESTS):
        resp = c.post("/api/discover/import", json=body)
        assert resp.status_code == 400
    resp = c.post("/api/discover/import", json=body)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    mock_db.table.assert_not_called()


def test_discover_rate_limit_is_independent_per_endpoint(client):
    c, mock_db = client
    # Exhaust /discover/import's quota...
    for _ in range(DEFAULT_MAX_REQUESTS):
        c.post("/api/discover/import", json={"feed_url": "http://127.0.0.1/x"})
    assert c.post(
        "/api/discover/import", json={"feed_url": "http://127.0.0.1/x"}
    ).status_code == 429
    # .../discover must still have its own, untouched quota.
    resp = c.post("/api/discover", json={"url": "http://127.0.0.1/x"})
    assert resp.status_code == 400
    mock_db.table.assert_not_called()
