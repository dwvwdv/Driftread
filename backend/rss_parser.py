from __future__ import annotations
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring


RSS_NS = ""
ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
DC_NS = "http://purl.org/dc/elements/1.1/"


@dataclass
class ParsedArticle:
    title: str
    url: str
    summary: str | None = None
    content: str | None = None
    author: str | None = None
    published_at: datetime | None = None


@dataclass
class ParsedFeed:
    title: str
    url: str
    description: str | None = None
    website_url: str | None = None
    language: str | None = None
    articles: list[ParsedArticle] = field(default_factory=list)


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    return (el.text or "").strip() or None


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _tag(ns: str, name: str) -> str:
    if ns:
        return f"{{{ns}}}{name}"
    return name


def _parse_rss(root: ET.Element) -> ParsedFeed:
    channel = root.find("channel")
    if channel is None:
        raise ValueError("No <channel> element found")

    feed = ParsedFeed(
        title=_text(channel.find("title")) or "Untitled",
        url=_text(channel.find("link")) or "",
        description=_text(channel.find("description")),
        website_url=_text(channel.find("link")),
        language=_text(channel.find("language")),
    )

    for item in channel.findall("item"):
        title = _text(item.find("title")) or "Untitled"
        link = _text(item.find("link")) or _text(item.find("guid")) or ""
        desc = _text(item.find("description"))
        content_el = item.find(_tag(CONTENT_NS, "encoded"))
        content = _text(content_el)
        author = _text(item.find("author")) or _text(item.find(_tag(DC_NS, "creator")))
        pub_date = _parse_date(_text(item.find("pubDate")))

        feed.articles.append(ParsedArticle(
            title=title,
            url=link,
            summary=desc,
            content=content,
            author=author,
            published_at=pub_date,
        ))

    return feed


def _parse_atom(root: ET.Element) -> ParsedFeed:
    ns = ATOM_NS
    title = _text(root.find(_tag(ns, "title"))) or "Untitled"
    website_url = None
    for link_el in root.findall(_tag(ns, "link")):
        rel = link_el.get("rel", "alternate")
        if rel == "alternate":
            website_url = link_el.get("href")
            break
    subtitle = _text(root.find(_tag(ns, "subtitle")))

    feed = ParsedFeed(
        title=title,
        url=website_url or "",
        description=subtitle,
        website_url=website_url,
    )

    for entry in root.findall(_tag(ns, "entry")):
        etitle = _text(entry.find(_tag(ns, "title"))) or "Untitled"
        elink = None
        for link_el in entry.findall(_tag(ns, "link")):
            rel = link_el.get("rel", "alternate")
            if rel == "alternate":
                elink = link_el.get("href")
                break
        if not elink:
            elink = _text(entry.find(_tag(ns, "id"))) or ""

        summary_el = entry.find(_tag(ns, "summary"))
        content_el = entry.find(_tag(ns, "content"))
        summary = _text(summary_el)
        content = _text(content_el)

        author_el = entry.find(_tag(ns, "author"))
        author = None
        if author_el is not None:
            author = _text(author_el.find(_tag(ns, "name")))

        pub_date = _parse_date(
            _text(entry.find(_tag(ns, "published")))
            or _text(entry.find(_tag(ns, "updated")))
        )

        feed.articles.append(ParsedArticle(
            title=etitle,
            url=elink,
            summary=summary,
            content=content,
            author=author,
            published_at=pub_date,
        ))

    return feed


def parse_feed(xml_text: str) -> ParsedFeed:
    ET.register_namespace("content", CONTENT_NS)
    ET.register_namespace("dc", DC_NS)
    # safe_fromstring (defusedxml) rejects DTD entity declarations and
    # external references, closing the same billion-laughs / XXE holes that
    # opml.py's import guards against — except here the XML comes from
    # fetch_and_parse/discover_feeds, i.e. an arbitrary remote server chosen
    # by whoever calls the fully public, unauthenticated /api/discover
    # endpoint. A plain xml.etree.ElementTree.fromstring here would let any
    # anonymous caller point at a feed URL serving a malicious payload and
    # exhaust this process's memory/CPU.
    try:
        root = safe_fromstring(xml_text)
    except (ET.ParseError, DefusedXmlException) as e:
        raise ValueError(f"Malformed feed XML: {e}") from e

    tag = root.tag.lower()
    if "rss" in tag or root.find("channel") is not None:
        return _parse_rss(root)
    if "feed" in tag or ATOM_NS in root.tag:
        return _parse_atom(root)

    raise ValueError(f"Unknown feed format: {root.tag}")


async def fetch_and_parse(url: str, timeout: float = 15.0, max_redirects: int = 5) -> ParsedFeed:
    # Local import: services.feed_discovery imports ParsedFeed/parse_feed from
    # this module at load time, so importing it back at module level here
    # would be circular. Delegates the actual HTTP fetch to fetch_with_cap,
    # which follows redirects manually and re-validates each hop (a public
    # URL that 302s to e.g. http://169.254.169.254/ must not bypass the SSRF
    # guard) and caps response bytes read — a bare client.get() has no size
    # limit and would let a validated host exhaust memory via a huge body.
    from services.feed_discovery import MAX_FEED_BYTES, fetch_with_cap, validate_fetch_url

    headers = {"User-Agent": "Driftread/1.0"}
    current_url = validate_fetch_url(url)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, headers=headers) as client:
        text, _ctype = await fetch_with_cap(client, current_url, MAX_FEED_BYTES, max_redirects=max_redirects)
    return parse_feed(text)
