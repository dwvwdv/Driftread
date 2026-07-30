from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch
from uuid import uuid4

import pytest

from services.discovery_candidates import (
    approve_candidate,
    auto_promote_due,
    get_candidate,
    list_candidates,
    promote_approved,
    promote_candidate,
    record_candidates,
    reject_candidate,
    sanitize_http_url,
    sanitize_text,
    stats,
)
from tests.discovery_fakes import FakeDB

TARGET_ID = str(uuid4())


@dataclass
class Found:
    """Stands in for services.feed_discovery.DiscoveryCandidate."""
    feed_url: str
    title: str | None = None
    website_url: str | None = None


def _target(**overrides) -> dict:
    row = {"id": TARGET_ID, "host": "blog.example.org", "referring_feed_count": 2}
    row.update(overrides)
    return row


def _candidate_row(**overrides) -> dict:
    row = {
        "id": str(uuid4()),
        "target_id": TARGET_ID,
        "feed_url": "https://blog.example.org/feed",
        "title": "A Blog",
        "website_url": "https://blog.example.org/",
        "source_host": "blog.example.org",
        "referring_feed_count": 2,
        "status": "pending",
        "feed_id": None,
        "discovered_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


# ── sanitizers ───────────────────────────────────────────────────────────────

def test_sanitize_text_strips_control_and_invisible_characters():
    assert sanitize_text("A\x00B\x1fC") == "ABC"
    assert sanitize_text("hel​lo") == "hello"


def test_sanitize_text_strips_bidi_overrides():
    """A bidi override in a title can visually reverse a domain in the reviewer's
    list, so an approver could be looking at something other than what's stored."""
    assert sanitize_text("safe ‮gro.live‬ site") == "safe gro.live site"


def test_sanitize_text_collapses_whitespace_and_truncates():
    assert sanitize_text("  a\n\n   b  ") == "a b"
    assert len(sanitize_text("x" * 500)) == 200
    assert sanitize_text("y" * 20, 5) == "yyyyy"


@pytest.mark.parametrize("value", [None, "", "   ", "​​", "\x00"])
def test_sanitize_text_empty_becomes_none(value):
    assert sanitize_text(value) is None


@pytest.mark.parametrize(
    "url",
    [None, "", "javascript:alert(1)", "data:text/html,x", "mailto:a@b.com",
     "ftp://example.com/f", "not a url", "https://" + "a" * 3000],
)
def test_sanitize_http_url_rejects(url):
    assert sanitize_http_url(url) is None


def test_sanitize_http_url_accepts_http_and_https():
    assert sanitize_http_url("https://example.com/feed") == "https://example.com/feed"
    assert sanitize_http_url(" http://example.com/f ") == "http://example.com/f"


# ── record_candidates ────────────────────────────────────────────────────────

def test_record_candidates_inserts_pending():
    db = FakeDB(discovery_candidates=[], feeds=[])
    new, seen = record_candidates(
        db, _target(), [Found("https://blog.example.org/feed", "A Blog",
                              "https://blog.example.org/")]
    )
    assert (new, seen) == (1, 0)
    row = db.rows("discovery_candidates")[0]
    assert row["status"] == "pending"
    assert row["source_host"] == "blog.example.org"
    assert row["referring_feed_count"] == 2
    assert row["target_id"] == TARGET_ID


def test_record_candidates_never_resurrects_a_rejected_candidate():
    """The headline guarantee. The same feed keeps being linked from the same
    articles every cycle, so without this a rejection would be undone within
    minutes."""
    rejected = _candidate_row(status="rejected", review_note="spam")
    db = FakeDB(discovery_candidates=[rejected], feeds=[])

    new, seen = record_candidates(
        db, _target(), [Found("https://blog.example.org/feed", "A Blog")]
    )

    assert (new, seen) == (0, 1)
    assert len(db.rows("discovery_candidates")) == 1
    assert db.rows("discovery_candidates")[0]["status"] == "rejected"
    # No insert at all, and every update was fenced to pending rows.
    assert "insert" not in db.op_names("discovery_candidates")
    for name, args in db.ops_for("discovery_candidates"):
        if name == "update":
            assert set(args[0]) == {"last_seen_at"}
    eq_ops = [args for name, args in db.ops_for("discovery_candidates") if name == "eq"]
    assert ("status", "pending") in eq_ops


def test_record_candidates_refreshes_last_seen_on_a_pending_duplicate():
    existing = _candidate_row(last_seen_at="2020-01-01T00:00:00+00:00")
    db = FakeDB(discovery_candidates=[existing], feeds=[])

    new, seen = record_candidates(db, _target(), [Found("https://blog.example.org/feed")])

    assert (new, seen) == (0, 1)
    assert len(db.rows("discovery_candidates")) == 1
    assert db.rows("discovery_candidates")[0]["last_seen_at"] > "2026-01-01"


def test_record_candidates_marks_an_already_imported_feed():
    feed_id = str(uuid4())
    db = FakeDB(
        discovery_candidates=[],
        feeds=[{"id": feed_id, "url": "https://blog.example.org/feed"}],
    )
    new, seen = record_candidates(db, _target(), [Found("https://blog.example.org/feed")])

    assert (new, seen) == (0, 0)
    row = db.rows("discovery_candidates")[0]
    assert row["status"] == "imported"
    assert row["feed_id"] == feed_id


def test_record_candidates_sanitizes_untrusted_text():
    db = FakeDB(discovery_candidates=[], feeds=[])
    record_candidates(db, _target(), [
        Found("https://blog.example.org/feed",
              title="Ev​il\x00 ‮Title",
              website_url="javascript:alert(1)")
    ])
    row = db.rows("discovery_candidates")[0]
    assert row["title"] == "Evil Title"
    assert row["website_url"] is None


def test_record_candidates_skips_unusable_urls():
    db = FakeDB(discovery_candidates=[], feeds=[])
    new, seen = record_candidates(db, _target(), [
        Found("javascript:alert(1)"), Found(""), Found("https://ok.example.org/feed"),
    ])
    assert new == 1
    assert len(db.rows("discovery_candidates")) == 1


def test_record_candidates_looks_urls_up_individually_not_via_in_():
    """These URLs come from remote HTML, and PostgREST's filter language treats
    `,` `(` `)` as syntax (SECURITY.md #14) — so they must never reach a filter
    that parses lists."""
    db = FakeDB(discovery_candidates=[], feeds=[])
    record_candidates(db, _target(), [
        Found("https://a.example.org/feed"), Found("https://b.example.org/feed"),
    ])
    assert "in_" not in db.op_names("discovery_candidates")
    assert "in_" not in db.op_names("feeds")


def test_record_candidates_uses_maybe_single_not_single():
    db = FakeDB(discovery_candidates=[], feeds=[])
    # FakeQuery.single() raises; reaching this line means it was never called.
    record_candidates(db, _target(), [Found("https://a.example.org/feed")])
    assert "maybe_single" in db.op_names("discovery_candidates")


# ── reject ───────────────────────────────────────────────────────────────────

def test_reject_candidate_sets_status_and_note():
    row = _candidate_row()
    db = FakeDB(discovery_candidates=[row], discovery_targets=[])
    result = reject_candidate(db, row["id"], note="  low quality  ")
    assert result["status"] == "rejected"
    assert row["review_note"] == "low quality"
    assert row["reviewed_at"] is not None


def test_reject_candidate_with_block_host_rejects_the_target():
    row = _candidate_row()
    target = {"id": TARGET_ID, "host": "blog.example.org", "status": "done"}
    db = FakeDB(discovery_candidates=[row], discovery_targets=[target])

    reject_candidate(db, row["id"], block_host=True)

    assert target["status"] == "rejected"


def test_reject_candidate_without_block_host_leaves_the_target():
    row = _candidate_row()
    target = {"id": TARGET_ID, "host": "blog.example.org", "status": "done"}
    db = FakeDB(discovery_candidates=[row], discovery_targets=[target])
    reject_candidate(db, row["id"], block_host=False)
    assert target["status"] == "done"


def test_reject_unknown_candidate_returns_none():
    db = FakeDB(discovery_candidates=[], discovery_targets=[])
    assert reject_candidate(db, str(uuid4())) is None


def test_get_candidate_handles_a_bare_none_result():
    """postgrest-py has shipped versions where maybe_single().execute() returns a
    bare None on zero rows (SECURITY.md #20)."""
    db = FakeDB(discovery_candidates=[])
    with patch.object(db, "table") as table:
        table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = None
        assert get_candidate(db, str(uuid4())) is None


# ── promote / approve ────────────────────────────────────────────────────────

def test_promote_candidate_writes_feeds_with_next_fetch_at():
    row = _candidate_row()
    db = FakeDB(discovery_candidates=[row], feeds=[])

    feed = promote_candidate(db, row, category="tech", tags=["blog"])

    assert feed["url"] == "https://blog.example.org/feed"
    assert feed["category"] == "tech"
    assert feed["tags"] == ["blog"]
    assert feed["next_fetch_at"] is not None
    _table, _rows, on_conflict = db.upserts[0]
    assert on_conflict == "url"
    assert row["status"] == "imported"
    assert row["feed_id"] == feed["id"]


@pytest.mark.asyncio
async def test_promotion_never_fetches_inline():
    """The existing refresh worker owns the first article fetch. A bulk approval
    that fetched would take as long as the slowest feed."""
    row = _candidate_row()
    db = FakeDB(discovery_candidates=[row], feeds=[])
    with (
        patch("rss_parser.fetch_and_parse", side_effect=AssertionError("fetched!")),
        patch("httpx.AsyncClient", side_effect=AssertionError("network!")),
    ):
        assert promote_candidate(db, row) is not None


def test_promote_candidate_falls_back_to_host_for_a_null_title():
    """feeds.title is NOT NULL, and an Atom feed can have an empty <title>."""
    row = _candidate_row(title=None)
    db = FakeDB(discovery_candidates=[row], feeds=[])
    feed = promote_candidate(db, row)
    assert feed["title"] == "blog.example.org"


def test_promote_candidate_falls_back_to_url_without_a_host():
    row = _candidate_row(title=None, source_host=None)
    db = FakeDB(discovery_candidates=[row], feeds=[])
    assert promote_candidate(db, row)["title"] == "https://blog.example.org/feed"


def test_approve_candidate_records_the_decision_before_writing_feeds():
    """So a failed feeds write doesn't lose the approval — promote_approved()
    retries it next cycle."""
    row = _candidate_row()
    db = FakeDB(discovery_candidates=[row], feeds=[])

    feed, outcome = approve_candidate(db, row["id"])

    assert outcome == "imported"
    assert feed is not None
    # 'approved' was written first, then superseded by 'imported'.
    updates = [p for t, p in db.updates if t == "discovery_candidates"]
    assert updates[0]["status"] == "approved"
    assert updates[-1]["status"] == "imported"


def test_approve_unknown_candidate():
    db = FakeDB(discovery_candidates=[], feeds=[])
    assert approve_candidate(db, str(uuid4())) == (None, "not_found")


def test_approve_a_rejected_candidate_is_refused():
    row = _candidate_row(status="rejected")
    db = FakeDB(discovery_candidates=[row], feeds=[])
    feed, outcome = approve_candidate(db, row["id"])
    assert (feed, outcome) == (None, "already_rejected")
    assert row["status"] == "rejected"


def test_promote_approved_sweeps_stranded_approvals():
    stranded = _candidate_row(status="approved")
    other = _candidate_row(status="pending", feed_url="https://other.example.org/feed")
    db = FakeDB(discovery_candidates=[stranded, other], feeds=[])

    promoted = promote_approved(db)

    assert len(promoted) == 1
    assert stranded["status"] == "imported"
    assert other["status"] == "pending"


# ── auto-promote ─────────────────────────────────────────────────────────────

def test_auto_promote_is_off_by_default(monkeypatch):
    monkeypatch.delenv("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", raising=False)
    db = FakeDB()  # no tables declared: any query at all would raise
    assert auto_promote_due(db) == []
    assert db.ops == []


def test_auto_promote_explicit_zero_stays_off(monkeypatch):
    """0 is a meaningful opt-out, not a garbage value to be replaced by the
    default — getting this wrong silently enables auto-import."""
    monkeypatch.setenv("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", "0")
    db = FakeDB()
    assert auto_promote_due(db) == []
    assert db.ops == []


def test_auto_promote_imports_only_rows_over_the_threshold(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", "3")
    strong = _candidate_row(referring_feed_count=4, feed_url="https://strong.example/f")
    weak = _candidate_row(referring_feed_count=2, feed_url="https://weak.example/f")
    db = FakeDB(discovery_candidates=[strong, weak], feeds=[])

    promoted = auto_promote_due(db)

    assert [f["url"] for f in promoted] == ["https://strong.example/f"]
    assert strong["status"] == "imported"
    assert weak["status"] == "pending"


def test_auto_promote_ignores_rejected_rows(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", "1")
    rejected = _candidate_row(status="rejected", referring_feed_count=9)
    db = FakeDB(discovery_candidates=[rejected], feeds=[])
    assert auto_promote_due(db) == []
    assert rejected["status"] == "rejected"


# ── listing / stats ──────────────────────────────────────────────────────────

def test_list_candidates_filters_and_orders():
    rows = [
        _candidate_row(feed_url="https://a/f", referring_feed_count=1),
        _candidate_row(feed_url="https://b/f", referring_feed_count=5),
        _candidate_row(feed_url="https://c/f", referring_feed_count=3,
                       status="rejected"),
    ]
    db = FakeDB(discovery_candidates=rows)
    items, total = list_candidates(db, status="pending")
    assert [i["feed_url"] for i in items] == ["https://b/f", "https://a/f"]
    assert total == 2


def test_list_candidates_applies_min_referrers():
    rows = [
        _candidate_row(feed_url="https://a/f", referring_feed_count=1),
        _candidate_row(feed_url="https://b/f", referring_feed_count=5),
    ]
    db = FakeDB(discovery_candidates=rows)
    items, _ = list_candidates(db, min_referrers=3)
    assert [i["feed_url"] for i in items] == ["https://b/f"]


def test_list_candidates_paginates():
    rows = [
        _candidate_row(feed_url=f"https://h{i}/f", referring_feed_count=10 - i)
        for i in range(5)
    ]
    db = FakeDB(discovery_candidates=rows)
    items, total = list_candidates(db, page=2, page_size=2)
    assert [i["feed_url"] for i in items] == ["https://h2/f", "https://h3/f"]
    assert total == 5


def test_stats_counts_every_status():
    db = FakeDB(
        discovery_targets=[
            {"id": "1", "status": "pending"}, {"id": "2", "status": "pending"},
            {"id": "3", "status": "done"}, {"id": "4", "status": "rejected"},
        ],
        discovery_candidates=[
            {"id": "c1", "status": "pending"}, {"id": "c2", "status": "imported"},
        ],
        discovery_sources=[{"id": "s1", "enabled": True}, {"id": "s2", "enabled": False}],
    )
    out = stats(db)
    assert out["targets_pending"] == 2
    assert out["targets_done"] == 1
    assert out["targets_rejected"] == 1
    assert out["targets_blocked"] == 0
    assert out["candidates_pending"] == 1
    assert out["candidates_imported"] == 1
    assert out["sources_enabled"] == 1


# ── promotion must not clobber an existing feed ──────────────────────────────

def test_promote_links_to_an_existing_feed_without_overwriting_it():
    """A feed on this URL may have been imported by hand while the candidate sat
    in the queue. Upserting would replace a curated title/category/tags with
    scraped values and this call's empty defaults, so a duplicate approval would
    quietly damage the catalog."""
    row = _candidate_row(title="Scraped Title", website_url="https://scraped.example/")
    curated = {
        "id": str(uuid4()),
        "title": "Hand-curated Title",
        "url": "https://blog.example.org/feed",
        "website_url": "https://curated.example/",
        "category": "科技",
        "tags": ["精選"],
    }
    db = FakeDB(discovery_candidates=[row], feeds=[curated])

    feed = promote_candidate(db, row)

    assert feed["id"] == curated["id"]
    assert curated["title"] == "Hand-curated Title"
    assert curated["category"] == "科技"
    assert curated["tags"] == ["精選"]
    assert curated["website_url"] == "https://curated.example/"
    assert db.upserts == []          # nothing was written to feeds at all
    assert row["status"] == "imported"
    assert row["feed_id"] == curated["id"]


def test_approve_links_to_an_existing_feed_instead_of_overwriting():
    row = _candidate_row()
    curated = {"id": str(uuid4()), "title": "Curated", "category": "既有",
               "url": "https://blog.example.org/feed", "tags": ["keep"]}
    db = FakeDB(discovery_candidates=[row], feeds=[curated])

    feed, outcome = approve_candidate(db, row["id"], category="新的", tags=["new"])

    assert outcome == "imported"
    assert feed["id"] == curated["id"]
    assert curated["category"] == "既有"
    assert curated["tags"] == ["keep"]


# ── approval metadata survives a failed write ────────────────────────────────

def test_approval_stores_the_reviewers_choices():
    row = _candidate_row()
    db = FakeDB(discovery_candidates=[row], feeds=[])

    approve_candidate(db, row["id"], category="科技", tags=["blog", "個人"])

    approval = [p for t, p in db.updates if t == "discovery_candidates"][0]
    assert approval["status"] == "approved"
    assert approval["approved_category"] == "科技"
    assert approval["approved_tags"] == ["blog", "個人"]


def test_retry_after_a_failed_write_reproduces_the_reviewers_choices():
    """promote_approved() calls promote_candidate() with no arguments, so without
    the stored choices the retry would import the feed uncategorised and silently
    discard what the admin picked."""
    stranded = _candidate_row(
        status="approved", approved_category="科技", approved_tags=["blog"],
    )
    db = FakeDB(discovery_candidates=[stranded], feeds=[])

    promoted = promote_approved(db)

    assert len(promoted) == 1
    assert promoted[0]["category"] == "科技"
    assert promoted[0]["tags"] == ["blog"]


def test_explicit_arguments_still_win_over_stored_ones():
    row = _candidate_row(approved_category="舊的", approved_tags=["old"])
    db = FakeDB(discovery_candidates=[row], feeds=[])

    feed = promote_candidate(db, row, category="新的", tags=["new"])

    assert feed["category"] == "新的"
    assert feed["tags"] == ["new"]
