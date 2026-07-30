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
    for link in soup.find_all("link"):
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
) -> list[DiscoveryCandidate]:
    """Discover candidate feeds for a URL. Returns list (possibly empty).

    `delay_seconds` pauses between the requests this call makes against the one
    host it is probing (a full run is the initial page plus up to seven
    well-known feed paths), and `allow_url` gates every one of them. Both default
    to off so user-triggered discovery via POST /api/discover is unchanged; the
    autonomous loop supplies them.

    Note that fetch failures are absorbed into an empty result rather than
    raised, so `[]` cannot distinguish "this site has no feed" from "this site is
    unreachable". Callers that need to tell those apart have to probe
    reachability separately — see services/discovery_probe.py, which reuses its
    robots.txt fetch for exactly that.
    """
    safe_url = validate_fetch_url(url)
    headers = {"User-Agent": user_agent(), "Accept": "text/html,application/xhtml+xml,*/*"}

    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, headers=headers
    ) as client:
        # 1. Maybe the URL itself is already a feed.
        try:
            text, ctype = await fetch_with_cap(
                client, safe_url, MAX_FEED_BYTES, allow_url=allow_url
            )
        except (httpx.HTTPError, DiscoveryError):
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
