from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from services import directory_sources
from services.directory_sources import (
    extract_opml_feed_urls,
    harvest_source_one,
    harvest_sources_due,
    load_default_sources,
    record_feed_targets,
    select_due_sources,
    summarize_sources,
)
from services.feed_discovery import DiscoveryError
from services.link_harvest import HostIndex
from tests.discovery_fakes import FakeDB

EMPTY_INDEX = HostIndex(feed_hosts=frozenset(), target_hosts={})

_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(handler):
    def factory(*args, **kwargs):
        kwargs.pop("follow_redirects", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)

    return factory


def _responding(body: str, content_type: str = "text/html"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": content_type})

    return handler


def _source(kind: str = "links_page", **overrides) -> dict:
    row = {
        "id": str(uuid4()),
        "url": "https://directory.example.com/list",
        "kind": kind,
        "enabled": True,
        "interval_hours": 168,
        "next_harvest_at": "2020-01-01T00:00:00+00:00",
        "attempts": 0,
        "targets_created": 0,
    }
    row.update(overrides)
    return row


async def _harvest(db, source, index=None, handler=None, allow_url=None):
    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(handler)),
    ):
        return await harvest_source_one(
            db, source, index or EMPTY_INDEX, allow_url=allow_url
        )


# ── OPML parsing ─────────────────────────────────────────────────────────────

def test_extract_opml_feed_urls_walks_nested_outlines():
    xml = """<opml version="2.0"><body>
      <outline text="Tech">
        <outline type="rss" xmlUrl="https://a.example.com/feed"/>
        <outline text="Nested">
          <outline type="rss" xmlUrl="https://b.example.com/feed"/>
        </outline>
      </outline>
      <outline text="folder with no xmlUrl"/>
      <outline type="rss" xmlUrl="https://c.example.com/feed"/>
    </body></opml>"""
    assert extract_opml_feed_urls(xml) == [
        "https://a.example.com/feed",
        "https://b.example.com/feed",
        "https://c.example.com/feed",
    ]


def test_extract_opml_feed_urls_dedupes_and_ignores_blanks():
    xml = """<opml><body>
      <outline xmlUrl="https://a.example.com/feed"/>
      <outline xmlUrl="https://a.example.com/feed"/>
      <outline xmlUrl="   "/>
      <outline/>
    </body></opml>"""
    assert extract_opml_feed_urls(xml) == ["https://a.example.com/feed"]


def test_extract_opml_feed_urls_rejects_malformed_xml():
    with pytest.raises(DiscoveryError):
        extract_opml_feed_urls("<opml><body>")


def test_extract_opml_feed_urls_rejects_entity_expansion():
    """The bytes come from a third party we don't control, so the same
    billion-laughs hardening routers/opml.py applies to uploads is needed here."""
    xml = """<?xml version="1.0"?>
    <!DOCTYPE opml [
      <!ENTITY a "aaaaaaaaaa">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
    ]>
    <opml><body><outline xmlUrl="&b;"/></body></opml>"""
    with pytest.raises(DiscoveryError):
        extract_opml_feed_urls(xml)


def test_extract_opml_feed_urls_rejects_external_entity():
    xml = """<?xml version="1.0"?>
    <!DOCTYPE opml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <opml><body><outline xmlUrl="&xxe;"/></body></opml>"""
    with pytest.raises(DiscoveryError):
        extract_opml_feed_urls(xml)


# ── record_feed_targets ──────────────────────────────────────────────────────

def test_record_feed_targets_keeps_several_feeds_on_one_host():
    """The reason discovery_targets is UNIQUE on url rather than host: a
    directory can legitimately list many feeds from the same publisher."""
    db = FakeDB(discovery_targets=[])
    created = record_feed_targets(
        db,
        ["https://pub.example.com/a/feed", "https://pub.example.com/b/feed"],
        HostIndex(frozenset(), {}),
    )
    assert created == 2
    assert {r["url"] for r in db.rows("discovery_targets")} == {
        "https://pub.example.com/a/feed", "https://pub.example.com/b/feed"
    }
    assert all(r["source"] == "opml" for r in db.rows("discovery_targets"))


def test_record_feed_targets_skips_urls_already_in_the_frontier():
    db = FakeDB(discovery_targets=[])
    created = record_feed_targets(
        db, ["https://a.example.com/feed"],
        HostIndex(frozenset(), {}, target_urls=frozenset({"https://a.example.com/feed"})),
    )
    assert created == 0


def test_record_feed_targets_skips_hosts_we_already_carry():
    db = FakeDB(discovery_targets=[])
    created = record_feed_targets(
        db, ["https://known.example.com/feed"],
        HostIndex(frozenset({"known.example.com"}), {}),
    )
    assert created == 0


def test_record_feed_targets_refuses_a_rejected_host_under_a_new_url():
    """Host dedupe doesn't apply on this path, so the rejection has to be
    enforced explicitly — otherwise a directory listing walks a banned site right
    back in under a different URL."""
    db = FakeDB(discovery_targets=[])
    created = record_feed_targets(
        db, ["https://banned.example.com/some/other/feed"],
        HostIndex(frozenset(), {}, blocked_hosts=frozenset({"banned.example.com"})),
    )
    assert created == 0
    assert db.rows("discovery_targets") == []


@pytest.mark.parametrize(
    "url",
    ["https://twitter.com/x/feed", "https://192.168.0.9/feed",
     "mailto:me@example.com", "https://box.local/feed"],
)
def test_record_feed_targets_applies_normalization_and_denylist(url):
    db = FakeDB(discovery_targets=[])
    assert record_feed_targets(db, [url], HostIndex(frozenset(), {})) == 0


def test_record_feed_targets_caps_per_source(monkeypatch):
    monkeypatch.setattr(directory_sources, "MAX_OPML_FEEDS_PER_SOURCE", 3)
    db = FakeDB(discovery_targets=[])
    urls = [f"https://h{i}.example.com/feed" for i in range(20)]
    assert record_feed_targets(db, urls, HostIndex(frozenset(), {})) == 3


def test_record_feed_targets_creates_nothing_when_frontier_is_full():
    db = FakeDB(discovery_targets=[])
    created = record_feed_targets(
        db, ["https://a.example.com/feed"],
        HostIndex(frozenset(), {}, frontier_full=True),
    )
    assert created == 0


# ── select_due_sources ───────────────────────────────────────────────────────

def test_select_due_sources_matches_the_partial_index():
    db = FakeDB(discovery_sources=[])
    select_due_sources(db, 3)
    assert db.op_names("discovery_sources") == [
        "select", "is_", "lte", "order", "limit"
    ]
    ops = dict(db.ops_for("discovery_sources"))
    assert ops["is_"] == ("enabled", True)
    assert ops["lte"][0] == "next_harvest_at"


def test_select_due_sources_skips_disabled_and_not_yet_due():
    db = FakeDB(discovery_sources=[
        _source(id="a", enabled=True, next_harvest_at="2020-01-01T00:00:00+00:00"),
        _source(id="b", enabled=False, next_harvest_at="2020-01-01T00:00:00+00:00"),
        _source(id="c", enabled=True, next_harvest_at="2099-01-01T00:00:00+00:00"),
    ])
    assert [s["id"] for s in select_due_sources(db, 10)] == ["a"]


# ── load_default_sources ─────────────────────────────────────────────────────

def test_load_default_sources_is_idempotent():
    db = FakeDB(discovery_sources=[])
    first = load_default_sources(db)
    assert first > 0
    before = len(db.rows("discovery_sources"))
    load_default_sources(db)
    assert len(db.rows("discovery_sources")) == before


def test_load_default_sources_does_not_reset_operator_settings():
    """Re-seeding must not silently re-enable a source an admin turned off, so
    the payload carries no `enabled` or `interval_hours`."""
    db = FakeDB(discovery_sources=[])
    load_default_sources(db)
    row = db.rows("discovery_sources")[0]
    row["enabled"] = False
    row["interval_hours"] = 999
    load_default_sources(db)
    assert row["enabled"] is False
    assert row["interval_hours"] == 999


def test_load_default_sources_uses_upsert_on_url():
    db = FakeDB(discovery_sources=[])
    load_default_sources(db)
    _table, _rows, on_conflict = db.upserts[0]
    assert on_conflict == "url"


def test_default_seed_file_is_well_formed():
    entries = json.loads(directory_sources.SEEDS_PATH.read_text(encoding="utf-8"))
    assert entries
    for entry in entries:
        assert entry["kind"] in ("links_page", "opml")
        assert entry["url"].startswith("https://")


def test_load_default_sources_survives_a_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(directory_sources, "SEEDS_PATH", tmp_path / "nope.json")
    db = FakeDB(discovery_sources=[])
    assert load_default_sources(db) == 0


def test_load_default_sources_skips_invalid_entries(monkeypatch, tmp_path):
    path = tmp_path / "seeds.json"
    path.write_text(json.dumps([
        {"url": "https://ok.example.com/", "kind": "opml"},
        {"url": "", "kind": "opml"},
        {"url": "https://bad.example.com/", "kind": "scrape_everything"},
    ]), encoding="utf-8")
    monkeypatch.setattr(directory_sources, "SEEDS_PATH", path)
    db = FakeDB(discovery_sources=[])
    assert load_default_sources(db) == 1


# ── harvest_source_one ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_links_page_source_creates_host_targets():
    db = FakeDB(discovery_sources=[_source()], discovery_targets=[])
    source = db.rows("discovery_sources")[0]
    handler = _responding(
        '<a href="https://blog-a.example.org/">a</a>'
        '<a href="https://blog-b.example.org/">b</a>'
        '<a href="https://github.com/x">denied</a>'
    )
    result = await _harvest(db, source, handler=handler)

    assert result.targets_created == 2
    assert {r["host"] for r in db.rows("discovery_targets")} == {
        "blog-a.example.org", "blog-b.example.org"
    }
    assert all(r["source"] == "directory" for r in db.rows("discovery_targets"))


@pytest.mark.asyncio
async def test_links_page_source_records_no_referrer_edge():
    """A directory has no owning feed, so there is nothing to count as a distinct
    referrer — the FakeDB would raise on an undeclared table if we tried."""
    db = FakeDB(discovery_sources=[_source()], discovery_targets=[])
    handler = _responding('<a href="https://blog.example.org/">a</a>')
    result = await _harvest(db, db.rows("discovery_sources")[0], handler=handler)
    assert result.error is None


@pytest.mark.asyncio
async def test_opml_source_creates_feed_targets():
    db = FakeDB(discovery_sources=[_source("opml")], discovery_targets=[])
    xml = """<opml><body>
      <outline xmlUrl="https://a.example.org/feed"/>
      <outline xmlUrl="https://b.example.org/atom"/>
    </body></opml>"""
    result = await _harvest(
        db, db.rows("discovery_sources")[0],
        handler=_responding(xml, "application/xml"),
    )
    assert result.feed_targets_created == 2
    assert {r["source"] for r in db.rows("discovery_targets")} == {"opml"}
    assert {r["url"] for r in db.rows("discovery_targets")} == {
        "https://a.example.org/feed", "https://b.example.org/atom"
    }


@pytest.mark.asyncio
async def test_source_fetch_failure_is_recorded_and_backed_off():
    db = FakeDB(discovery_sources=[_source()], discovery_targets=[])
    source = db.rows("discovery_sources")[0]

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    result = await _harvest(db, source, handler=handler)

    assert result.error is not None
    assert result.targets_created == 0
    assert source["attempts"] == 1
    assert source["last_failure_reason"]
    assert source["next_harvest_at"] > "2026-07-01"


@pytest.mark.asyncio
async def test_malformed_opml_is_a_recorded_failure_not_a_crash():
    db = FakeDB(discovery_sources=[_source("opml")], discovery_targets=[])
    result = await _harvest(
        db, db.rows("discovery_sources")[0],
        handler=_responding("<opml><body>", "application/xml"),
    )
    assert result.error is not None
    assert "Invalid OPML" in result.error


@pytest.mark.asyncio
async def test_success_clears_failure_state_and_counts_targets():
    db = FakeDB(
        discovery_sources=[_source(attempts=2, last_failure_reason="earlier boom")],
        discovery_targets=[],
    )
    source = db.rows("discovery_sources")[0]
    await _harvest(db, source, handler=_responding('<a href="https://x.example.org/">x</a>'))

    assert source["attempts"] == 0
    assert source["last_failure_reason"] is None
    assert source["targets_created"] == 1


@pytest.mark.asyncio
async def test_source_fetch_passes_the_crawl_policy_gate():
    db = FakeDB(discovery_sources=[_source()], discovery_targets=[])
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="", headers={"content-type": "text/html"})

    async def deny(url: str) -> bool:
        return False

    result = await _harvest(db, db.rows("discovery_sources")[0], handler=handler,
                            allow_url=deny)
    assert requested == []
    assert result.error is not None


@pytest.mark.asyncio
async def test_source_fetch_goes_through_the_ssrf_gate():
    db = FakeDB(discovery_sources=[_source(url="https://10.0.0.5/list")],
                discovery_targets=[])
    with (
        patch("services.feed_discovery._is_safe_host", return_value=False),
        patch("httpx.AsyncClient", new=_mock_client_factory(_responding(""))),
    ):
        result = await harvest_source_one(db, db.rows("discovery_sources")[0], EMPTY_INDEX)
    assert result.error is not None
    assert db.rows("discovery_targets") == []


# ── harvest_sources_due / summarize ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_harvest_sources_due_respects_the_batch_size(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_DIRECTORY_BATCH_SIZE", "1")
    db = FakeDB(
        discovery_sources=[_source(), _source(), _source()],
        discovery_targets=[],
    )
    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient", new=_mock_client_factory(_responding(""))),
    ):
        results = await harvest_sources_due(db, EMPTY_INDEX)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_harvest_sources_due_shares_the_index_across_sources():
    """Two directories listing the same site must produce one target."""
    db = FakeDB(
        discovery_sources=[_source(url="https://d1.example.com/"),
                           _source(url="https://d2.example.com/")],
        discovery_targets=[],
    )
    index = HostIndex(frozenset(), {})
    with (
        patch("services.feed_discovery._is_safe_host", return_value=True),
        patch("httpx.AsyncClient",
              new=_mock_client_factory(_responding('<a href="https://shared.example.org/">x</a>'))),
    ):
        await harvest_sources_due(db, index)
    assert len(db.rows("discovery_targets")) == 1


@pytest.mark.asyncio
async def test_harvest_sources_due_returns_empty_when_nothing_is_due():
    db = FakeDB(discovery_sources=[])
    assert await harvest_sources_due(db, EMPTY_INDEX) == []


def test_summarize_sources_totals():
    from services.directory_sources import SourceHarvestResult

    results = [
        SourceHarvestResult("1", "u", "links_page", targets_created=3),
        SourceHarvestResult("2", "u", "opml", feed_targets_created=5),
        SourceHarvestResult("3", "u", "opml", error="boom"),
    ]
    assert summarize_sources(results) == {
        "processed": 3, "targets_created": 3, "feed_targets_created": 5, "failed": 1,
    }


@pytest.mark.asyncio
async def test_shipped_default_sources_are_not_blocked_by_the_gate():
    """Regression: the shipped defaults are hosted on github.com, which is on the
    target denylist. Handing the directory fetch a denylist-applying gate made
    every one of them fail with "Blocked by crawl policy" and reschedule forever.
    The denylist is about hosts worth cataloguing, not about where we read a list
    from."""
    import json

    from services.crawl_policy import make_gate

    gate = make_gate("Driftread/1.0", apply_denylist=False)
    entries = json.loads(directory_sources.SEEDS_PATH.read_text(encoding="utf-8"))

    with patch("services.crawl_policy.respect_robots", return_value=False):
        for entry in entries:
            assert await gate(entry["url"]) is True, entry["url"]


@pytest.mark.asyncio
async def test_a_github_hosted_directory_can_actually_be_harvested():
    """End to end through harvest_source_one with the gate the cycle really uses."""
    from services.crawl_policy import make_gate

    db = FakeDB(
        discovery_sources=[_source(url="https://github.com/someone/awesome-rss")],
        discovery_targets=[],
    )
    handler = _responding('<a href="https://found.example.org/">a blog</a>')

    with patch("services.crawl_policy.respect_robots", return_value=False):
        result = await _harvest(
            db, db.rows("discovery_sources")[0], handler=handler,
            allow_url=make_gate("Driftread/1.0", apply_denylist=False),
        )

    assert result.error is None
    assert result.targets_created == 1
    # ...while the links it yields are still filtered normally.
    assert {r["host"] for r in db.rows("discovery_targets")} == {"found.example.org"}


@pytest.mark.asyncio
async def test_links_extracted_from_a_directory_still_respect_the_denylist():
    from services.crawl_policy import make_gate

    db = FakeDB(discovery_sources=[_source()], discovery_targets=[])
    handler = _responding(
        '<a href="https://github.com/x/y">code</a>'
        '<a href="https://twitter.com/someone">social</a>'
        '<a href="https://real.example.org/">a blog</a>'
    )

    with patch("services.crawl_policy.respect_robots", return_value=False):
        await _harvest(
            db, db.rows("discovery_sources")[0], handler=handler,
            allow_url=make_gate("Driftread/1.0", apply_denylist=False),
        )

    assert {r["host"] for r in db.rows("discovery_targets")} == {"real.example.org"}
