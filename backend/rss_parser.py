from __future__ import annotations
import html as html_lib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring


RSS_NS = ""
ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
DC_NS = "http://purl.org/dc/elements/1.1/"

# Deliberately conservative: the opening `<` must be followed by a letter or a
# `/`, so prose like "if x < 3 and y > 2" is left alone. A greedy `<[^>]+>`
# would eat the middle of that sentence.
_TAG_RE = re.compile(r"<!--.*?-->|</?[a-zA-Z][^>]*>", re.DOTALL)
# Their text is markup source, not readable prose — it has to go before tags
# are stripped, or a stylesheet ends up inside the summary.
_DROP_WHOLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_TAG_NAME_RE = re.compile(r"</?\s*([a-zA-Z][a-zA-Z0-9]*)")
# Tags that imply a break in the text. Everything else is inline and gets
# removed outright: `<a>` and `<em>` are word-internal, and turning them into
# spaces sprayed gaps through Chinese summaries ("这里记录 开源 。").
_BLOCK_TAGS = frozenset(
    {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
     "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
     "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
     "tbody", "td", "tfoot", "th", "thead", "tr", "ul"}
)


def _tag_to_space(match: re.Match[str]) -> str:
    name = _TAG_NAME_RE.match(match.group(0))
    return " " if name and name.group(1).lower() in _BLOCK_TAGS else ""

# Serialized without a closing tag, matching the HTML spec rather than XML's
# self-closing form (`<br></br>` is two line breaks in a browser).
_VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
     "param", "source", "track", "wbr"}
)

# Tells an HTML *document* apart from prose that happens to mention a tag.
#
# The two are genuinely indistinguishable by the time we see them: XML requires
# both to be escaped, so a body sent as `&lt;p&gt;text&lt;/p&gt;` and a sentence
# written as `Use &lt;p&gt; for paragraphs` both arrive from the XML parser with
# real `<` characters. Deciding on "contains a tag" got the sentence wrong, and
# the failure was the bad direction — the text was stripped or fed to a renderer
# that swallowed it, so it vanished from the page instead of merely looking ugly.
#
# Three shapes count, and the asymmetry above is why the list stops where it does:
#
#   </x>          a closing tag. Markup that encloses anything has one. Prose
#                 quoting a *paired* tag has one too ("Use <strong>bold</strong>
#                 for emphasis"), and that is knowingly accepted: it is the same
#                 string shape as "Hello <b>world</b>, welcome", an ordinary short
#                 RSS body, so no rule can separate them. The tie-break is the one
#                 below — misreading the sentence renders `bold` in bold and drops
#                 two tags, while misreading the body would put its markup back on
#                 screen as literal tags, which is the bug this file exists to fix.
#                 Every word survives either way; only a *paired* quote is safe
#                 that way, which is why the empty `<p>` case is excluded.
#   <x …/>        explicitly self-closed.
#   <img src=…>   a void element *carrying an attribute*. This is what makes an
#                 image-only description work — very common on photo blogs and
#                 webcomics, where the image is the article. Restricted to void
#                 elements on purpose: "an attribute" on its own would swallow
#                 prose quoting `<a href="…">`, which tech writing does daily.
#
# A *bare* void tag stays out. "one<br>two" really is markup, but so is "use
# <br> to break lines", and only the second one loses text if we guess wrong —
# it would be stripped down to "use  to break lines". Misjudging the first only
# shows a `<br>` on screen: visible, and fixable by whoever notices.
_MARKUP_RE = re.compile(
    r"</[a-zA-Z]"
    r"|<[a-zA-Z][^>]*/>"
    rf"|<(?:{'|'.join(sorted(_VOID_ELEMENTS))})\b[^>]*=[^>]*>",
    re.IGNORECASE,
)


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


def _local_name(tag: str) -> str:
    """Drop the `{namespace}` prefix ElementTree puts on qualified names."""
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _serialize(el: ET.Element) -> str:
    """Render a parsed element back to HTML source, namespaces stripped.

    Only reached for Atom's `type="xhtml"` content, where the body arrives as
    real child elements. ET.tostring() would emit them as `<ns0:p xmlns:ns0=…>`,
    which is not HTML a browser will style.
    """
    tag = _local_name(el.tag)
    attrs = "".join(
        f' {_local_name(k)}="{html_lib.escape(v, quote=True)}"'
        for k, v in el.attrib.items()
        # An XHTML body carries its namespace declaration as a plain attribute
        # once parsed; re-emitting it into HTML says nothing.
        if _local_name(k) != "xmlns"
    )
    tail = html_lib.escape(el.tail or "", quote=False)

    inner = html_lib.escape(el.text or "", quote=False)
    for child in el:
        inner += _serialize(child)

    if not inner and tag in _VOID_ELEMENTS:
        return f"<{tag}{attrs}>{tail}"
    return f"<{tag}{attrs}>{inner}</{tag}>{tail}"


def _inner_html(el: ET.Element | None) -> str | None:
    """Return an element's payload as HTML source.

    Feeds deliver article bodies two ways. The common one wraps the markup in
    CDATA or escapes it, so the whole body lands in `.text` already unescaped by
    the XML parser — verbatim source we must not touch. Atom's `type="xhtml"`
    instead makes the markup *real child elements*, and `.text` is then just the
    whitespace before the first child. `_text()` only ever read `.text`, so the
    second kind was silently truncated to the empty string.
    """
    if el is None:
        return None
    out = (el.text or "") + "".join(_serialize(child) for child in el)
    return out.strip() or None


def _looks_like_markup(s: str | None) -> bool:
    return bool(s) and _MARKUP_RE.search(s) is not None


def _plain_text(markup: str | None) -> str | None:
    """Flatten HTML source into readable text for the `summary` preview field.

    `summary` is rendered as text (bookmark rows, the reader's no-content
    fallback), so storing raw markup in it meant the tags showed up on screen
    verbatim. Nothing is truncated here — callers that want a short preview clamp
    it in CSS, and the reader still needs the whole thing when it is all we have.

    Tags are only stripped from something that is actually a document. A blurb
    reading "Use <p> for paragraphs" is already the plain text we want, and
    stripping it would delete the one token the sentence is about.
    """
    if not markup:
        return None
    if _looks_like_markup(markup):
        text = _TAG_RE.sub(_tag_to_space, _DROP_WHOLE_RE.sub(" ", markup))
    else:
        text = markup
    # Unescaped last either way, so a `&lt;p&gt;` that survived double-escaping
    # upstream becomes visible text rather than being re-parsed as a tag.
    text = html_lib.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


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
        desc_html = _inner_html(item.find("description"))
        content = _inner_html(item.find(_tag(CONTENT_NS, "encoded")))
        # Plenty of feeds ship the entire article in <description> and never
        # send content:encoded (ruanyifeng's weekly, for one). That left
        # `content` NULL, so the reader fell through to its text-only summary
        # branch and printed the article's markup as literal tags. When the
        # description *is* markup, it is the body.
        if not content and _looks_like_markup(desc_html):
            content = desc_html
        summary = _plain_text(desc_html) or _plain_text(content)
        author = _text(item.find("author")) or _text(item.find(_tag(DC_NS, "creator")))
        pub_date = _parse_date(_text(item.find("pubDate")))

        feed.articles.append(ParsedArticle(
            title=title,
            url=link,
            summary=summary,
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

        summary_html = _inner_html(entry.find(_tag(ns, "summary")))
        content = _inner_html(entry.find(_tag(ns, "content")))
        # Same reasoning as the RSS branch: an entry with only a markup
        # <summary> is an entry whose body is that summary.
        if not content and _looks_like_markup(summary_html):
            content = summary_html
        summary = _plain_text(summary_html) or _plain_text(content)

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


@dataclass
class ConditionalFetch:
    """Result of a conditional feed fetch. `parsed` is None exactly when
    `not_modified` is True — the server told us nothing changed and sent no
    body, so there is nothing to parse and nothing to upsert.
    """
    not_modified: bool
    parsed: ParsedFeed | None = None
    etag: str | None = None
    last_modified: str | None = None


async def fetch_and_parse_conditional(
    url: str,
    etag: str | None = None,
    last_modified: str | None = None,
    timeout: float = 15.0,
    max_redirects: int = 5,
) -> ConditionalFetch:
    """Fetch and parse a feed, skipping the download when it hasn't changed.

    Used only by the scheduled refresh path (services/feed_refresh.py), which
    has stored validators from the previous poll. The import paths keep using
    fetch_and_parse() — a first fetch has nothing to be conditional on.

    Returns the validators from the response so the caller can store them for
    next time. A server that omits them simply gets an unconditional fetch on
    the following poll; the refresh path falls back to comparing article counts
    to decide whether anything actually changed.
    """
    # Local import for the same circular-import reason as fetch_and_parse below.
    from services.feed_discovery import (
        MAX_FEED_BYTES,
        fetch_with_cap_response,
        ssrf_safe_client,
        user_agent,
        validate_fetch_url,
    )

    headers = {"User-Agent": user_agent()}
    conditional: dict[str, str] = {}
    if etag:
        conditional["If-None-Match"] = etag
    if last_modified:
        conditional["If-Modified-Since"] = last_modified

    current_url = await validate_fetch_url(url, timeout=timeout)
    async with ssrf_safe_client(follow_redirects=False, timeout=timeout, headers=headers) as client:
        resp = await fetch_with_cap_response(
            client,
            current_url,
            MAX_FEED_BYTES,
            max_redirects=max_redirects,
            extra_headers=conditional or None,
        )

    if resp.not_modified:
        # Keep the previously stored validators when the 304 omits them —
        # dropping them would make every subsequent poll unconditional again.
        return ConditionalFetch(
            not_modified=True,
            parsed=None,
            etag=resp.etag or etag,
            last_modified=resp.last_modified or last_modified,
        )

    return ConditionalFetch(
        not_modified=False,
        parsed=parse_feed(resp.text),
        etag=resp.etag,
        last_modified=resp.last_modified,
    )


async def fetch_and_parse(url: str, timeout: float = 15.0, max_redirects: int = 5) -> ParsedFeed:
    # Local import: services.feed_discovery imports ParsedFeed/parse_feed from
    # this module at load time, so importing it back at module level here
    # would be circular. Delegates the actual HTTP fetch to fetch_with_cap,
    # which follows redirects manually and re-validates each hop (a public
    # URL that 302s to e.g. http://169.254.169.254/ must not bypass the SSRF
    # guard) and caps response bytes read — a bare client.get() has no size
    # limit and would let a validated host exhaust memory via a huge body.
    from services.feed_discovery import (
        MAX_FEED_BYTES,
        fetch_with_cap,
        ssrf_safe_client,
        user_agent,
        validate_fetch_url,
    )

    headers = {"User-Agent": user_agent()}
    current_url = await validate_fetch_url(url, timeout=timeout)
    async with ssrf_safe_client(follow_redirects=False, timeout=timeout, headers=headers) as client:
        text, _ctype = await fetch_with_cap(client, current_url, MAX_FEED_BYTES, max_redirects=max_redirects)
    return parse_feed(text)
