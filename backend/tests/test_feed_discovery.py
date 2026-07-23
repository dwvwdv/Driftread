from __future__ import annotations
from unittest.mock import patch

import httpx
import pytest

from services.feed_discovery import (
    DiscoveryError,
    _extract_feed_links,
    discover_feeds,
    validate_fetch_url,
)


def test_extract_feed_links_finds_alternate():
    html = """<html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml" title="Main"/>
      <link rel="alternate" type="application/atom+xml" href="https://other.example.com/atom"/>
      <link rel="stylesheet" href="/style.css"/>
    </head></html>"""
    out = _extract_feed_links(html, "https://example.com/blog")
    urls = [c.feed_url for c in out]
    assert "https://example.com/feed.xml" in urls
    assert "https://other.example.com/atom" in urls
    assert len(out) == 2


def test_extract_feed_links_empty_when_none():
    html = "<html><head></head></html>"
    assert _extract_feed_links(html, "https://example.com") == []


def test_normalize_url_adds_scheme():
    with patch("services.feed_discovery._is_safe_host", return_value=True):
        assert validate_fetch_url("example.com") == "https://example.com"


def test_normalize_url_rejects_loopback():
    with pytest.raises(DiscoveryError):
        validate_fetch_url("http://localhost")


def test_normalize_url_rejects_private():
    with patch(
        "services.feed_discovery._is_safe_host", return_value=False
    ):
        with pytest.raises(DiscoveryError):
            validate_fetch_url("https://10.0.0.1")


def test_normalize_url_rejects_ftp():
    with pytest.raises(DiscoveryError):
        validate_fetch_url("ftp://example.com")


def test_normalize_url_rejects_metadata_ip():
    # 169.254.169.254 is the cloud-metadata link-local address — must be
    # blocked the same way loopback/private ranges are (SSRF guard).
    with pytest.raises(DiscoveryError):
        validate_fetch_url("http://169.254.169.254/latest/meta-data/")


def test_normalize_url_accepts_mixed_case_scheme():
    # URI schemes are case-insensitive (RFC 3986); "HTTP://" must not fall
    # through to the https:// prepend, which would corrupt the URL into one
    # whose "hostname" is the literal string "http".
    with patch("services.feed_discovery._is_safe_host", return_value=True):
        assert validate_fetch_url("HTTP://Example.com/feed") == "HTTP://Example.com/feed"


def test_is_safe_host_rejects_oversized_label_without_crashing():
    # socket.getaddrinfo raises UnicodeError (not gaierror) for a host that
    # fails idna encoding, e.g. a DNS label over 63 bytes — must be treated
    # as unsafe, not propagate as an unhandled exception.
    with pytest.raises(DiscoveryError):
        validate_fetch_url("https://" + "a" * 64 + ".com")


_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(handler):
    def factory(*args, **kwargs):
        kwargs.pop("follow_redirects", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return factory


@pytest.mark.asyncio
async def test_discover_feeds_rejects_redirect_to_private_ip():
    """A redirect must be re-validated, not just the initial URL — otherwise
    a safe-looking host can hand off to an internal target via a 3xx."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    def fake_is_safe_host(host: str) -> bool:
        return host != "169.254.169.254"

    with (
        patch("services.feed_discovery._is_safe_host", side_effect=fake_is_safe_host),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        # The redirect is rejected inside _fetch; discover_feeds treats that
        # the same as any other fetch failure and returns no candidates,
        # rather than propagating the internal target anywhere.
        assert await discover_feeds("https://start.example.com/") == []
