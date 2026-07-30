"""Auto-discover RSS / Atom feeds from any URL.

Strategy (in order):
1. Fetch HTML, parse <link rel="alternate" type="application/rss+xml|atom+xml">.
2. Try a list of well-known fallback paths (/feed, /rss, /atom.xml, ...).
3. Validate each candidate by attempting to parse it with rss_parser.

SSRF protection is applied: private / loopback / link-local addresses are rejected.
"""
from __future__ import annotations
import asyncio
import ipaddress
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from rss_parser import ParsedFeed, parse_feed


# A crawl-policy predicate: given the URL about to be fetched, may we fetch it?
# Evaluated at the single fetch choke point below, on the initial URL *and* every
# redirect hop. This is where denylists and robots.txt checks belong — the
# autonomous discovery loop installs one (see services/discovery_probe.py), while
# user-triggered discovery passes None and behaves exactly as before.
#
# It is NOT a security boundary against private addresses: validate_fetch_url()
# is, and it always runs first.
AllowUrl = Callable[[str], Awaitable[bool]]

FEED_CONTENT_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml")
FALLBACK_PATHS = ("/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml", "/feed/")
# One cap for every fetch. discover_feeds's first request serves double
# duty — the URL may turn out to be a feed or an HTML page — so the cap
# has to be chosen before the content type is known, and the feed limit
# is the one that has to hold.
MAX_FEED_BYTES = 5 * 1024 * 1024  # 5 MiB
# A real page's declared alternate feed links realistically number in the
# single digits. Without a cap, a page crafted for the fully public,
# unauthenticated POST /api/discover could pad <head> with thousands of
# <link rel="alternate"> tags, each one costing an outbound validation fetch
# in discover_feeds() below and, since #25, a DB lookup in routers/discover.py
# — turning one cheap request into an unbounded pile of outbound/DB work.
# Same technique as link_harvest.py's MAX_ANCHORS_PER_DOC, applied at the
# same choke point (_extract_feed_links) so every downstream consumer of its
# output is bounded by construction, not by remembering to cap it themselves.
MAX_FEED_LINK_CANDIDATES = 50


@dataclass
class DiscoveryCandidate:
    feed_url: str
    title: str | None
    website_url: str | None


@dataclass(frozen=True)
class CappedResponse:
    """A fetch result plus the bits conditional GET needs: the status (to spot
    304) and the validators to replay on the next poll.
    """
    status_code: int
    text: str
    content_type: str
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304


class DiscoveryError(Exception):
    pass


def user_agent() -> str:
    """The User-Agent for every outbound fetch — discovery and feed ingestion
    alike. rss_parser.fetch_and_parse() uses this too, so DISCOVERY_USER_AGENT
    applies to /discover/import, admin import/refresh and OPML import, not
    just candidate discovery.
    """
    return os.getenv("DISCOVERY_USER_AGENT", "Driftread/1.0")


def _is_safe_host(host: str) -> bool:
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        # UnicodeError: a malformed host (e.g. a DNS label > 63 bytes) fails
        # idna encoding before any lookup is attempted — not a gaierror.
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False
    return True


def validate_fetch_url(url: str) -> str:
    """Normalize a user-supplied URL and reject private/loopback/link-local
    hosts. Any code path that fetches an arbitrary URL supplied by a client
    (feed discovery, manual import, OPML import, admin refresh) must call
    this before making the request — otherwise it's an SSRF vector.
    """
    if not url:
        raise DiscoveryError("Empty URL")
    # Case-insensitive per RFC 3986 (schemes aren't case-sensitive) — a
    # mixed-case scheme like "HTTP://" must not fall through to the
    # https:// prepend below, which would corrupt it into a bogus URL
    # whose "hostname" is the literal string "http".
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DiscoveryError(f"Unsupported scheme: {parsed.scheme}")
    if not parsed.netloc:
        raise DiscoveryError("URL missing host")
    if not _is_safe_host(parsed.hostname or ""):
        raise DiscoveryError("Refusing to fetch private/loopback address")
    return url


def _resolve_pinned_ips(host: str) -> list[str]:
    """Resolve `host` and return every IP that passed the same private/
    loopback/etc checks as _is_safe_host — for PinnedTransport to connect to
    directly, trying them in order.

    validate_fetch_url() and the actual TCP connection each used to do their
    own independent socket.getaddrinfo() for the same hostname (the former to
    decide whether to proceed, httpcore internally for the latter). A DNS
    answer with a short TTL or multiple A/AAAA records can differ between the
    two lookups — the classic DNS-rebinding bypass documented in
    docs/SECURITY.md (#22's known limitation, elaborated in #24 point 2): a
    public IP can pass validation while a private one is what's actually
    connected to. Reusing *this* function's single resolution as the connect
    targets (via PinnedTransport below) closes that gap by construction —
    there is no second, independent lookup left to disagree with the first.

    Returns the *whole* validated list, not just one address, so a dual-stack
    host isn't reduced to a single attempt: connecting to plain (unpinned)
    httpx/httpcore already falls back across every getaddrinfo() result in
    order, and only trying address[0] here would silently drop that fallback
    — e.g. an AAAA record picked first in an environment with broken IPv6
    would fail outright instead of falling back to a working A record.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        raise DiscoveryError(f"DNS resolution failed for {host!r}")
    # Reject the whole host if *any* resolved address is unsafe, not just the
    # ones we'd try to connect to — same conservative stance as _is_safe_host
    # (a host that can answer with a private address under some resolution is
    # treated as unsafe outright, not just "unsafe on the record we happened
    # to try first").
    ips: list[str] = []
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise DiscoveryError(f"DNS resolution returned an invalid address for {host!r}")
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise DiscoveryError(f"Refusing to connect {host!r} to private/loopback address")
        if ip_str not in ips:
            ips.append(ip_str)
    if not ips:
        raise DiscoveryError(f"DNS resolution returned no addresses for {host!r}")
    return ips


class PinnedTransport(httpx.AsyncHTTPTransport):
    """The real transport used for every outbound fetch (see ssrf_safe_client
    below) — connects to one of the exact IPs _resolve_pinned_ips() just
    validated, instead of handing httpcore a hostname it would resolve again
    on its own. The Host header and TLS SNI still use the original hostname
    (the former was already set from it when httpx built the request, before
    any transport runs; the latter is set explicitly below), so this is
    transparent to the server — only the connect target is pinned. Every
    request in this project is a bodyless GET (see fetch_with_cap_response),
    so retrying the same Request object across addresses is safe — there is
    no request stream that a failed attempt could have partially consumed.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        original_url = request.url
        ips = _resolve_pinned_ips(original_host)
        last_error: httpx.TransportError | None = None
        for ip in ips:
            request.url = original_url.copy_with(host=ip)
            request.extensions = {**request.extensions, "sni_hostname": original_host}
            try:
                return await super().handle_async_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Only a connect-stage failure warrants trying the next
                # address — anything past that point (read/write timeout,
                # HTTP-level error) isn't an address-reachability problem and
                # retrying it against a different IP wouldn't fix it. Both
                # exceptions are needed: httpx.ConnectTimeout is a sibling of
                # ConnectError, not a subclass of it (they hang off
                # TimeoutException vs NetworkError respectively), and a
                # connect-stage timeout — packets silently dropped rather than
                # actively refused — is the more common real-world shape of
                # exactly the "broken IPv6 route" case this fallback exists
                # for, not the exception thrown for an address that's merely
                # unreachable.
                last_error = exc
                continue
        assert last_error is not None  # ips is non-empty; the loop always runs >=1 time
        raise last_error


def ssrf_safe_client(**kwargs) -> httpx.AsyncClient:
    """The only way any code in this project should construct an AsyncClient
    for fetching an externally-supplied URL — wires in PinnedTransport so the
    DNS resolution that validated a host is the same resolution the TCP
    connection actually uses. Tests override this at the `httpx.AsyncClient`
    patch point same as before (see tests/test_feed_discovery.py's
    _mock_client_factory): MockTransport always overwrites `transport`, so
    this default never reaches a mocked client.

    Also defaults keep-alive connection reuse off (`Limits(
    max_keepalive_connections=0)`). PinnedTransport rewrites request.url's
    host to the resolved IP before delegating to the real transport, which
    means httpcore's connection-pool key is that IP too — so two *different*
    hostnames that happen to resolve to the same shared-hosting/CDN IP would
    collide in the pool, and a request for the second could be sent over a
    connection whose TLS session (and SNI) was established for the first.
    Disabling reuse means no connection ever outlives the single request it
    was opened for, so there is nothing left in the pool for a later request
    — to any hostname — to collide with.

    None of the current callers pass other transport-level kwargs (verify,
    cert, http2, proxy...) — if one ever needs to, it must go to
    PinnedTransport(...) instead of here, since httpx.AsyncClient silently
    ignores `limits` (and verify/cert/http2/proxy) once an explicit
    `transport` is supplied — it only applies them when *it* constructs the
    default transport itself. That's why `limits` below is threaded into
    PinnedTransport(...) directly rather than passed alongside `transport` in
    kwargs.
    """
    kwargs.setdefault(
        "transport",
        PinnedTransport(limits=httpx.Limits(max_keepalive_connections=0)),
    )
    return httpx.AsyncClient(**kwargs)


async def fetch_with_cap_response(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int,
    max_redirects: int = 5,
    extra_headers: dict[str, str] | None = None,
    allow_url: AllowUrl | None = None,
) -> CappedResponse:
    """Fetch `url`, capping bytes read, and return status + body + validators.

    `client` must be constructed with follow_redirects=False — redirects are
    followed manually here, re-validating each hop's host, so a public URL
    can't use a redirect to reach a private/internal target (a safe initial
    host is not enough on its own, since the SSRF guard only ever inspects
    the URL that's about to be fetched).

    `extra_headers` carries per-request headers the shared client can't hold —
    conditional-GET validators (If-None-Match / If-Modified-Since) differ per
    feed, so they can't live on the client's default headers.

    `allow_url`, when given, is consulted for the initial URL and again for every
    redirect target. Placing it here rather than at each call site is what makes
    a host denylist actually hold: the denylist is checked against the host we
    harvested, but a 3xx can hand off anywhere (blog.example.com -> facebook.com,
    or any shortener), and up to five hops per request means five chances to
    escape a per-call-site check.
    """
    # The initial URL is validated here, not just redirect hops, because not
    # every caller supplies a URL a client typed: _validate_feed() below
    # fetches candidates lifted out of remote HTML (<link rel="alternate">
    # hrefs, which may be absolute and point anywhere). Validating per call
    # site had already been forgotten once for that path, so the guard lives
    # at the single choke point every fetch goes through instead. Re-checking
    # an already-validated URL only costs one extra DNS lookup.
    current_url = validate_fetch_url(url)
    for _ in range(max_redirects + 1):
        # After the SSRF gate, never before it: crawl policy is a layer stacked
        # on top of the security boundary, not a replacement for it. Inside the
        # loop so the initial URL and each redirect hop are both covered by this
        # one call.
        if allow_url is not None and not await allow_url(current_url):
            raise DiscoveryError("Blocked by crawl policy")
        async with client.stream("GET", current_url, headers=extra_headers) as resp:
            # Must precede the redirect branch: httpx's is_redirect is True for
            # *any* 3xx, so a 304 would otherwise be treated as a redirect and
            # rejected for its (correctly) missing Location header. It also
            # slips past raise_for_status, which only fires on 4xx/5xx — so
            # without this branch the empty body would reach parse_feed and
            # surface as "malformed XML" rather than the no-change it is.
            if resp.status_code == 304:
                return CappedResponse(
                    status_code=304,
                    text="",
                    content_type=resp.headers.get("content-type", ""),
                    etag=resp.headers.get("etag"),
                    last_modified=resp.headers.get("last-modified"),
                )
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise DiscoveryError(f"Redirect ({resp.status_code}) missing Location header")
                current_url = validate_fetch_url(
                    str(httpx.URL(current_url).join(location))
                )
                continue
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise DiscoveryError(f"Response exceeds {max_bytes} bytes")
            body = b"".join(chunks)
            encoding = resp.encoding or "utf-8"
            try:
                text = body.decode(encoding, errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
            return CappedResponse(
                status_code=resp.status_code,
                text=text,
                content_type=ctype,
                etag=resp.headers.get("etag"),
                last_modified=resp.headers.get("last-modified"),
            )
    raise DiscoveryError(f"Too many redirects (> {max_redirects})")


async def fetch_with_cap(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int,
    max_redirects: int = 5,
    allow_url: AllowUrl | None = None,
) -> tuple[str, str]:
    """Return (text, content_type). Caps bytes read.

    Thin wrapper over fetch_with_cap_response for the unconditional callers
    (discovery, manual/OPML import) that only ever want the body.

    Public (not a leading-underscore name) because rss_parser.fetch_and_parse
    calls this too, for the same streaming byte cap — a bare httpx client.get()
    buffers the entire response in memory with no limit.
    """
    resp = await fetch_with_cap_response(
        client, url, max_bytes, max_redirects=max_redirects, allow_url=allow_url
    )
    if resp.not_modified:
        # No validators were sent, so a 304 here is the server violating the
        # spec. Fail loudly rather than hand back an empty body that would
        # resurface downstream as a confusing "malformed feed XML".
        raise DiscoveryError("Unexpected 304 for an unconditional request")
    return resp.text, resp.content_type


def _extract_feed_links(html: str, base_url: str) -> list[DiscoveryCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[DiscoveryCandidate] = []
    seen: set[str] = set()
    # Cap qualifying candidates, not the raw <link> tags scanned: BeautifulSoup
    # has already parsed the whole (byte-capped) document by the time find_all
    # returns anything, so limiting the scan itself (find_all(..., limit=...))
    # buys no real cost saving — it only risks stopping before the actual feed
    # declaration on a page whose <head> happens to list many ordinary
    # stylesheet/icon/preload <link> tags first. Capping on candidates found
    # keeps the bound that matters (outbound validation fetches downstream in
    # discover_feeds(), DB lookups in routers/discover.py) without that
    # false-negative.
    for link in soup.find_all("link"):
        if len(candidates) >= MAX_FEED_LINK_CANDIDATES:
            break
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = [rel]
        if "alternate" not in [r.lower() for r in rel]:
            continue
        ctype = (link.get("type") or "").lower()
        if not any(ft in ctype for ft in FEED_CONTENT_TYPES):
            continue
        href = link.get("href")
        if not href:
            continue
        feed_url = urljoin(base_url, href)
        if feed_url in seen:
            continue
        seen.add(feed_url)
        candidates.append(
            DiscoveryCandidate(
                feed_url=feed_url,
                title=link.get("title"),
                website_url=base_url,
            )
        )
    return candidates


async def _validate_feed(
    client: httpx.AsyncClient,
    feed_url: str,
    delay_seconds: float = 0.0,
    allow_url: AllowUrl | None = None,
) -> ParsedFeed | None:
    # Sleeping here rather than at each of discover_feeds' loops is what makes
    # "pause between requests, but not before the first one" fall out for free:
    # discover_feeds' own initial fetch is the only request that doesn't come
    # through this function.
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    try:
        text, ctype = await fetch_with_cap(
            client, feed_url, MAX_FEED_BYTES, allow_url=allow_url
        )
        return parse_feed(text)
    except (httpx.HTTPError, DiscoveryError, ValueError):
        return None


async def discover_feeds(
    url: str,
    timeout: float = 12.0,
    delay_seconds: float = 0.0,
    allow_url: AllowUrl | None = None,
    raise_on_fetch_error: bool = False,
) -> list[DiscoveryCandidate]:
    """Discover candidate feeds for a URL. Returns list (possibly empty).

    `delay_seconds` paces the requests this call makes against the one host it is
    probing (a full run is the initial page plus up to seven well-known feed
    paths), and `allow_url` gates every one of them. Both default to off so
    user-triggered discovery via POST /api/discover is unchanged; the autonomous
    loop supplies them.

    `raise_on_fetch_error` decides what `[]` means. By default the initial fetch
    failing is absorbed into an empty result, which is right for a person who
    pasted a URL — they just want "nothing found". An unattended caller needs the
    difference, because "this site has no feed" is terminal while "this site is
    down" must be retried; passing True re-raises instead, leaving `[]` to mean
    unambiguously "we fetched the page and it advertises no feed".
    """
    safe_url = validate_fetch_url(url)
    headers = {"User-Agent": user_agent(), "Accept": "text/html,application/xhtml+xml,*/*"}

    async with ssrf_safe_client(
        follow_redirects=False, timeout=timeout, headers=headers
    ) as client:
        # The pause applies before the *first* request too. The autonomous caller
        # has just fetched this host's robots.txt, so without it the advertised
        # per-host interval wouldn't cover the robots-to-homepage transition —
        # the two requests would go out back to back.
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        # 1. Maybe the URL itself is already a feed.
        try:
            text, ctype = await fetch_with_cap(
                client, safe_url, MAX_FEED_BYTES, allow_url=allow_url
            )
        except (httpx.HTTPError, DiscoveryError):
            if raise_on_fetch_error:
                raise
            return []

        if any(ft in ctype.lower() for ft in FEED_CONTENT_TYPES):
            try:
                parsed = parse_feed(text)
                return [
                    DiscoveryCandidate(
                        feed_url=safe_url,
                        title=parsed.title,
                        website_url=parsed.website_url,
                    )
                ]
            except ValueError:
                pass

        # 2. Parse HTML <link rel="alternate"> tags.
        candidates = _extract_feed_links(text, safe_url)

        # 3. Validate each candidate; keep working ones with parsed metadata.
        validated: list[DiscoveryCandidate] = []
        for c in candidates:
            parsed = await _validate_feed(client, c.feed_url, delay_seconds, allow_url)
            if parsed:
                validated.append(
                    DiscoveryCandidate(
                        feed_url=c.feed_url,
                        title=c.title or parsed.title,
                        website_url=parsed.website_url or safe_url,
                    )
                )
        if validated:
            return validated

        # 4. Fallback: try well-known paths.
        parsed_url = urlparse(safe_url)
        root = f"{parsed_url.scheme}://{parsed_url.netloc}"
        for path in FALLBACK_PATHS:
            candidate_url = urljoin(root, path)
            parsed = await _validate_feed(
                client, candidate_url, delay_seconds, allow_url
            )
            if parsed:
                validated.append(
                    DiscoveryCandidate(
                        feed_url=candidate_url,
                        title=parsed.title,
                        website_url=parsed.website_url or root,
                    )
                )
        return validated
