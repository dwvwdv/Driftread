from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from services import link_harvest
from services.link_harvest import (
    HostIndex,
    build_host_index,
    extract_anchor_hosts,
    harvest_due,
    harvest_one,
    is_denied_host,
    normalize_host,
    record_targets,
    select_due_harvest_feeds,
    site_key,
    summarize_harvest,
)
from tests.discovery_fakes import FakeDB

FEED_ID = str(uuid4())
EMPTY_INDEX = HostIndex(feed_hosts=frozenset(), target_hosts={})


# ── normalize_host ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/post", "example.com"),
        ("http://EXAMPLE.com/", "example.com"),
        ("https://www.example.com/x", "example.com"),
        ("https://blog.example.com:8443/x", "blog.example.com"),
        ("https://user:pw@example.com/x", "example.com"),
        ("https://example.com.tw/", "example.com.tw"),
    ],
)
def test_normalize_host_normalizes(url, expected):
    assert normalize_host(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        None, "", "   ",
        "mailto:someone@example.com",
        "javascript:alert(1)",
        "tel:+886123456",
        "data:text/html;base64,AAAA",
        "//example.com/scheme-relative",
        "/relative/path",
        "ftp://example.com/x",
        "https://192.168.0.1/x",          # IP literal
        "https://8.8.8.8/x",
        "https://[::1]/x",
        "https://localhost/x",            # dotless
        "https://intranet/x",
        "https://box.local/x",
        "https://svc.internal/x",
        "https://thing.onion/x",
        "https://a.test/x",
        "https://www.tw/",                # stripping www. would leave a dotless host
    ],
)
def test_normalize_host_rejects(url):
    assert normalize_host(url) is None


def test_normalize_host_rejects_oversized_labels():
    assert normalize_host("https://" + "a" * 64 + ".com/") is None
    assert normalize_host("https://" + ("a" * 60 + ".") * 5 + "com/") is None


# ── site_key / denylist ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "host,expected",
    [
        ("example.com", "example.com"),
        ("blog.example.com", "example.com"),
        ("deep.blog.example.com", "example.com"),
        ("blog.example.com.tw", "example.com.tw"),
        ("someone.substack.com", "someone.substack.com"),
        ("user.github.io", "user.github.io"),
    ],
)
def test_site_key(host, expected):
    assert site_key(host) == expected


@pytest.mark.parametrize(
    "host",
    [
        "facebook.com", "m.facebook.com", "youtube.com", "github.com",
        "gist.github.com", "cdn.imgur.com", "en.wikipedia.org",
        "bit.ly", "medium.com", "substack.com", "wordpress.com",
    ],
)
def test_is_denied_host(host):
    assert is_denied_host(host) is True


@pytest.mark.parametrize(
    "host",
    ["example.com", "blog.example.com", "someone.substack.com", "user.github.io"],
)
def test_is_not_denied_host(host):
    assert is_denied_host(host) is False


def test_platform_apex_denied_but_subdomains_survive():
    """The asymmetry is deliberate: substack.com is a marketing site, but
    someone.substack.com is a real blog with a real feed."""
    assert is_denied_host("substack.com") is True
    assert is_denied_host("a.substack.com") is False
    assert site_key("a.substack.com") != site_key("b.substack.com")


# ── extract_anchor_hosts ─────────────────────────────────────────────────────

def test_extract_anchor_hosts_resolves_relative_and_skips_junk():
    html = """
      <a href="https://other.example.org/post">other</a>
      <a href="/relative">same site</a>
      <a href="mailto:x@y.com">mail</a>
      <a>no href</a>
      <a href="https://third.example.net/">third</a>
    """
    out = extract_anchor_hosts(html, "https://source.example.com/article")
    assert [h for h, _ in out] == [
        "other.example.org", "source.example.com", "third.example.net"
    ]


def test_extract_anchor_hosts_ignores_nofollow():
    """nofollow is a ranking directive, not a crawl directive — and blogroll
    links are frequently nofollowed, so honouring it would discard our best
    signal."""
    html = '<a href="https://friend.example.org/" rel="nofollow">friend</a>'
    assert [h for h, _ in extract_anchor_hosts(html)] == ["friend.example.org"]


def test_extract_anchor_hosts_caps_anchor_count(monkeypatch):
    monkeypatch.setattr(link_harvest, "MAX_ANCHORS_PER_DOC", 5)
    html = "".join(f'<a href="https://h{i}.example.com/">x</a>' for i in range(50))
    assert len(extract_anchor_hosts(html)) == 5


def test_extract_anchor_hosts_truncates_html(monkeypatch):
    """Only the first slice is parsed, so a huge full-text article can't stall the
    worker's event loop."""
    monkeypatch.setattr(link_harvest, "MAX_HARVEST_HTML_BYTES", 60)
    html = "<p>" + "x" * 100 + '</p><a href="https://late.example.com/">late</a>'
    assert extract_anchor_hosts(html) == []


@pytest.mark.parametrize("html", ["", None, "not html at all", "<<<>>>"])
def test_extract_anchor_hosts_survives_junk(html):
    assert extract_anchor_hosts(html) == []


# ── select_due_harvest_feeds ─────────────────────────────────────────────────

def test_select_due_harvest_feeds_matches_the_partial_index():
    db = FakeDB(feeds=[])
    select_due_harvest_feeds(db, 10)
    names = db.op_names("feeds")
    assert names == ["select", "is_", "lte", "order", "limit"]
    ops = dict((name, args) for name, args in db.ops_for("feeds"))
    assert ops["is_"] == ("archived_at", "null")
    assert ops["lte"][0] == "next_harvest_at"
    assert ops["order"] == ("next_harvest_at", ("desc", False))
    assert ops["limit"] == (10,)


def test_select_due_harvest_feeds_excludes_archived_and_not_yet_due():
    db = FakeDB(feeds=[
        {"id": "a", "url": "https://a.com/f", "next_harvest_at": "2020-01-01T00:00:00+00:00",
         "archived_at": None},
        {"id": "b", "url": "https://b.com/f", "next_harvest_at": "2020-01-01T00:00:00+00:00",
         "archived_at": "2021-01-01T00:00:00+00:00"},
        {"id": "c", "url": "https://c.com/f", "next_harvest_at": "2099-01-01T00:00:00+00:00",
         "archived_at": None},
    ])
    assert [f["id"] for f in select_due_harvest_feeds(db, 10)] == ["a"]


# ── build_host_index ─────────────────────────────────────────────────────────

def test_build_host_index_collects_feed_and_target_hosts():
    db = FakeDB(
        feeds=[{"id": "1", "url": "https://feed.example.com/rss",
                "website_url": "https://www.site.example.org/"}],
        discovery_targets=[{"id": "t1", "host": "known.example.net", "status": "pending"}],
    )
    index = build_host_index(db)
    assert index.feed_hosts == frozenset({"feed.example.com", "site.example.org"})
    assert index.target_hosts == {"known.example.net": "t1"}
    assert index.frontier_full is False


def test_build_host_index_flags_a_full_frontier(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_MAX_FRONTIER_SIZE", "1")
    db = FakeDB(
        feeds=[],
        discovery_targets=[
            {"id": "t1", "host": "a.example.com", "status": "pending"},
            {"id": "t2", "host": "b.example.com", "status": "pending"},
            {"id": "t3", "host": "c.example.com", "status": "done"},
        ],
    )
    # Two pending > the ceiling of one; the 'done' row doesn't count.
    assert build_host_index(db).frontier_full is True


# ── record_targets ───────────────────────────────────────────────────────────

def test_record_targets_creates_targets_and_referrers():
    db = FakeDB(discovery_targets=[], discovery_target_referrers=[])
    created, referrers = record_targets(
        db, FEED_ID,
        {"a.example.com": "https://a.example.com/", "b.example.com": "https://b.example.com/"},
        HostIndex(frozenset(), {}),
    )
    assert (created, referrers) == (2, 2)
    assert {r["host"] for r in db.rows("discovery_targets")} == {
        "a.example.com", "b.example.com"
    }
    assert all(r["source"] == "article_link" for r in db.rows("discovery_targets"))
    assert len(db.rows("discovery_target_referrers")) == 2


def test_record_targets_skips_known_host_but_still_records_the_edge():
    """Per-host dedupe must not cost us the distinct-referrer signal: the second
    feed to link somewhere is exactly the evidence we want."""
    db = FakeDB(
        discovery_targets=[{"id": "t1", "host": "known.example.com",
                            "url": "https://known.example.com/", "status": "pending"}],
        discovery_target_referrers=[],
    )
    created, referrers = record_targets(
        db, FEED_ID, {"known.example.com": "https://known.example.com/"},
        HostIndex(frozenset(), {"known.example.com": "t1"}),
    )
    assert (created, referrers) == (0, 1)
    assert len(db.rows("discovery_targets")) == 1
    assert db.rows("discovery_target_referrers")[0]["target_id"] == "t1"


def test_record_targets_never_resurrects_a_rejected_host():
    """A rejected host keeps its frontier row, and record_targets skips any host
    already present — which is what makes the rejection permanent without a
    separate blocklist table."""
    db = FakeDB(
        discovery_targets=[{"id": "t1", "host": "banned.example.com",
                            "url": "https://banned.example.com/", "status": "rejected"}],
        discovery_target_referrers=[],
    )
    created, _ = record_targets(
        db, FEED_ID, {"banned.example.com": "https://banned.example.com/"},
        HostIndex(frozenset(), {"banned.example.com": "t1"}),
    )
    assert created == 0
    assert db.rows("discovery_targets")[0]["status"] == "rejected"


def test_record_targets_referrer_payload_omits_first_seen_at():
    """Leaving first_seen_at out of the payload keeps it out of the ON CONFLICT
    set-list, so a re-harvest can't rewrite when we first saw the edge."""
    db = FakeDB(discovery_targets=[], discovery_target_referrers=[])
    record_targets(db, FEED_ID, {"a.example.com": "https://a.example.com/"},
                   HostIndex(frozenset(), {}))
    table, rows, on_conflict = db.upserts[0]
    assert table == "discovery_target_referrers"
    assert on_conflict == "target_id,feed_id"
    assert set(rows[0]) == {"target_id", "feed_id"}


def test_record_targets_is_idempotent_across_reharvests():
    db = FakeDB(discovery_targets=[], discovery_target_referrers=[])
    index = HostIndex(frozenset(), {})
    hosts = {"a.example.com": "https://a.example.com/"}
    record_targets(db, FEED_ID, hosts, index)
    # Same index object, as harvest_due shares one per cycle.
    second = record_targets(db, FEED_ID, hosts, index)
    assert second[0] == 0
    assert len(db.rows("discovery_targets")) == 1
    assert len(db.rows("discovery_target_referrers")) == 1


def test_record_targets_creates_nothing_when_frontier_is_full():
    db = FakeDB(discovery_targets=[], discovery_target_referrers=[])
    created, referrers = record_targets(
        db, FEED_ID, {"new.example.com": "https://new.example.com/"},
        HostIndex(frozenset(), {}, frontier_full=True),
    )
    assert (created, referrers) == (0, 0)
    assert db.rows("discovery_targets") == []


def test_record_targets_with_full_frontier_still_records_known_edges():
    db = FakeDB(
        discovery_targets=[{"id": "t1", "host": "known.example.com", "status": "pending"}],
        discovery_target_referrers=[],
    )
    created, referrers = record_targets(
        db, FEED_ID, {"known.example.com": "https://known.example.com/"},
        HostIndex(frozenset(), {"known.example.com": "t1"}, frontier_full=True),
    )
    assert (created, referrers) == (0, 1)


def test_record_targets_without_a_feed_records_no_edge():
    """Directory sources have no owning feed, so there is no distinct-feed edge —
    the target row itself is the whole signal."""
    db = FakeDB(discovery_targets=[])
    created, referrers = record_targets(
        db, None, {"a.example.com": "https://a.example.com/"},
        HostIndex(frozenset(), {}), source="directory",
    )
    assert (created, referrers) == (1, 0)
    assert db.rows("discovery_targets")[0]["source"] == "directory"


# ── harvest_one ──────────────────────────────────────────────────────────────

def _db_with_articles(*html: str, feed=None, targets=None) -> FakeDB:
    return FakeDB(
        feeds=[feed or {"id": FEED_ID, "url": "https://source.example.com/rss",
                        "website_url": "https://source.example.com/",
                        "next_harvest_at": "2020-01-01T00:00:00+00:00",
                        "archived_at": None}],
        articles=[
            {"feed_id": FEED_ID, "url": f"https://source.example.com/p{i}",
             "content": body, "summary": None,
             "fetched_at": f"2026-01-{i + 1:02d}T00:00:00+00:00"}
            for i, body in enumerate(html)
        ],
        discovery_targets=targets if targets is not None else [],
        discovery_target_referrers=[],
    )


@pytest.mark.asyncio
async def test_harvest_one_makes_no_network_request():
    """The article path reads only cached content. Any outbound client here is a
    bug — blogroll fetching is a separate, opt-in stage."""
    db = _db_with_articles('<a href="https://found.example.org/">x</a>')
    with patch("httpx.AsyncClient", side_effect=AssertionError("network!")):
        result = await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert result.hosts_kept == 1
    assert result.blogroll_fetched is False


@pytest.mark.asyncio
async def test_harvest_one_excludes_self_hosts():
    """Both the feed URL's host and the website_url's host — a feed served from
    feeds.example.com for a site at example.com is ordinary."""
    db = _db_with_articles(
        '<a href="https://source.example.com/other">self via website</a>'
        '<a href="https://feeds.example.net/x">self via feed url</a>'
        '<a href="https://real.example.org/">keep</a>',
        feed={"id": FEED_ID, "url": "https://feeds.example.net/rss",
              "website_url": "https://source.example.com/"},
    )
    result = await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert {r["host"] for r in db.rows("discovery_targets")} == {"real.example.org"}
    assert result.hosts_kept == 1


@pytest.mark.asyncio
async def test_harvest_one_excludes_hosts_already_in_feeds():
    db = _db_with_articles('<a href="https://already.example.org/">x</a>')
    index = HostIndex(feed_hosts=frozenset({"already.example.org"}), target_hosts={})
    result = await harvest_one(db, db.rows("feeds")[0], index)
    assert result.hosts_kept == 0
    assert db.rows("discovery_targets") == []


@pytest.mark.asyncio
async def test_harvest_one_excludes_denylisted_hosts():
    db = _db_with_articles(
        '<a href="https://twitter.com/someone">tweet</a>'
        '<a href="https://github.com/x/y">repo</a>'
        '<a href="https://good.example.org/">good</a>'
    )
    await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert {r["host"] for r in db.rows("discovery_targets")} == {"good.example.org"}


@pytest.mark.asyncio
async def test_harvest_one_stores_the_origin_not_the_deep_link():
    db = _db_with_articles('<a href="https://blog.example.org/deep/post?x=1#f">x</a>')
    await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert db.rows("discovery_targets")[0]["url"] == "https://blog.example.org/"


@pytest.mark.asyncio
async def test_harvest_one_caps_hosts_per_feed(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_HARVEST_MAX_LINKS_PER_FEED", "3")
    html = "".join(f'<a href="https://h{i}.example.org/">x</a>' for i in range(20))
    db = _db_with_articles(html)
    result = await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert result.hosts_kept == 3
    assert len(db.rows("discovery_targets")) == 3


@pytest.mark.asyncio
async def test_harvest_one_orders_articles_by_fetched_at():
    """published_at is nullable and Postgres sorts NULLS FIRST on DESC, so
    ordering by it would systematically mine only the undated articles."""
    db = _db_with_articles("<a href='https://x.example.org/'>x</a>")
    await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    orders = [args for name, args in db.ops_for("articles") if name == "order"]
    assert orders == [("fetched_at", ("desc", True))]


@pytest.mark.asyncio
async def test_harvest_one_respects_the_article_limit(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_HARVEST_ARTICLES", "2")
    db = _db_with_articles(
        '<a href="https://a.example.org/">a</a>',
        '<a href="https://b.example.org/">b</a>',
        '<a href="https://c.example.org/">c</a>',
    )
    result = await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert result.articles_scanned == 2


@pytest.mark.asyncio
async def test_harvest_one_falls_back_to_summary_when_content_is_null():
    db = FakeDB(
        feeds=[{"id": FEED_ID, "url": "https://source.example.com/rss"}],
        articles=[{"feed_id": FEED_ID, "url": "https://source.example.com/p",
                   "content": None,
                   "summary": '<a href="https://from-summary.example.org/">x</a>',
                   "fetched_at": "2026-01-01T00:00:00+00:00"}],
        discovery_targets=[], discovery_target_referrers=[],
    )
    await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert {r["host"] for r in db.rows("discovery_targets")} == {"from-summary.example.org"}


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "", "not html", "<<<garbage"])
async def test_harvest_one_survives_junk_content_and_still_reschedules(content):
    db = FakeDB(
        feeds=[{"id": FEED_ID, "url": "https://source.example.com/rss"}],
        articles=[{"feed_id": FEED_ID, "url": "https://source.example.com/p",
                   "content": content, "summary": None,
                   "fetched_at": "2026-01-01T00:00:00+00:00"}],
        discovery_targets=[], discovery_target_referrers=[],
    )
    result = await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert result.error is None
    assert db.rows("feeds")[0]["next_harvest_at"] > "2026-07-01"


@pytest.mark.asyncio
async def test_harvest_one_reschedules_even_when_the_query_explodes():
    """One broken feed must not wedge the head of the due queue forever."""
    db = FakeDB(feeds=[{"id": FEED_ID, "url": "https://source.example.com/rss"}],
                articles=[], discovery_targets=[], discovery_target_referrers=[])

    original = db.table

    def exploding(name):
        if name == "articles":
            raise RuntimeError("boom")
        return original(name)

    with patch.object(db, "table", side_effect=exploding):
        result = await harvest_one(db, {"id": FEED_ID, "url": "https://source.example.com/rss"},
                                   EMPTY_INDEX)

    assert result.error == "boom"
    assert db.rows("feeds")[0]["next_harvest_at"] is not None


# ── blogroll stage ───────────────────────────────────────────────────────────

_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(handler):
    def factory(*args, **kwargs):
        kwargs.pop("follow_redirects", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return factory


@pytest.mark.asyncio
async def test_blogroll_disabled_by_default_makes_no_request():
    db = _db_with_articles("<p>no links</p>")
    with patch("httpx.AsyncClient", side_effect=AssertionError("network!")):
        result = await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)
    assert result.blogroll_fetched is False


@pytest.mark.asyncio
async def test_blogroll_enabled_harvests_homepage_links(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_BLOGROLL_ENABLED", "true")
    db = _db_with_articles("<p>no links in articles</p>")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='<a href="https://friend.example.org/">friend</a>',
            headers={"content-type": "text/html"},
        )

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        result = await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)

    assert result.blogroll_fetched is True
    assert {r["host"] for r in db.rows("discovery_targets")} == {"friend.example.org"}


@pytest.mark.asyncio
async def test_blogroll_failure_does_not_lose_article_hosts(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_BLOGROLL_ENABLED", "true")
    db = _db_with_articles('<a href="https://from-article.example.org/">x</a>')

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        result = await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX)

    assert result.error is None
    assert result.blogroll_fetched is False
    assert {r["host"] for r in db.rows("discovery_targets")} == {"from-article.example.org"}


@pytest.mark.asyncio
async def test_blogroll_passes_the_crawl_policy_gate(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_BLOGROLL_ENABLED", "true")
    db = _db_with_articles("<p>none</p>")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="", headers={"content-type": "text/html"})

    async def deny(url: str) -> bool:
        return False

    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        await harvest_one(db, db.rows("feeds")[0], EMPTY_INDEX, allow_url=deny)

    assert requested == []


# ── harvest_due / summarize ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_harvest_due_shares_one_index_so_hosts_are_not_double_inserted():
    """Two feeds linking the same new host must produce one target and two
    referrer edges — which only works if the index is shared across the batch."""
    feed_a, feed_b = str(uuid4()), str(uuid4())
    db = FakeDB(
        feeds=[
            {"id": feed_a, "url": "https://a.example.com/rss", "website_url": None,
             "next_harvest_at": "2020-01-01T00:00:00+00:00", "archived_at": None},
            {"id": feed_b, "url": "https://b.example.com/rss", "website_url": None,
             "next_harvest_at": "2020-01-01T00:00:00+00:00", "archived_at": None},
        ],
        articles=[
            {"feed_id": feed_a, "url": "https://a.example.com/p", "summary": None,
             "content": '<a href="https://shared.example.org/">x</a>',
             "fetched_at": "2026-01-01T00:00:00+00:00"},
            {"feed_id": feed_b, "url": "https://b.example.com/p", "summary": None,
             "content": '<a href="https://shared.example.org/">x</a>',
             "fetched_at": "2026-01-01T00:00:00+00:00"},
        ],
        discovery_targets=[], discovery_target_referrers=[],
    )
    results = await harvest_due(db)
    assert len(results) == 2
    assert len(db.rows("discovery_targets")) == 1
    assert len(db.rows("discovery_target_referrers")) == 2


@pytest.mark.asyncio
async def test_harvest_due_returns_empty_without_touching_the_index():
    db = FakeDB(feeds=[])
    assert await harvest_due(db) == []
    # No discovery_targets table declared, so building an index would raise.


@pytest.mark.asyncio
async def test_harvest_due_respects_the_batch_size(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_HARVEST_BATCH_SIZE", "1")
    db = FakeDB(
        feeds=[
            {"id": str(uuid4()), "url": f"https://h{i}.example.com/rss",
             "website_url": None, "next_harvest_at": "2020-01-01T00:00:00+00:00",
             "archived_at": None}
            for i in range(3)
        ],
        articles=[], discovery_targets=[], discovery_target_referrers=[],
    )
    assert len(await harvest_due(db)) == 1


def test_summarize_harvest_totals():
    from services.link_harvest import HarvestResult

    results = [
        HarvestResult("a", articles_scanned=3, anchors_seen=10, hosts_kept=2,
                      targets_created=2, referrers_recorded=2),
        HarvestResult("b", articles_scanned=1, anchors_seen=1, error="boom"),
    ]
    assert summarize_harvest(results) == {
        "processed": 2, "articles_scanned": 4, "anchors_seen": 11,
        "hosts_kept": 2, "targets_created": 2, "referrers_recorded": 2, "failed": 1,
    }
