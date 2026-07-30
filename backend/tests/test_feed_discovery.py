from __future__ import annotations
import asyncio
from unittest.mock import patch

import httpx
import pytest

from services.feed_discovery import (
    FALLBACK_PATHS,
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
        # The redirect is rejected inside fetch_with_cap; discover_feeds treats
        # that the same as any other fetch failure and returns no candidates,
        # rather than propagating the internal target anywhere.
        assert await discover_feeds("https://start.example.com/") == []


@pytest.mark.asyncio
async def test_discover_feeds_rejects_private_alternate_link():
    """A discovered candidate must be validated before its first fetch, not
    only on redirects. /api/discover is public and unauthenticated, so a
    safe public page advertising <link rel="alternate" href="http://169.254.
    169.254/..."> would otherwise make the server request that internal
    address — the guard only ever inspects the URL it is about to fetch, and
    nothing had inspected this one.
    """
    html = (
        '<html><head><link rel="alternate" type="application/rss+xml" '
        'href="http://169.254.169.254/latest/meta-data/"/></head></html>'
    )
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    def fake_is_safe_host(host: str) -> bool:
        return host != "169.254.169.254"

    with (
        patch("services.feed_discovery._is_safe_host", side_effect=fake_is_safe_host),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        candidates = await discover_feeds("https://start.example.com/")

    assert "169.254.169.254" not in requested_hosts
    assert [c.feed_url for c in candidates] == []


# ── the allow_url crawl-policy hook (used by the autonomous discovery loop) ───


@pytest.mark.asyncio
async def test_allow_url_blocking_initial_url_makes_no_request():
    """The gate runs before the request, not after — a denylisted host must cost
    zero outbound traffic."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    async def deny_all(url: str) -> bool:
        return False

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        assert await discover_feeds("https://example.com/", allow_url=deny_all) == []

    assert requested == []


@pytest.mark.asyncio
async def test_allow_url_blocks_one_fallback_path_but_not_the_others():
    """A per-URL policy (robots.txt Disallow: /feed) must skip only the paths it
    covers, leaving the rest of the fallback sweep intact."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/rss.xml":
            return httpx.Response(
                200,
                text='<rss version="2.0"><channel><title>Yes</title></channel></rss>',
                headers={"content-type": "application/rss+xml"},
            )
        if request.url.path == "/":
            # An ordinary HTML page with no <link rel=alternate>, so discovery
            # falls through to the well-known-paths sweep.
            return httpx.Response(
                200,
                text="<html><head></head></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(404)

    async def deny_feed_path(url: str) -> bool:
        return httpx.URL(url).path != "/feed"

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        candidates = await discover_feeds(
            "https://example.com/", allow_url=deny_feed_path
        )

    assert "/feed" not in requested
    assert "/rss.xml" in requested
    assert [c.feed_url for c in candidates] == ["https://example.com/rss.xml"]


@pytest.mark.asyncio
async def test_allow_url_is_applied_to_redirect_hops():
    """The denylist is checked against the host we harvested, but a 3xx can hand
    off anywhere. Without re-evaluating the policy per hop, blog.example.com
    could redirect straight to a denylisted host — or any shortener could."""
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "start.example.com":
            return httpx.Response(302, headers={"location": "https://denied.example.com/"})
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    async def deny_one_host(url: str) -> bool:
        return httpx.URL(url).host != "denied.example.com"

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        assert (
            await discover_feeds("https://start.example.com/", allow_url=deny_one_host)
            == []
        )

    assert "denied.example.com" not in requested_hosts


@pytest.mark.asyncio
async def test_delay_seconds_paces_every_request_including_the_first():
    """The autonomous caller has just fetched this host's robots.txt, so the
    advertised per-host interval has to cover the robots-to-homepage transition
    too — otherwise those two go out back to back."""
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Not a feed and no <link rel=alternate>, so the fallback sweep runs and
        # there is more than one request to space out.
        return httpx.Response(
            200, text="<html><head></head></html>", headers={"content-type": "text/html"}
        )

    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *args, **kwargs):
        sleeps.append(seconds)
        await real_sleep(0)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
        patch("services.feed_discovery.asyncio.sleep", new=fake_sleep),
    ):
        await discover_feeds("https://example.com/", delay_seconds=1.5)

    # One before the initial fetch, then one per fallback path.
    assert sleeps == [1.5] * (len(FALLBACK_PATHS) + 1)


@pytest.mark.asyncio
async def test_no_delay_and_no_gate_by_default():
    """The defaults must reproduce the pre-hook behaviour byte for byte — this is
    the regression net for the public POST /api/discover endpoint."""
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<html><head></head></html>", headers={"content-type": "text/html"}
        )

    async def fake_sleep(seconds, *args, **kwargs):
        sleeps.append(seconds)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
        patch("services.feed_discovery.asyncio.sleep", new=fake_sleep),
    ):
        await discover_feeds("https://example.com/")

    assert sleeps == []


@pytest.mark.asyncio
async def test_a_transient_fallback_failure_is_reported_not_swallowed():
    """The homepage is fine but the site's only feed lives at a well-known path
    that is briefly 5xxing. Returning [] would have the caller write the site off
    permanently, losing a real feed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200, text="<html><head></head></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(503)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        with pytest.raises(DiscoveryError):
            await discover_feeds("https://example.com/", raise_on_fetch_error=True)


@pytest.mark.asyncio
async def test_a_definitive_404_sweep_is_not_a_transient_failure():
    """The ordinary case: every well-known path 404s because the site simply has
    no feed. That must stay terminal, or nothing would ever be terminal."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200, text="<html><head></head></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(404)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        assert await discover_feeds(
            "https://example.com/", raise_on_fetch_error=True
        ) == []


@pytest.mark.asyncio
async def test_a_transient_failure_alongside_a_real_feed_still_returns_it():
    """Found something, so the target isn't lost — no reason to raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200, text="<html><head></head></html>",
                headers={"content-type": "text/html"},
            )
        if request.url.path == "/rss.xml":
            return httpx.Response(
                200,
                text='<rss version="2.0"><channel><title>Yes</title></channel></rss>',
                headers={"content-type": "application/rss+xml"},
            )
        return httpx.Response(503)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        found = await discover_feeds(
            "https://example.com/", raise_on_fetch_error=True
        )
    assert [c.feed_url for c in found] == ["https://example.com/rss.xml"]


@pytest.mark.asyncio
async def test_transient_fallback_failures_are_still_swallowed_by_default():
    """Regression net for the public POST /api/discover, which must keep
    returning an empty list rather than raising at a person."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200, text="<html><head></head></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(503)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        assert await discover_feeds("https://example.com/") == []
