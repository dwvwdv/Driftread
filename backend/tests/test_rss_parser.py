from __future__ import annotations
from unittest.mock import patch

import httpx
import pytest

import rss_parser
from rss_parser import parse_feed

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <description>A test feed</description>
    <language>en</language>
    <item>
      <title>Article One</title>
      <link>https://example.com/1</link>
      <description>Summary of article one</description>
      <author>Alice</author>
      <pubDate>Wed, 01 Jan 2025 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Article Two</title>
      <link>https://example.com/2</link>
      <description>Summary of article two</description>
    </item>
  </channel>
</rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test</title>
  <link href="https://atom.example.com" rel="alternate"/>
  <subtitle>An Atom feed</subtitle>
  <entry>
    <title>Atom Entry</title>
    <link href="https://atom.example.com/entry1" rel="alternate"/>
    <id>https://atom.example.com/entry1</id>
    <author><name>Bob</name></author>
    <published>2025-06-01T10:00:00Z</published>
    <summary>Atom entry summary</summary>
  </entry>
</feed>"""


def test_parse_rss_feed():
    feed = parse_feed(RSS_SAMPLE)
    assert feed.title == "Test Feed"
    assert feed.description == "A test feed"
    assert feed.language == "en"
    assert len(feed.articles) == 2
    assert feed.articles[0].title == "Article One"
    assert feed.articles[0].url == "https://example.com/1"
    assert feed.articles[0].author == "Alice"
    assert feed.articles[0].published_at is not None
    assert feed.articles[1].title == "Article Two"


def test_parse_atom_feed():
    feed = parse_feed(ATOM_SAMPLE)
    assert feed.title == "Atom Test"
    assert feed.description == "An Atom feed"
    assert len(feed.articles) == 1
    assert feed.articles[0].title == "Atom Entry"
    assert feed.articles[0].url == "https://atom.example.com/entry1"
    assert feed.articles[0].author == "Bob"
    assert feed.articles[0].published_at is not None


def test_parse_rss_no_articles():
    xml = """<rss version="2.0"><channel><title>Empty</title><link>https://x.com</link></channel></rss>"""
    feed = parse_feed(xml)
    assert feed.title == "Empty"
    assert feed.articles == []


_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(handler):
    """Build a replacement for httpx.AsyncClient that routes through a
    MockTransport instead of the network, ignoring follow_redirects (the
    code under test must handle redirects itself, not delegate to httpx)."""

    def factory(*args, **kwargs):
        kwargs.pop("follow_redirects", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return factory


@pytest.mark.asyncio
async def test_fetch_and_parse_follows_safe_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "start.example.com":
            return httpx.Response(302, headers={"location": "https://example.com/feed.xml"})
        return httpx.Response(
            200, text=RSS_SAMPLE, headers={"content-type": "application/rss+xml"}
        )

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        feed = await rss_parser.fetch_and_parse("https://start.example.com/")
    assert feed.title == "Test Feed"


@pytest.mark.asyncio
async def test_fetch_and_parse_rejects_redirect_to_private_ip():
    def handler(request: httpx.Request) -> httpx.Response:
        # Only reached if the redirect target isn't rejected first.
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    def fake_is_safe_host(host: str) -> bool:
        return host != "169.254.169.254"

    with (
        patch("services.feed_discovery._is_safe_host", side_effect=fake_is_safe_host),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        with pytest.raises(Exception):
            await rss_parser.fetch_and_parse("https://start.example.com/")


@pytest.mark.asyncio
async def test_fetch_and_parse_rejects_too_many_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"location": f"https://start.example.com/{n + 1}"})

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        with pytest.raises(Exception):
            await rss_parser.fetch_and_parse("https://start.example.com/0", max_redirects=3)


@pytest.mark.asyncio
async def test_fetch_and_parse_rejects_oversized_response():
    from services.feed_discovery import MAX_FEED_BYTES

    oversized_body = b"<rss>" + b"a" * (MAX_FEED_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=oversized_body, headers={"content-type": "application/rss+xml"}
        )

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        with pytest.raises(Exception):
            await rss_parser.fetch_and_parse("https://start.example.com/")
