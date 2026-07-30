from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from services import robots

UA = "Driftread/1.0 (+https://driftread.example/about)"

_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(handler):
    """Same shape as tests/test_feed_discovery.py's: drop follow_redirects so the
    manual redirect handling in fetch_with_cap_response is still exercised."""

    def factory(*args, **kwargs):
        kwargs.pop("follow_redirects", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return factory


def _serving(body: str, status: int = 200, counter: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(str(request.url))
        if status != 200:
            return httpx.Response(status, text="")
        return httpx.Response(status, text=body, headers={"content-type": "text/plain"})

    return handler


@pytest.fixture(autouse=True)
def _clear_robots_cache():
    """The cache is process-global; without this a body from one test answers
    another test's request."""
    robots.clear_cache()
    yield
    robots.clear_cache()


def _patched(handler):
    return (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    )


async def _check(handler, url: str):
    p1, p2 = _patched(handler)
    with p1, p2:
        return await robots.check(url, UA)


# ── directives ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disallow_blocks_matching_path_only():
    handler = _serving("User-agent: *\nDisallow: /private/\n")
    assert (await _check(handler, "https://example.com/private/x")).allowed is False
    robots.clear_cache()
    assert (await _check(handler, "https://example.com/")).allowed is True


@pytest.mark.asyncio
async def test_ua_specific_group_beats_wildcard():
    """Our UA has a slash and a parenthetical; robotparser matches on the text
    before the first "/", so `User-agent: driftread` must still win."""
    handler = _serving(
        "User-agent: driftread\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    )
    assert (await _check(handler, "https://example.com/anything")).allowed is False


@pytest.mark.asyncio
async def test_contact_url_in_user_agent_does_not_break_matching():
    """The reverse of the above: a group we don't match must not apply to us."""
    handler = _serving("User-agent: someotherbot\nDisallow: /\n")
    assert (await _check(handler, "https://example.com/anything")).allowed is True


@pytest.mark.asyncio
async def test_crawl_delay_falls_back_to_wildcard_group():
    """crawl_delay() returns None when a UA-specific group matched but declares
    no Crawl-delay — the wildcard value does not fall through on its own."""
    handler = _serving(
        "User-agent: *\nCrawl-delay: 7\n\nUser-agent: driftread\nDisallow: /x\n"
    )
    decision = await _check(handler, "https://example.com/ok")
    assert decision.allowed is True
    assert decision.crawl_delay == 7.0


@pytest.mark.asyncio
async def test_crawl_delay_is_clamped():
    """A hostile Crawl-delay must not pin a probe slot for a day."""
    handler = _serving("User-agent: *\nCrawl-delay: 86400\n")
    decision = await _check(handler, "https://example.com/")
    assert decision.crawl_delay == robots.MAX_CRAWL_DELAY_SECONDS


@pytest.mark.asyncio
async def test_no_crawl_delay_declared_is_none():
    handler = _serving("User-agent: *\nDisallow: /nope\n")
    assert (await _check(handler, "https://example.com/")).crawl_delay is None


@pytest.mark.asyncio
async def test_empty_robots_allows_everything():
    handler = _serving("")
    decision = await _check(handler, "https://example.com/anything")
    assert (decision.allowed, decision.reachable) == (True, True)


# ── status-code semantics (RFC 9309 §2.3.1) ──────────────────────────────────

@pytest.mark.asyncio
async def test_404_allows_everything():
    """The common case: most sites have no robots.txt at all."""
    decision = await _check(_serving("", status=404), "https://example.com/x")
    assert (decision.allowed, decision.reachable) == (True, True)


@pytest.mark.asyncio
async def test_403_allows_everything():
    decision = await _check(_serving("", status=403), "https://example.com/x")
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_500_disallows_everything_but_host_is_reachable():
    """A server error means complete disallow — but the host answered, which is
    what discovery_probe needs to know to tell 'no feed' from 'down'."""
    decision = await _check(_serving("", status=500), "https://example.com/x")
    assert (decision.allowed, decision.reachable) == (False, True)


@pytest.mark.asyncio
async def test_transport_error_is_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    decision = await _check(handler, "https://example.com/x")
    assert (decision.allowed, decision.reachable) == (False, False)


@pytest.mark.asyncio
async def test_oversized_robots_is_rejected():
    """The byte cap surfaces as a DiscoveryError, which is a policy answer rather
    than evidence about the host — so not allowed, not reachable."""
    body = "User-agent: *\nAllow: /\n" + ("#" * (robots.MAX_ROBOTS_BYTES + 10))
    decision = await _check(_serving(body), "https://example.com/x")
    assert (decision.allowed, decision.reachable) == (False, False)


@pytest.mark.asyncio
async def test_ssrf_rejection_is_unreachable_not_allowed():
    """robots.txt goes through the same SSRF gate as every other fetch."""
    handler = _serving("User-agent: *\nAllow: /\n")
    with (
        patch("services.feed_discovery._is_safe_host", return_value=False),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        decision = await robots.check("https://10.0.0.5/x", UA)
    assert (decision.allowed, decision.reachable) == (False, False)


@pytest.mark.asyncio
async def test_read_is_never_called():
    """RobotFileParser.read() does its own unguarded urlopen(), bypassing the
    SSRF gate, the redirect handling and the byte cap. It must never run."""
    handler = _serving("User-agent: *\nAllow: /\n")
    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
        patch.object(
            robots.RobotFileParser, "read", side_effect=AssertionError("read() called")
        ),
    ):
        assert (await robots.check("https://example.com/", UA)).allowed is True


# ── caching ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_second_check_on_same_origin_makes_no_request():
    requests: list[str] = []
    handler = _serving("User-agent: *\nDisallow: /no\n", counter=requests)
    p1, p2 = _patched(handler)
    with p1, p2:
        await robots.check("https://example.com/a", UA)
        assert len(requests) == 1
        # A different path on the same origin is served from the same parse.
        await robots.check("https://example.com/b/c", UA)
        await robots.check("https://example.com/no", UA)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_distinct_origins_are_fetched_separately():
    requests: list[str] = []
    handler = _serving("User-agent: *\nAllow: /\n", counter=requests)
    p1, p2 = _patched(handler)
    with p1, p2:
        await robots.check("https://a.example.com/", UA)
        await robots.check("https://b.example.com/", UA)
        # Port is part of the origin.
        await robots.check("https://a.example.com:8443/", UA)
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(monkeypatch):
    requests: list[str] = []
    handler = _serving("User-agent: *\nAllow: /\n", counter=requests)
    clock = [1000.0]
    monkeypatch.setattr(robots.time, "monotonic", lambda: clock[0])

    p1, p2 = _patched(handler)
    with p1, p2:
        await robots.check("https://example.com/", UA)
        clock[0] += robots.CACHE_TTL_SECONDS - 1
        await robots.check("https://example.com/", UA)
        assert len(requests) == 1
        clock[0] += 2
        await robots.check("https://example.com/", UA)
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_cache_is_bounded_and_evicts_lru(monkeypatch):
    monkeypatch.setattr(robots, "MAX_CACHED_ORIGINS", 3)
    handler = _serving("User-agent: *\nAllow: /\n")
    p1, p2 = _patched(handler)
    with p1, p2:
        for i in range(3):
            await robots.check(f"https://h{i}.example.com/", UA)
        # Touch the oldest so it is no longer the LRU victim.
        await robots.check("https://h0.example.com/", UA)
        await robots.check("https://h3.example.com/", UA)

    assert len(robots._cache) == 3
    assert "https://h1.example.com" not in robots._cache
    assert "https://h0.example.com" in robots._cache
    assert "https://h3.example.com" in robots._cache


@pytest.mark.asyncio
async def test_is_allowed_is_a_thin_bool_wrapper():
    handler = _serving("User-agent: *\nDisallow: /private/\n")
    p1, p2 = _patched(handler)
    with p1, p2:
        assert await robots.is_allowed("https://example.com/ok", UA) is True
        assert await robots.is_allowed("https://example.com/private/x", UA) is False


@pytest.mark.asyncio
async def test_500_is_marked_transient_but_a_real_disallow_is_not():
    """The distinction discovery_probe routes on: a server error must be
    retryable, an actual Disallow rule must not be."""
    server_error = await _check(_serving("", status=500), "https://example.com/x")
    assert (server_error.allowed, server_error.transient) == (False, True)

    robots.clear_cache()
    disallowed = await _check(
        _serving("User-agent: *\nDisallow: /\n"), "https://example.com/x"
    )
    assert (disallowed.allowed, disallowed.transient) == (False, False)


@pytest.mark.asyncio
async def test_unreachable_host_is_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    decision = await _check(handler, "https://example.com/x")
    assert (decision.allowed, decision.reachable, decision.transient) == (
        False, False, True,
    )


@pytest.mark.asyncio
async def test_an_allowed_decision_is_never_transient():
    decision = await _check(_serving("User-agent: *\nAllow: /\n"), "https://example.com/")
    assert (decision.allowed, decision.transient) == (True, False)
