from __future__ import annotations
import asyncio
from unittest.mock import patch

import httpx
import pytest

from services.feed_discovery import (
    FALLBACK_PATHS,
    MAX_FEED_LINK_CANDIDATES,
    MAX_PINNED_CONNECT_ATTEMPTS,
    DiscoveryError,
    PinnedTransport,
    _extract_feed_links,
    _pick_pinned_ips,
    _resolve_pinned_ips,
    discover_feeds,
    ssrf_safe_client,
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


def test_extract_feed_links_caps_candidates():
    """An attacker-controlled page seen by the fully public, unauthenticated
    POST /api/discover could otherwise pad <head> with unlimited <link
    rel="alternate"> tags, each costing an outbound validation fetch (and,
    since #25, a DB lookup in routers/discover.py) per candidate."""
    links = "".join(
        f'<link rel="alternate" type="application/rss+xml" href="/feed{i}.xml"/>'
        for i in range(MAX_FEED_LINK_CANDIDATES * 4)
    )
    html = f"<html><head>{links}</head></html>"
    out = _extract_feed_links(html, "https://example.com")
    assert len(out) == MAX_FEED_LINK_CANDIDATES


def test_extract_feed_links_finds_feed_past_many_unrelated_link_tags():
    """The cap must apply to qualifying candidates, not to how many raw <link>
    tags get scanned — a real page can easily list more than
    MAX_FEED_LINK_CANDIDATES stylesheet/icon/preload <link> tags before its
    actual feed declaration, and a scan-count cap would silently never reach
    it."""
    noise = "".join(
        f'<link rel="stylesheet" href="/style{i}.css"/>' for i in range(MAX_FEED_LINK_CANDIDATES * 3)
    )
    html = (
        f"<html><head>{noise}"
        '<link rel="alternate" type="application/rss+xml" href="/real-feed.xml"/>'
        "</head></html>"
    )
    out = _extract_feed_links(html, "https://example.com")
    assert [c.feed_url for c in out] == ["https://example.com/real-feed.xml"]


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


# ── pin-and-connect (DNS-rebinding gap, SECURITY.md #22/#24/#25) ──────────────
#
# These test _resolve_pinned_ips and PinnedTransport directly rather than via
# discover_feeds()'s MockTransport-based tests above: MockTransport replaces
# the whole transport, so it never reaches PinnedTransport.handle_async_request
# and can't observe what host/headers/extensions a real connection would see.


def _fake_addrinfo(*ips: str):
    return [(None, None, None, "", (ip, 0)) for ip in ips]


def test_resolve_pinned_ips_returns_the_resolved_addresses():
    with patch(
        "services.feed_discovery.socket.getaddrinfo",
        return_value=_fake_addrinfo("93.184.216.34"),
    ):
        assert _resolve_pinned_ips("example.com") == ["93.184.216.34"]


def test_resolve_pinned_ips_returns_every_address_in_order():
    """Dual-stack hosts keep every validated address, not just the first —
    PinnedTransport falls back across them the same way plain httpx/httpcore
    would across getaddrinfo()'s own results."""
    with patch(
        "services.feed_discovery.socket.getaddrinfo",
        return_value=_fake_addrinfo("2606:4700:4700::1111", "93.184.216.34"),
    ):
        assert _resolve_pinned_ips("dualstack.example.com") == ["2606:4700:4700::1111", "93.184.216.34"]


def test_resolve_pinned_ips_rejects_private_address():
    with patch(
        "services.feed_discovery.socket.getaddrinfo",
        return_value=_fake_addrinfo("169.254.169.254"),
    ):
        with pytest.raises(DiscoveryError):
            _resolve_pinned_ips("evil.example.com")


def test_resolve_pinned_ips_rejects_host_if_any_of_several_addresses_unsafe():
    """Same conservative stance as _is_safe_host: a host that resolves to
    multiple addresses is rejected outright if any one of them is private,
    not just when the address we'd happen to try first is."""
    with patch(
        "services.feed_discovery.socket.getaddrinfo",
        return_value=_fake_addrinfo("93.184.216.34", "169.254.169.254"),
    ):
        with pytest.raises(DiscoveryError):
            _resolve_pinned_ips("multi-record.example.com")


def test_resolve_pinned_ips_propagates_dns_failure():
    import socket as socket_module

    with patch(
        "services.feed_discovery.socket.getaddrinfo",
        side_effect=socket_module.gaierror("no such host"),
    ):
        with pytest.raises(DiscoveryError):
            _resolve_pinned_ips("nonexistent.invalid")


def test_pick_pinned_ips_interleaves_by_family():
    """A plain ips[:limit] would fail this exact case: a resolver that lists
    two AAAA records before its one A record would have both attempt slots
    taken by IPv6, silently losing the IPv4 fallback again in an environment
    where IPv6 doesn't actually work."""
    ips = ["2606:4700:4700::1111", "2001:4860:4860::8888", "93.184.216.34"]
    assert _pick_pinned_ips(ips, 2) == ["2606:4700:4700::1111", "93.184.216.34"]


def test_pick_pinned_ips_single_family_takes_first_n_in_order():
    ips = ["93.184.216.34", "93.184.216.35", "93.184.216.36"]
    assert _pick_pinned_ips(ips, 2) == ["93.184.216.34", "93.184.216.35"]


def test_pick_pinned_ips_returns_fewer_than_limit_if_thats_all_there_is():
    assert _pick_pinned_ips(["93.184.216.34"], 2) == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_pinned_transport_connects_to_resolved_ip_keeps_host_and_sni():
    """The mechanism that closes the rebind gap: the request handed to the
    real transport must target the resolved IP, while Host and SNI stay on
    the original hostname so the fetch is transparent to the server."""
    captured: dict = {}

    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["host_header"] = request.headers.get("host")
        captured["sni_hostname"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, text="ok")

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://example.com/feed.xml")

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo("93.184.216.34"),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        response = await transport.handle_async_request(request)

    assert captured["host"] == "93.184.216.34"
    assert captured["host_header"] == "example.com"
    assert captured["sni_hostname"] == "example.com"
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_pinned_transport_restores_original_url_before_returning():
    """httpx.Client._send_single_request sets response.request = request and
    only *then* extracts Set-Cookie headers from the response, using
    response.request.url's host as the cookie domain. Since request is the
    same object PinnedTransport mutated, leaving request.url pointing at the
    pinned IP after returning would file any Set-Cookie under the IP instead
    of the real hostname — and the next manual-redirect hop in
    fetch_with_cap_response() (same client, built against the real hostname)
    would then fail to find it."""

    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://example.com/feed.xml")

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo("93.184.216.34"),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        await transport.handle_async_request(request)

    assert request.url.host == "example.com"


@pytest.mark.asyncio
async def test_pinned_transport_restores_original_url_after_every_address_fails():
    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unreachable", request=request)

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://unreachable.example.com/feed.xml")

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo("93.184.216.34"),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(request)

    assert request.url.host == "unreachable.example.com"


@pytest.mark.asyncio
async def test_pinned_transport_falls_back_to_next_address_on_connect_error():
    """A dual-stack host whose first resolved address is unreachable (e.g. an
    AAAA record in an environment with broken IPv6) must still succeed via a
    later validated address, matching the fallback plain httpx/httpcore
    already provides across getaddrinfo()'s own results."""
    attempted_hosts: list[str] = []

    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        attempted_hosts.append(request.url.host)
        if request.url.host == "2606:4700:4700::1111":
            raise httpx.ConnectError("network unreachable", request=request)
        return httpx.Response(200, text="ok")

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://dualstack.example.com/feed.xml")

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo("2606:4700:4700::1111", "93.184.216.34"),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        response = await transport.handle_async_request(request)

    assert attempted_hosts == ["2606:4700:4700::1111", "93.184.216.34"]
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_pinned_transport_falls_back_on_connect_timeout_too():
    """httpx.ConnectTimeout is a sibling of ConnectError, not a subclass of it
    — and a connect-stage timeout (packets silently dropped) is the more
    common real-world shape of "this address doesn't actually work" than an
    immediately-refused connection, so it must trigger fallback too."""
    attempted_hosts: list[str] = []

    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        attempted_hosts.append(request.url.host)
        if request.url.host == "2606:4700:4700::1111":
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, text="ok")

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://dualstack.example.com/feed.xml")

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo("2606:4700:4700::1111", "93.184.216.34"),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        response = await transport.handle_async_request(request)

    assert attempted_hosts == ["2606:4700:4700::1111", "93.184.216.34"]
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_pinned_transport_raises_after_every_address_fails():
    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unreachable", request=request)

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://unreachable.example.com/feed.xml")

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo("93.184.216.34", "93.184.216.35"),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(request)


@pytest.mark.asyncio
async def test_pinned_transport_caps_address_attempts():
    """Each attempt gets its own full connect timeout, so an unbounded
    attempt count would let a hostname resolving to many public-but-
    unreachable addresses (attacker-controlled DNS) turn one request into
    address_count x timeout of hung connection time. Only the first
    MAX_PINNED_CONNECT_ATTEMPTS addresses may ever be tried."""
    attempted_hosts: list[str] = []

    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        attempted_hosts.append(request.url.host)
        raise httpx.ConnectError("network unreachable", request=request)

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://many-addresses.example.com/feed.xml")
    many_ips = [f"93.184.216.{i}" for i in range(10)]

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo(*many_ips),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(request)

    assert attempted_hosts == many_ips[:MAX_PINNED_CONNECT_ATTEMPTS]


@pytest.mark.asyncio
async def test_pinned_transport_falls_back_to_ipv4_past_multiple_aaaa_records():
    """End-to-end version of test_pick_pinned_ips_interleaves_by_family: a
    resolver listing two AAAA records before the one A record must still
    reach the IPv4 fallback within the attempt budget, not exhaust it on
    IPv6 alone."""
    attempted_hosts: list[str] = []

    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        attempted_hosts.append(request.url.host)
        if request.url.host != "93.184.216.34":
            raise httpx.ConnectError("network unreachable", request=request)
        return httpx.Response(200, text="ok")

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://dualstack.example.com/feed.xml")

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo(
                "2606:4700:4700::1111", "2001:4860:4860::8888", "93.184.216.34"
            ),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        response = await transport.handle_async_request(request)

    assert attempted_hosts == ["2606:4700:4700::1111", "93.184.216.34"]
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_pinned_transport_rejects_dns_rebind_to_private_ip():
    """This is the actual connect-time choke point: even if an earlier
    validate_fetch_url() call saw a public address for this hostname, the
    transport's own resolution is what decides the real connect target, and
    must independently reject a private one — no unpinned second lookup is
    left for an attacker's short-TTL DNS answer to exploit."""

    async def fake_inner(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach the real transport for a private IP")

    transport = PinnedTransport()
    request = httpx.Request("GET", "https://rebind.example.com/feed.xml")

    with (
        patch(
            "services.feed_discovery.socket.getaddrinfo",
            return_value=_fake_addrinfo("169.254.169.254"),
        ),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", fake_inner),
    ):
        with pytest.raises(DiscoveryError):
            await transport.handle_async_request(request)


@pytest.mark.asyncio
async def test_ssrf_safe_client_defaults_to_pinned_transport():
    async with ssrf_safe_client() as client:
        assert isinstance(client._transport, PinnedTransport)


@pytest.mark.asyncio
async def test_ssrf_safe_client_disables_keepalive_by_default():
    """PinnedTransport's rewritten request.url makes httpcore's connection-
    pool key the resolved IP rather than the original hostname — two
    different hostnames sharing an IP (shared hosting/CDN) would otherwise
    collide in the pool, letting a request for one reuse a connection whose
    TLS session was established (and SNI'd) for the other. Disabling
    keep-alive means no connection ever survives past the single request it
    was opened for, so nothing is left in the pool to collide with."""
    async with ssrf_safe_client() as client:
        assert client._transport._pool._max_keepalive_connections == 0


@pytest.mark.asyncio
async def test_ssrf_safe_client_respects_explicit_transport():
    """A caller-supplied transport (as every test's _mock_client_factory
    supplies) must win — ssrf_safe_client only sets a default, never forces
    PinnedTransport onto an already-mocked client."""
    sentinel = httpx.MockTransport(lambda request: httpx.Response(200))
    async with ssrf_safe_client(transport=sentinel) as client:
        assert client._transport is sentinel
