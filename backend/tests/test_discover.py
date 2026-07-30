from __future__ import annotations

from unittest.mock import MagicMock, patch

from rate_limit import DEFAULT_MAX_REQUESTS
from services.feed_discovery import DiscoveryCandidate


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


def test_discover_rate_limit_keys_on_forwarded_client_not_proxy(client):
    """Requests arrive from the frontend nginx container, not the real
    client (see main.py's ProxyHeadersMiddleware comment) — the limiter must
    key on the forwarded client IP, or one user behind the proxy can exhaust
    the shared quota and lock out everyone else."""
    c, mock_db = client
    body = {"feed_url": "http://127.0.0.1/x"}

    for _ in range(DEFAULT_MAX_REQUESTS):
        resp = c.post(
            "/api/discover/import", json=body, headers={"x-forwarded-for": "203.0.113.1"}
        )
        assert resp.status_code == 400
    # 203.0.113.1's quota is now exhausted...
    assert c.post(
        "/api/discover/import", json=body, headers={"x-forwarded-for": "203.0.113.1"}
    ).status_code == 429
    # ...but a different forwarded client must still have its own.
    resp = c.post(
        "/api/discover/import", json=body, headers={"x-forwarded-for": "203.0.113.2"}
    )
    assert resp.status_code == 400
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


def test_discover_checks_existing_feeds_without_bulk_in_filter(client):
    """discover_feeds() returns candidate feed_url strings lifted straight out
    of remote HTML the caller doesn't control (see _extract_feed_links()) —
    routers/discover.py must never splice them into a single .in_("url", ...)
    PostgREST filter (docs/SECURITY.md #24's documented, now-fixed exposure).
    Each candidate is looked up on its own via .eq(...).maybe_single()
    instead, so a crafted feed_url containing filter metacharacters (`,`,
    `(`, `)`, `"`) is just a literal comparison value, never filter syntax."""
    c, mock_db = client
    known_url = 'https://known.example.com/feed.xml?evil=","or(id.neq.0'
    new_url = "https://new.example.com/feed.xml"
    candidates = [
        DiscoveryCandidate(feed_url=known_url, title="Known", website_url="https://known.example.com"),
        DiscoveryCandidate(feed_url=new_url, title="New", website_url="https://new.example.com"),
    ]

    async def fake_discover_feeds(url):
        return candidates

    def fake_eq(column, value):
        assert column == "url"
        chain = MagicMock()
        if value == known_url:
            chain.maybe_single.return_value.execute.return_value = MagicMock(
                data={"id": "11111111-1111-1111-1111-111111111111", "url": value}
            )
        else:
            chain.maybe_single.return_value.execute.return_value = MagicMock(data=None)
        return chain

    mock_db.table.return_value.select.return_value.eq.side_effect = fake_eq

    with patch("routers.discover.discover_feeds", new=fake_discover_feeds):
        resp = c.post("/api/discover", json={"url": "https://start.example.com/"})

    assert resp.status_code == 200
    by_url = {row["feed_url"]: row for row in resp.json()["candidates"]}
    assert by_url[known_url]["already_exists"] is True
    assert by_url[known_url]["existing_feed_id"] == "11111111-1111-1111-1111-111111111111"
    assert by_url[new_url]["already_exists"] is False
    mock_db.table.return_value.select.return_value.in_.assert_not_called()
