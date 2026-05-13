"""Auto-discover RSS / Atom feeds from any URL.

Strategy (in order):
1. Fetch HTML, parse <link rel="alternate" type="application/rss+xml|atom+xml">.
2. Try a list of well-known fallback paths (/feed, /rss, /atom.xml, ...).
3. Validate each candidate by attempting to parse it with rss_parser.

SSRF protection is applied: private / loopback / link-local addresses are rejected.
"""
from __future__ import annotations
import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from rss_parser import ParsedFeed, parse_feed


FEED_CONTENT_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml")
FALLBACK_PATHS = ("/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml", "/feed/")
MAX_HTML_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_FEED_BYTES = 5 * 1024 * 1024  # 5 MiB


@dataclass
class DiscoveryCandidate:
    feed_url: str
    title: str | None
    website_url: str | None


class DiscoveryError(Exception):
    pass


def _user_agent() -> str:
    return os.getenv("DISCOVERY_USER_AGENT", "Driftread/1.0 (+https://driftread.pages.dev)")


def _is_safe_host(host: str) -> bool:
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
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


def _normalize_url(url: str) -> str:
    if not url:
        raise DiscoveryError("Empty URL")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DiscoveryError(f"Unsupported scheme: {parsed.scheme}")
    if not parsed.netloc:
        raise DiscoveryError("URL missing host")
    if not _is_safe_host(parsed.hostname or ""):
        raise DiscoveryError("Refusing to fetch private/loopback address")
    return url


async def _fetch(
    client: httpx.AsyncClient, url: str, max_bytes: int
) -> tuple[str, str]:
    """Return (text, content_type). Caps bytes read."""
    async with client.stream("GET", url) as resp:
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
    return text, ctype


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
    client: httpx.AsyncClient, feed_url: str
) -> ParsedFeed | None:
    try:
        text, ctype = await _fetch(client, feed_url, MAX_FEED_BYTES)
        return parse_feed(text)
    except (httpx.HTTPError, DiscoveryError, ValueError):
        return None


async def discover_feeds(url: str, timeout: float = 12.0) -> list[DiscoveryCandidate]:
    """Discover candidate feeds for a URL. Returns list (possibly empty)."""
    safe_url = _normalize_url(url)
    headers = {"User-Agent": _user_agent(), "Accept": "text/html,application/xhtml+xml,*/*"}

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers=headers
    ) as client:
        # 1. Maybe the URL itself is already a feed.
        try:
            text, ctype = await _fetch(client, safe_url, MAX_FEED_BYTES)
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
            parsed = await _validate_feed(client, c.feed_url)
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
            parsed = await _validate_feed(client, candidate_url)
            if parsed:
                validated.append(
                    DiscoveryCandidate(
                        feed_url=candidate_url,
                        title=parsed.title,
                        website_url=parsed.website_url or root,
                    )
                )
        return validated
