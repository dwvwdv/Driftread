from __future__ import annotations
from unittest.mock import patch

import httpx
import pytest

import rss_parser
from rss_parser import parse_feed

ENTITY_EXPANSION_RSS = """<?xml version="1.0"?>
<!DOCTYPE rss [
  <!ENTITY a "spam">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
]>
<rss version="2.0"><channel><title>&b;</title><link>https://x.com</link></channel></rss>"""

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


def test_rss_description_markup_becomes_the_body():
    """A feed whose <description> *is* the article (very common — no
    content:encoded anywhere) used to leave `content` NULL, so the reader fell
    through to its text-only summary branch and printed the markup as literal
    tags on screen."""
    xml = """<rss version="2.0"><channel><title>T</title><link>https://x.com</link>
      <item><title>A</title><link>https://x.com/1</link>
        <description>&lt;p&gt;第一段&lt;a href="https://g.com"&gt;連結&lt;/a&gt;。&lt;/p&gt;&lt;p&gt;第二段&lt;/p&gt;</description>
      </item></channel></rss>"""
    article = parse_feed(xml).articles[0]

    assert article.content == '<p>第一段<a href="https://g.com">連結</a>。</p><p>第二段</p>'
    # Flattened for display, and no space wedged in around the inline <a> —
    # that reads as a typo in Chinese ("第一段 連結 。").
    assert article.summary == "第一段連結。 第二段"


def test_rss_content_encoded_still_wins_over_description():
    xml = """<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel><title>T</title><link>https://x.com</link>
      <item><title>A</title><link>https://x.com/1</link>
        <description>&lt;p&gt;short blurb&lt;/p&gt;</description>
        <content:encoded>&lt;p&gt;the whole article&lt;/p&gt;</content:encoded>
      </item></channel></rss>"""
    article = parse_feed(xml).articles[0]

    assert article.content == "<p>the whole article</p>"
    assert article.summary == "short blurb"


def test_plain_text_description_is_left_alone():
    """The `<` here opens no tag. A greedy tag-stripper would eat the middle of
    the sentence, and promoting this to `content` would drop the reader's
    "go read the original" fallback for a feed that only ships blurbs."""
    xml = """<rss version="2.0"><channel><title>T</title><link>https://x.com</link>
      <item><title>A</title><link>https://x.com/1</link>
        <description>if x &lt; 3 and y &gt; 2 then done</description>
      </item></channel></rss>"""
    article = parse_feed(xml).articles[0]

    assert article.content is None
    assert article.summary == "if x < 3 and y > 2 then done"


def test_image_only_description_becomes_the_body():
    """Photo blogs and webcomics: the image *is* the article, and it routinely
    arrives as a bare `<img>` with no closing or self-closing tag. Left
    unpromoted, the reader would print the tag as text and show no picture."""
    xml = """<rss version="2.0"><channel><title>T</title><link>https://x.com</link>
      <item><title>A</title><link>https://x.com/1</link>
        <description>&lt;img src="https://x.com/today.png" alt="today"&gt;</description>
      </item></channel></rss>"""
    article = parse_feed(xml).articles[0]

    assert article.content == '<img src="https://x.com/today.png" alt="today">'
    assert article.summary is None


def test_summary_drops_script_and_style_bodies():
    xml = """<rss version="2.0"><channel><title>T</title><link>https://x.com</link>
      <item><title>A</title><link>https://x.com/1</link>
        <description>&lt;style&gt;p{color:red}&lt;/style&gt;&lt;p&gt;Hi&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;</description>
      </item></channel></rss>"""
    assert parse_feed(xml).articles[0].summary == "Hi"


def test_atom_xhtml_content_is_serialized_not_truncated():
    """Atom's type="xhtml" delivers the body as real child elements. Reading
    only `.text` returned the whitespace before the first child, i.e. nothing —
    the article looked like it had no cached content at all."""
    xml = """<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
      <entry><title>E</title><link href="https://a.com/1" rel="alternate"/>
        <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Hello <b>world</b></p><br/><img src="a.png"/></div></content>
      </entry></feed>"""
    article = parse_feed(xml).articles[0]

    # Namespaces stripped and void elements left unclosed: this has to be HTML a
    # browser will render, not the XML round-trip ET.tostring() would produce.
    assert article.content == '<div><p>Hello <b>world</b></p><br><img src="a.png"></div>'
    assert article.summary == "Hello world"


def test_atom_markup_summary_becomes_the_body():
    xml = """<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
      <entry><title>E</title><link href="https://a.com/1" rel="alternate"/>
        <summary>&lt;p&gt;the whole entry&lt;/p&gt;</summary>
      </entry></feed>"""
    article = parse_feed(xml).articles[0]

    assert article.content == "<p>the whole entry</p>"
    assert article.summary == "the whole entry"


def test_atom_plain_summary_is_not_promoted():
    article = parse_feed(ATOM_SAMPLE).articles[0]
    assert article.content is None
    assert article.summary == "Atom entry summary"


def test_summary_decodes_entities_after_stripping_tags():
    """Order matters: a `&lt;p&gt;` that survived double-escaping upstream must
    end up as visible text, not be re-read as a tag and deleted."""
    xml = """<rss version="2.0"><channel><title>T</title><link>https://x.com</link>
      <item><title>A</title><link>https://x.com/1</link>
        <description>&lt;p&gt;Tom &amp;amp; Jerry wrote &amp;lt;p&amp;gt;&lt;/p&gt;</description>
      </item></channel></rss>"""
    assert parse_feed(xml).articles[0].summary == "Tom & Jerry wrote <p>"


def test_prose_quoting_a_tag_is_not_markup():
    """XML forces both a real body and a sentence *about* HTML to arrive escaped,
    so they are indistinguishable by "contains a tag". Getting this wrong runs the
    destructive direction: the `<p>` would be stripped out of the summary, and the
    reader would hand the rest to [innerHTML], which swallows it."""
    xml = """<rss version="2.0"><channel><title>T</title><link>https://x.com</link>
      <item><title>A</title><link>https://x.com/1</link>
        <description>Use &lt;p&gt; for paragraphs and &lt;a href="…"&gt; for links</description>
      </item></channel></rss>"""
    article = parse_feed(xml).articles[0]

    assert article.content is None
    assert article.summary == 'Use <p> for paragraphs and <a href="…"> for links'


def test_what_makes_a_value_a_document():
    """The discriminator, stated directly: a closing tag, an explicitly
    self-closed tag, or a void element carrying an attribute."""
    assert rss_parser._looks_like_markup("<p>body</p>")
    assert rss_parser._looks_like_markup('<div><img src="a.png"/></div>')
    assert rss_parser._looks_like_markup('<img src="a.png"/>')
    # No slash, no closing tag — but an image-only description *is* the article
    # on a photo blog, and dropping it would lose the whole item.
    assert rss_parser._looks_like_markup('<img src="a.png">')
    assert rss_parser._looks_like_markup('<IMG SRC="a.png">')
    assert rss_parser._looks_like_markup('<hr class="rule">')

    assert not rss_parser._looks_like_markup("Use <p> for paragraphs")
    assert not rss_parser._looks_like_markup("if x < 3 and y > 2")
    assert not rss_parser._looks_like_markup("")
    # Void-element attributes count; attributes in general do not. Tech writing
    # quotes `<a href="…">` constantly, and treating that as markup is what
    # deletes the token the sentence is about.
    assert not rss_parser._looks_like_markup('quote <a href="https://x"> like so')


def test_a_bare_void_tag_is_left_as_text():
    """"one<br>two" really is markup, but so is "use <br> to break lines", and
    the two are indistinguishable. Only the second loses text if we guess wrong,
    so the tie goes to leaving it alone: misjudging the first just shows a `<br>`
    on screen, which is visible and fixable."""
    assert not rss_parser._looks_like_markup("one<br>two")
    assert not rss_parser._looks_like_markup("use <br> to break lines")
    assert rss_parser._plain_text("use <br> to break lines") == "use <br> to break lines"


def test_plain_text_leaves_tag_shaped_prose_alone():
    assert rss_parser._plain_text("Use <p> for paragraphs") == "Use <p> for paragraphs"
    assert rss_parser._plain_text("<p>Use <b>bold</b></p>") == "Use bold"


def test_parse_rss_no_articles():
    xml = """<rss version="2.0"><channel><title>Empty</title><link>https://x.com</link></channel></rss>"""
    feed = parse_feed(xml)
    assert feed.title == "Empty"
    assert feed.articles == []


def test_parse_feed_rejects_entity_expansion():
    """A billion-laughs payload from a remote feed server must raise a plain
    ValueError (the same failure mode as any other malformed feed, and what
    services.feed_discovery's narrow `except ValueError` already expects) —
    not blow up memory/CPU or raise an unhandled defusedxml exception that
    turns an anonymous /api/discover call into a 500."""
    with pytest.raises(ValueError):
        parse_feed(ENTITY_EXPANSION_RSS)


def test_parse_feed_rejects_malformed_xml():
    with pytest.raises(ValueError):
        parse_feed("<not xml")


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

    def fake_is_safe_host(host: str, timeout: float | None = None) -> bool:
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


@pytest.mark.asyncio
async def test_fetch_and_parse_conditional_sends_stored_validators():
    seen: list[dict[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({
            "if-none-match": request.headers.get("if-none-match"),
            "if-modified-since": request.headers.get("if-modified-since"),
        })
        return httpx.Response(
            200,
            text=RSS_SAMPLE,
            headers={
                "content-type": "application/rss+xml",
                "etag": 'W/"fresh"',
                "last-modified": "Thu, 02 Jan 2025 00:00:00 GMT",
            },
        )

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        result = await rss_parser.fetch_and_parse_conditional(
            "https://example.com/feed.xml",
            etag='W/"old"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
        )

    assert seen == [{
        "if-none-match": 'W/"old"',
        "if-modified-since": "Wed, 01 Jan 2025 00:00:00 GMT",
    }]
    assert result.not_modified is False
    assert result.parsed is not None
    assert result.parsed.title == "Test Feed"
    # Fresh validators come back for storage on the feed row.
    assert result.etag == 'W/"fresh"'
    assert result.last_modified == "Thu, 02 Jan 2025 00:00:00 GMT"


@pytest.mark.asyncio
async def test_fetch_and_parse_conditional_omits_headers_when_no_validators():
    seen: list[dict[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({
            "if-none-match": request.headers.get("if-none-match"),
            "if-modified-since": request.headers.get("if-modified-since"),
        })
        return httpx.Response(
            200, text=RSS_SAMPLE, headers={"content-type": "application/rss+xml"}
        )

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        result = await rss_parser.fetch_and_parse_conditional("https://example.com/feed.xml")

    assert seen == [{"if-none-match": None, "if-modified-since": None}]
    assert result.parsed is not None
    assert result.etag is None


@pytest.mark.asyncio
async def test_fetch_and_parse_conditional_handles_304():
    """304 carries no body. It is not a 4xx/5xx so raise_for_status lets it
    through, and httpx's is_redirect is False for it — without an explicit
    branch the empty body reaches parse_feed and surfaces as "malformed XML",
    turning a success-with-no-change into a spurious feed failure.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        result = await rss_parser.fetch_and_parse_conditional(
            "https://example.com/feed.xml", etag='W/"kept"'
        )

    assert result.not_modified is True
    assert result.parsed is None
    # A 304 that echoes no validators must not clear the stored ones, or every
    # later poll would go out unconditional.
    assert result.etag == 'W/"kept"'


@pytest.mark.asyncio
async def test_unconditional_fetch_rejects_unsolicited_304():
    """fetch_and_parse sends no validators, so a 304 is the server misbehaving.
    It must raise rather than yield an empty body that resurfaces later as a
    misleading "malformed feed XML"."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        with pytest.raises(Exception):
            await rss_parser.fetch_and_parse("https://example.com/feed.xml")


@pytest.mark.asyncio
async def test_fetch_and_parse_conditional_still_guards_redirects_to_private_ips():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    def fake_is_safe_host(host: str, timeout: float | None = None) -> bool:
        return host != "169.254.169.254"

    with (
        patch("services.feed_discovery._is_safe_host", side_effect=fake_is_safe_host),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        with pytest.raises(Exception):
            await rss_parser.fetch_and_parse_conditional("https://start.example.com/")


@pytest.mark.asyncio
async def test_fetch_and_parse_conditional_rejects_oversized_response():
    from services.feed_discovery import MAX_FEED_BYTES

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<rss>" + b"a" * (MAX_FEED_BYTES + 1),
            headers={"content-type": "application/rss+xml"},
        )

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        with pytest.raises(Exception):
            await rss_parser.fetch_and_parse_conditional("https://example.com/feed.xml")


@pytest.mark.asyncio
async def test_fetch_and_parse_uses_configured_user_agent(monkeypatch):
    """DISCOVERY_USER_AGENT must apply to feed ingestion too, not only to
    candidate discovery — /discover/import, admin import/refresh and OPML
    import all fetch through fetch_and_parse().
    """
    monkeypatch.setenv("DISCOVERY_USER_AGENT", "CustomAgent/9.9")
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(
            200, text=RSS_SAMPLE, headers={"content-type": "application/rss+xml"}
        )

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        await rss_parser.fetch_and_parse("https://example.com/feed.xml")

    assert seen == ["CustomAgent/9.9"]
