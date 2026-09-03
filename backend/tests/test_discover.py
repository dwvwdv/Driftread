from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import jwt

from rate_limit import DEFAULT_MAX_REQUESTS
from services.feed_discovery import DiscoveryCandidate

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


def test_discover_import_requires_authentication(client):
    """Anonymous writes into the global feeds catalog are the abuse vector
    this closes (TODO.md "Auth 與安全"): a signed-out caller must be
    rejected before any fetch or database write, not just rate limited."""
    c, mock_db = client
    resp = c.post("/api/discover/import", json={"feed_url": "https://example.com/feed.xml"})
    assert resp.status_code == 401
    mock_db.table.assert_not_called()


def test_discover_import_succeeds_for_authenticated_user(client):
    c, mock_db = client
    feed_row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Example",
        "url": "https://example.com/feed.xml",
        "description": None,
        "website_url": None,
        "language": None,
        "category": None,
        "tags": [],
        "article_count": 0,
        "last_fetched_at": None,
        "archived_at": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(
        data=[feed_row]
    )
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = (
        MagicMock(data=[])
    )

    async def fake_validate(url):
        return url

    async def fake_fetch(url):
        return MagicMock(
            title="Example",
            description=None,
            website_url=None,
            language=None,
            articles=[],
        )

    with (
        patch("routers.discover.validate_fetch_url", new=fake_validate),
        patch("routers.discover.fetch_and_parse", new=fake_fetch),
        patch("routers.discover.upsert_articles", return_value=0),
    ):
        resp = c.post(
            "/api/discover/import",
            json={"feed_url": "https://example.com/feed.xml"},
            headers={"Authorization": f"Bearer {_token()}"},
        )

    assert resp.status_code == 200
    assert resp.json()["id"] == feed_row["id"]
    # Auto-subscribes the importer, unconditionally now that the caller is
    # always a real signed-in user.
    user_feeds_upsert = mock_db.table.return_value.upsert.call_args_list
    assert {"user_id": "user-abc", "feed_id": feed_row["id"]} in [
        call.args[0] for call in user_feeds_upsert
    ]


def test_discover_import_rejects_private_url(client):
    c, mock_db = client
    resp = c.post(
        "/api/discover/import",
        json={"feed_url": "http://127.0.0.1/secret"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 400
    # Must fail before ever touching the database.
    mock_db.table.assert_not_called()


def test_discover_import_rejects_metadata_url(client):
    c, mock_db = client
    resp = c.post(
        "/api/discover/import",
        json={"feed_url": "http://169.254.169.254/latest/meta-data/"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 400
    mock_db.table.assert_not_called()


def test_discover_import_rate_limited_after_threshold(client):
    c, mock_db = client
    body = {"feed_url": "http://127.0.0.1/secret"}
    headers = {"Authorization": f"Bearer {_token()}"}
    # Each of these is individually rejected as a private URL (400) — the
    # point is that after DEFAULT_MAX_REQUESTS of them, the *next* one never
    # reaches route logic at all and gets 429 instead.
    for _ in range(DEFAULT_MAX_REQUESTS):
        resp = c.post("/api/discover/import", json=body, headers=headers)
        assert resp.status_code == 400
    resp = c.post("/api/discover/import", json=body, headers=headers)
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
    auth = f"Bearer {_token()}"

    for _ in range(DEFAULT_MAX_REQUESTS):
        resp = c.post(
            "/api/discover/import",
            json=body,
            headers={"x-forwarded-for": "203.0.113.1", "Authorization": auth},
        )
        assert resp.status_code == 400
    # 203.0.113.1's quota is now exhausted...
    assert c.post(
        "/api/discover/import",
        json=body,
        headers={"x-forwarded-for": "203.0.113.1", "Authorization": auth},
    ).status_code == 429
    # ...but a different forwarded client must still have its own.
    resp = c.post(
        "/api/discover/import",
        json=body,
        headers={"x-forwarded-for": "203.0.113.2", "Authorization": auth},
    )
    assert resp.status_code == 400
    mock_db.table.assert_not_called()


def test_discover_rate_limit_is_independent_per_endpoint(client):
    c, mock_db = client
    headers = {"Authorization": f"Bearer {_token()}"}
    # Exhaust /discover/import's quota...
    for _ in range(DEFAULT_MAX_REQUESTS):
        c.post("/api/discover/import", json={"feed_url": "http://127.0.0.1/x"}, headers=headers)
    assert c.post(
        "/api/discover/import", json={"feed_url": "http://127.0.0.1/x"}, headers=headers
    ).status_code == 429
    # .../discover must still have its own, untouched quota — and needs no
    # auth, since it never writes.
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
