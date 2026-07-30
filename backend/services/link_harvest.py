"""Stage one of autonomous discovery: mine our own corpus for hosts worth probing.

The article path makes **zero network requests** — it reads `articles.content`,
which the refresh worker already fetched and cached. That is what makes the loop
compound: more feeds means more articles, which means more outbound links, which
means more candidate hosts, which (once approved) means more feeds.

Two harvest sources live here:

- every unarchived feed's recent articles (always on);
- optionally each feed's `website_url` homepage, for the blogroll / "友情連結"
  block that independent blogs still keep. That one costs a request per feed, so
  it is behind FEED_DISCOVERY_BLOGROLL_ENABLED and off by default.

Hosts land in `discovery_targets`; who linked to them lands in
`discovery_target_referrers`, whose PRIMARY KEY is what makes "how many DISTINCT
feeds link here" idempotent under re-harvesting.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, SoupStrainer

from services.discovery_config import (
    blogroll_enabled,
    harvest_articles_per_feed,
    harvest_batch_size,
    harvest_interval_hours,
    harvest_max_links_per_feed,
    max_frontier_size,
)
from services.feed_discovery import (
    MAX_FEED_BYTES,
    AllowUrl,
    DiscoveryError,
    fetch_with_cap,
    user_agent,
)

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

# Anchors parsed out of one document. A link-farm page can carry tens of
# thousands; we only need enough to spot the interesting outbound hosts.
MAX_ANCHORS_PER_DOC = 500

# Slice of articles.content actually handed to BeautifulSoup. Full-text articles
# can be large, and this runs on the worker's event loop.
MAX_HARVEST_HTML_BYTES = 512 * 1024

# Rows read per page when building the host index.
_INDEX_PAGE_SIZE = 1000

# Hosts that are never a blog. Suffix-matched, so `cdn.imgur.com` is caught by
# `imgur.com`. Not exhaustive and not meant to be — it removes the bulk of the
# noise so the probe budget goes somewhere useful, and a miss only costs one
# wasted probe that ends as status='done', feeds_found=0.
DENY_HOST_SUFFIXES: frozenset[str] = frozenset({
    # social / video / messaging
    "facebook.com", "fb.com", "twitter.com", "x.com", "t.co", "instagram.com",
    "threads.net", "linkedin.com", "lnkd.in", "reddit.com", "redd.it",
    "pinterest.com", "tiktok.com", "weibo.com", "vk.com", "bsky.app",
    "mastodon.social", "discord.com", "discord.gg", "t.me", "telegram.me",
    "line.me", "whatsapp.com", "youtube.com", "youtu.be", "vimeo.com",
    "twitch.tv", "plurk.com", "dcard.tw", "ptt.cc",
    # code hosting / package registries / Q&A
    "github.com", "gitlab.com", "bitbucket.org", "sourceforge.net",
    "npmjs.com", "pypi.org", "crates.io", "rubygems.org", "packagist.org",
    "stackoverflow.com", "stackexchange.com", "superuser.com",
    "serverfault.com", "askubuntu.com", "codepen.io", "jsfiddle.net",
    "replit.com", "gist.github.com",
    # reference / archives
    "wikipedia.org", "wikimedia.org", "wiktionary.org", "wikidata.org",
    "archive.org", "archive.today", "doi.org", "arxiv.org",
    # commerce
    "amazon.com", "amazon.co.jp", "amazon.co.uk", "ebay.com", "etsy.com",
    "aliexpress.com", "taobao.com", "jd.com", "shopee.tw", "momoshop.com.tw",
    "pchome.com.tw", "books.com.tw", "apps.apple.com", "play.google.com",
    # link shorteners / syndication relays
    "bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "is.gd",
    "dlvr.it", "ift.tt", "rebrand.ly", "t.ly", "lnk.to",
    # CDNs / assets / analytics
    "gravatar.com", "googleapis.com", "gstatic.com", "cloudflare.com",
    "cloudfront.net", "akamaihd.net", "jsdelivr.net", "unpkg.com",
    "imgur.com", "flickr.com", "giphy.com", "googletagmanager.com",
    "google-analytics.com", "doubleclick.net", "w3.org", "schema.org",
    # search / payments / funding / events / storage
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com", "baidu.com",
    "paypal.com", "patreon.com", "ko-fi.com", "buymeacoffee.com",
    "gofundme.com", "eventbrite.com", "meetup.com", "dropbox.com",
    "notion.so", "docs.google.com",
    # Blogging-platform apexes. The apex itself is a marketing site, but a
    # subdomain on it is a real blog — hence the same names in
    # PLATFORM_HOST_SUFFIXES below. medium.com is denied outright and stays a
    # known miss: medium.com/feed/@user exists, but origin normalization
    # collapses the path away, so we can never reach it from a bare host.
    "substack.com", "blogspot.com", "wordpress.com", "medium.com",
    "tumblr.com", "wixsite.com", "weebly.com",
})

# Hosts where the *subdomain* is the site. For these, site_key() keeps the full
# host instead of collapsing to the registrable domain, so a.substack.com and
# b.substack.com are two distinct sites rather than one.
PLATFORM_HOST_SUFFIXES: frozenset[str] = frozenset({
    "substack.com", "blogspot.com", "wordpress.com", "tumblr.com",
    "github.io", "gitlab.io", "netlify.app", "vercel.app", "pages.dev",
    "hashnode.dev", "bearblog.dev", "micro.blog", "neocities.org",
    "wixsite.com", "weebly.com", "ghost.io", "hatenablog.com", "livedoor.jp",
})

# Two-label public suffixes we care about, so site_key doesn't collapse
# "example.com.tw" to the meaningless "com.tw". Deliberately hand-maintained
# rather than pulled from tldextract: that library fetches the Public Suffix List
# over the network on first use, which a container that must not need extra
# egress cannot rely on — and a wrong site_key here only costs one extra probe or
# one missed dedupe.
MULTI_LABEL_SUFFIXES: frozenset[str] = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk",
    "com.tw", "org.tw", "net.tw", "edu.tw", "gov.tw", "idv.tw",
    "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
    "com.hk", "org.hk", "com.au", "net.au", "org.au", "edu.au",
    "com.br", "com.mx", "com.ar", "co.kr", "or.kr", "co.nz", "co.in",
    "com.sg", "com.my", "co.th", "com.tr", "co.za",
})

# Reserved / non-public TLDs. Rejected before a row is ever written, so the
# frontier can't be seeded with something the SSRF gate would have to catch later.
BAD_TLD_SUFFIXES = (
    ".local", ".internal", ".test", ".invalid", ".localhost", ".onion",
    ".arpa", ".example", ".home", ".lan", ".corp",
)


@dataclass(frozen=True)
class HostIndex:
    """A cycle's worth of "what do we already know", built once from our own rows.

    Building this up front and filtering in memory is also a security choice: no
    third-party-derived host string ever reaches a PostgREST filter, whose
    mini-language treats `,` `(` `)` as syntax (docs/SECURITY.md #14).
    """
    feed_hosts: frozenset[str]
    target_hosts: dict[str, str]  # host -> discovery_targets.id
    frontier_full: bool = False
    # Every frontier URL, for the OPML path: an OPML directory can legitimately
    # contribute several feed URLs on one host, so it dedupes by URL where link
    # mining dedupes by host.
    target_urls: frozenset[str] = frozenset()
    # Hosts an admin rejected, or that a probe found blocked. Link mining skips
    # these implicitly (they're in target_hosts), but the OPML path dedupes by
    # URL and so needs them called out — otherwise a directory listing could walk
    # a rejected host straight back into the frontier under a new URL.
    blocked_hosts: frozenset[str] = frozenset()


@dataclass(frozen=True)
class HarvestResult:
    feed_id: str
    articles_scanned: int = 0
    anchors_seen: int = 0
    hosts_kept: int = 0
    targets_created: int = 0
    referrers_recorded: int = 0
    blogroll_fetched: bool = False
    error: str | None = None


def normalize_host(url: str | None) -> str | None:
    """The comparable host for a URL, or None if the link isn't worth following.

    Rejects (rather than normalizes) anything that can't be a public blog: a
    non-http scheme, an IP literal, a dotless or over-long host, and reserved
    TLDs. Doing this at harvest time keeps junk out of the frontier entirely
    instead of leaving it for the probe's SSRF gate to trip over later.
    """
    if not url:
        return None
    candidate = url.strip()
    if not candidate:
        return None
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    # No scheme-relative or relative URLs: harvesting passes absolute URLs in
    # (extract_anchor_hosts resolves against the document base first).
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    try:
        host = parsed.hostname  # already lowercased, userinfo and port stripped
    except ValueError:
        return None
    if not host:
        return None
    if len(host) > 253:
        return None
    if any(len(label) > 63 for label in host.split(".")):
        return None
    try:
        ipaddress.ip_address(host)
        return None  # a bare IP is never a blog we want to catalogue
    except ValueError:
        pass
    if "." not in host:
        return None
    if host.endswith(BAD_TLD_SUFFIXES):
        return None
    if host.startswith("www."):
        host = host[4:]
        if "." not in host:
            return None
    return host or None


def site_key(host: str) -> str:
    """The unit we treat as "one site", for denylisting and politeness.

    Normally the registrable domain, so blog.example.com and example.com collapse
    together — except on blogging platforms, where the subdomain *is* the site.
    """
    if host.endswith(tuple("." + s for s in PLATFORM_HOST_SUFFIXES)):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _is_platform_subdomain(host: str) -> bool:
    return host.endswith(tuple("." + s for s in PLATFORM_HOST_SUFFIXES))


def is_denied_host(host: str) -> bool:
    if not host:
        return True
    key = site_key(host)

    # An exact hit always denies. This is what blocks the blogging-platform
    # apexes (substack.com, wordpress.com, ...) while leaving their subdomains
    # to the check below.
    if host in DENY_HOST_SUFFIXES or key in DENY_HOST_SUFFIXES:
        return True

    # Suffix matching would otherwise swallow every subdomain of those same
    # platforms — someone.substack.com ends with ".substack.com" — and those are
    # precisely the hosts worth probing, since on a platform the subdomain *is*
    # the site. So for them the exact match above is the only way to be denied.
    if _is_platform_subdomain(host):
        return False

    # Everywhere else, suffix matching catches the subdomains site_key can't
    # collapse on its own: foo.apps.apple.com keys to apple.com, which isn't
    # itself on the list.
    dotted = tuple("." + s for s in DENY_HOST_SUFFIXES)
    return host.endswith(dotted) or key.endswith(dotted)


def extract_anchor_hosts(html: str, base_url: str | None = None) -> list[tuple[str, str]]:
    """(host, absolute_url) for each <a href> in `html`, in document order.

    SoupStrainer keeps this from building a full DOM: a harvest cycle parses
    articles from ten feeds on the worker's event loop, and anchors are all we
    read. `rel="nofollow"` is deliberately ignored — it's a ranking directive,
    not a crawl directive, and blogroll links are frequently nofollowed, so
    honouring it would throw away the best signal we have.
    """
    if not html:
        return []
    soup = BeautifulSoup(
        html[:MAX_HARVEST_HTML_BYTES], "html.parser", parse_only=SoupStrainer("a")
    )
    out: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", limit=MAX_ANCHORS_PER_DOC):
        href = anchor.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href) if base_url else href
        host = normalize_host(absolute)
        if host:
            out.append((host, absolute))
    return out


def select_due_harvest_feeds(db: "Client", limit: int) -> list[dict]:
    """Unarchived feeds whose next_harvest_at has passed, oldest due first.

    Matches feeds_next_harvest_at_idx from migration 006.
    """
    now = datetime.now(timezone.utc).isoformat()
    result = (
        db.table("feeds")
        .select("id,url,website_url")
        .is_("archived_at", "null")
        .lte("next_harvest_at", now)
        .order("next_harvest_at")
        .limit(limit)
        .execute()
    )
    return list(result.data or [])


def _paged(db: "Client", table: str, columns: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            db.table(table)
            .select(columns)
            .range(offset, offset + _INDEX_PAGE_SIZE - 1)
            .execute()
        )
        batch = list(page.data or [])
        rows.extend(batch)
        if len(batch) < _INDEX_PAGE_SIZE:
            return rows
        offset += _INDEX_PAGE_SIZE


def build_host_index(db: "Client") -> HostIndex:
    """Snapshot every host we already know about, once per cycle."""
    feed_hosts: set[str] = set()
    for row in _paged(db, "feeds", "url,website_url"):
        for value in (row.get("url"), row.get("website_url")):
            host = normalize_host(value)
            if host:
                feed_hosts.add(host)

    target_hosts: dict[str, str] = {}
    target_urls: set[str] = set()
    blocked_hosts: set[str] = set()
    for row in _paged(db, "discovery_targets", "id,host,url,status"):
        host = row.get("host")
        if host and host not in target_hosts:
            target_hosts[host] = str(row["id"])
        if row.get("url"):
            target_urls.add(row["url"])
        if host and row.get("status") in ("rejected", "blocked"):
            blocked_hosts.add(host)

    pending = (
        db.table("discovery_targets")
        .select("id", count="exact", head=True)
        .eq("status", "pending")
        .execute()
    )
    pending_count = getattr(pending, "count", None) or 0

    return HostIndex(
        feed_hosts=frozenset(feed_hosts),
        target_hosts=target_hosts,
        frontier_full=pending_count > max_frontier_size(),
        target_urls=frozenset(target_urls),
        blocked_hosts=frozenset(blocked_hosts),
    )


def record_targets(
    db: "Client",
    feed_id: str | None,
    host_urls: dict[str, str],
    index: HostIndex,
    source: str = "article_link",
) -> tuple[int, int]:
    """Write new frontier rows and referrer edges. Returns (created, referrers).

    A host already in the frontier gets no second target — including one an admin
    rejected, which is how a rejection stays permanent without a separate
    blocklist table — but it does still get a referrer edge, so its evidence
    count keeps improving.
    """
    if not host_urls:
        return (0, 0)

    known = index.target_hosts
    fresh = {host: url for host, url in host_urls.items() if host not in known}

    created_ids: dict[str, str] = {}
    if fresh and not index.frontier_full:
        payload = [
            {
                "url": url,
                "host": host,
                "source": source,
                "source_feed_id": feed_id,
            }
            for host, url in fresh.items()
        ]
        result = db.table("discovery_targets").insert(payload).execute()
        for row in result.data or []:
            created_ids[row["host"]] = str(row["id"])
            known[row["host"]] = str(row["id"])

    if feed_id is None:
        # Directory sources have no owning feed, so there is no distinct-feed
        # edge to record — the target row itself is the whole signal.
        return (len(created_ids), 0)

    target_ids = {known[host] for host in host_urls if host in known}
    if not target_ids:
        return (len(created_ids), 0)

    # Only {target_id, feed_id}: omitting first_seen_at keeps it out of the
    # ON CONFLICT DO UPDATE set-list, so re-harvesting can't rewrite when we
    # first saw the edge. That makes the write idempotent by construction rather
    # than by relying on ignore_duplicates, whose support varies by client version.
    rows = [{"target_id": tid, "feed_id": feed_id} for tid in sorted(target_ids)]
    db.table("discovery_target_referrers").upsert(
        rows, on_conflict="target_id,feed_id"
    ).execute()
    return (len(created_ids), len(rows))


def _schedule_next_harvest(db: "Client", feed_id: str) -> None:
    now = datetime.now(timezone.utc)
    db.table("feeds").update(
        {
            "last_harvested_at": now.isoformat(),
            "next_harvest_at": (
                now + timedelta(hours=harvest_interval_hours())
            ).isoformat(),
        }
    ).eq("id", feed_id).execute()


async def _fetch_blogroll(website_url: str, allow_url: AllowUrl | None) -> str | None:
    headers = {
        "User-Agent": user_agent(),
        "Accept": "text/html,application/xhtml+xml,*/*",
    }
    # Reusing MAX_FEED_BYTES rather than introducing an HTML-specific cap:
    # SECURITY.md #22 records a MAX_HTML_BYTES constant that was never wired up
    # and misled readers into thinking a second limit applied.
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=12.0, headers=headers
    ) as client:
        text, _ctype = await fetch_with_cap(
            client, website_url, MAX_FEED_BYTES, allow_url=allow_url
        )
    return text


async def harvest_one(
    db: "Client", feed: dict, index: HostIndex, allow_url: AllowUrl | None = None
) -> HarvestResult:
    """Mine one feed for outbound hosts.

    Never raises for a bad feed — the refresh_one() contract. The schedule is
    advanced whatever happens, so one unparseable article can't wedge a feed at
    the head of the due queue forever.
    """
    feed_id = str(feed["id"])
    max_hosts = harvest_max_links_per_feed()

    self_hosts = {
        host
        for host in (normalize_host(feed.get("url")), normalize_host(feed.get("website_url")))
        if host
    }

    host_urls: dict[str, str] = {}
    anchors_seen = 0
    articles_scanned = 0
    blogroll_fetched = False
    error: str | None = None

    def _absorb(pairs: list[tuple[str, str]]) -> None:
        nonlocal anchors_seen
        for host, absolute in pairs:
            anchors_seen += 1
            if len(host_urls) >= max_hosts:
                return
            if host in self_hosts or host in index.feed_hosts:
                continue
            if is_denied_host(host):
                continue
            if host in host_urls:
                continue
            # Probe the origin, not the deep link that happened to be shared:
            # feed autodiscovery lives at the site root.
            host_urls[host] = f"https://{host}/"

    try:
        articles = (
            db.table("articles")
            # fetched_at, not published_at: published_at is nullable and Postgres
            # sorts NULLS FIRST on DESC, so ordering by it would systematically
            # mine only the undated articles.
            .select("content,summary,url")
            .eq("feed_id", feed_id)
            .order("fetched_at", desc=True)
            .limit(harvest_articles_per_feed())
            .execute()
        )
        for article in list(articles.data or []):
            articles_scanned += 1
            html = article.get("content") or article.get("summary") or ""
            if html:
                _absorb(extract_anchor_hosts(html, article.get("url")))
            # Yield between articles: this is the only CPU-bound work sharing the
            # worker's event loop with the refresh tick. If it ever shows up in a
            # profile, wrap extract_anchor_hosts in asyncio.to_thread.
            await asyncio.sleep(0)

        if blogroll_enabled() and feed.get("website_url"):
            try:
                html = await _fetch_blogroll(feed["website_url"], allow_url)
                blogroll_fetched = True
                if html:
                    _absorb(extract_anchor_hosts(html, feed["website_url"]))
            except (httpx.HTTPError, DiscoveryError, ValueError) as e:
                # A blogroll we can't fetch is not a harvest failure: the article
                # path may well have produced hosts already.
                logger.debug("Blogroll fetch failed for feed %s: %s", feed_id, e)

        created, referrers = record_targets(db, feed_id, host_urls, index)
    except Exception as e:  # noqa: BLE001 - see docstring
        logger.exception("Harvest failed for feed %s", feed_id)
        error = str(e)[:500]
        created = referrers = 0
    finally:
        try:
            _schedule_next_harvest(db, feed_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Could not advance harvest schedule for feed %s", feed_id)

    return HarvestResult(
        feed_id=feed_id,
        articles_scanned=articles_scanned,
        anchors_seen=anchors_seen,
        hosts_kept=len(host_urls),
        targets_created=created,
        referrers_recorded=referrers,
        blogroll_fetched=blogroll_fetched,
        error=error,
    )


async def harvest_due(
    db: "Client", limit: int | None = None, allow_url: AllowUrl | None = None
) -> list[HarvestResult]:
    """Harvest every feed currently due, bounded by `limit`.

    Sequential rather than concurrent: the article path is DB-bound with a batch
    of ten, and the only outbound hop (blogroll) is already spaced by its own
    per-host delay. The HostIndex is built once and shared, so hosts discovered
    earlier in the batch are not re-inserted later in it.
    """
    feeds = select_due_harvest_feeds(db, limit or harvest_batch_size())
    if not feeds:
        return []

    index = build_host_index(db)
    return [await harvest_one(db, feed, index, allow_url) for feed in feeds]


def summarize_harvest(results: list[HarvestResult]) -> dict[str, int]:
    return {
        "processed": len(results),
        "articles_scanned": sum(r.articles_scanned for r in results),
        "anchors_seen": sum(r.anchors_seen for r in results),
        "hosts_kept": sum(r.hosts_kept for r in results),
        "targets_created": sum(r.targets_created for r in results),
        "referrers_recorded": sum(r.referrers_recorded for r in results),
        "failed": sum(1 for r in results if r.error),
    }
