from types import SimpleNamespace

import pytest

from services.discovery_candidates import auto_promote_due, promote_candidate, record_candidates
from services.discovery_config import chinese_seed_hosts, chinese_seed_quota
from services.discovery_probe import select_due_targets
from services.source_identity import feed_url_aliases, is_comment_feed
from tests.discovery_fakes import FakeDB


@pytest.mark.parametrize("url", [
    "https://example.org/comments/feed/", "https://example.org/?feed=comments-rss2",
    "https://example.org/comments.xml", "https://example.org/feed/?withcomments=1",
    "https://example.org/feed/comments/",
])
def test_comment_feeds_never_enter_review_or_promote(url):
    assert is_comment_feed(url)
    db = FakeDB(discovery_candidates=[], feeds=[])
    assert record_candidates(db, {"id": "target"},
                             [SimpleNamespace(feed_url=url)]) == (0, 0)
    assert promote_candidate(db, {"id": "candidate", "feed_url": url}) is None
    assert db.ops == []


@pytest.mark.parametrize("url", [
    "https://example.org/commentary/feed", "https://example.org/news/feed/",
    "https://example.org/feed/?withcomments=0",
])
def test_editorial_feeds_are_not_comment_feeds(url):
    assert not is_comment_feed(url)


@pytest.mark.parametrize("status", ["rejected", "held"])
def test_alias_cannot_circumvent_manual_decision(status):
    existing = {"id": "old", "feed_url": "https://example.org/feed/", "status": status}
    db = FakeDB(discovery_candidates=[existing], feeds=[])
    assert record_candidates(db, {"id": "target"},
                             [SimpleNamespace(feed_url="https://example.org/feed")]) == (0, 1)
    assert existing["status"] == status
    assert len(db.rows("discovery_candidates")) == 1


def test_alias_links_existing_curated_feed_without_overwrite():
    feed = {"id": "feed", "url": "https://example.org/rss/", "title": "Curated"}
    candidate = {"id": "candidate", "feed_url": "https://example.org/rss", "status": "approved"}
    db = FakeDB(feeds=[feed], discovery_candidates=[candidate])
    assert promote_candidate(db, candidate)["id"] == "feed"
    assert feed["title"] == "Curated"
    assert len(db.rows("feeds")) == 1
    assert candidate["status"] == "imported"


def test_historical_pending_alias_cannot_auto_promote_past_hold(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", "2")
    db = FakeDB(feeds=[], discovery_candidates=[
        {"id": "pending", "feed_url": "https://example.org/feed",
         "status": "pending", "referring_feed_count": 10},
        {"id": "held", "feed_url": "https://example.org/feed/", "status": "held"},
    ])
    assert auto_promote_due(db) == []
    assert db.rows("feeds") == []


@pytest.mark.parametrize("url", [
    "https://example.org/a/", "https://example.org/feed?category=1",
    "https://example.org/feed#different", "http://other.org/custom.xml",
])
def test_opaque_feeds_stay_distinct(url):
    assert feed_url_aliases(url) == (url,)


def _target(id, host="ordinary.org", source="article_link", count=10, **extra):
    return {"id": id, "host": host, "url": f"https://{host}/{id}",
            "source": source, "referring_feed_count": count,
            "status": "pending", "next_probe_at": "2020-01-01T00:00:00+00:00", **extra}


def test_chinese_seeds_get_bounded_slots_without_faking_referrers(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_CHINESE_SEED_HOSTS", "pansci.asia")
    monkeypatch.setenv("FEED_DISCOVERY_CHINESE_SEED_QUOTA", "99")
    normal = [_target(f"normal{i}") for i in range(5)]
    seeds = [_target(f"seed{i}", "pansci.asia", "seed", 0) for i in range(5)]
    db = FakeDB(discovery_targets=normal + seeds)
    result = select_due_targets(db, 4)
    assert [row["id"] for row in result] == ["seed0", "seed1", "normal0", "normal1"]
    assert all(row["referring_feed_count"] == 0 for row in result[:2])
    assert not db.updates


def test_priority_never_revives_terminal_or_future_seeds(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_CHINESE_SEED_HOSTS", "pansci.asia")
    db = FakeDB(discovery_targets=[
        _target("normal"),
        _target("rejected", "pansci.asia", "seed", 0, status="rejected"),
        _target("future", "pansci.asia", "seed", 0,
                next_probe_at="2099-01-01T00:00:00+00:00"),
    ])
    assert [row["id"] for row in select_due_targets(db, 4)] == ["normal"]


def test_config_can_disable_priority_and_rejects_filter_syntax(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_CHINESE_SEED_QUOTA", "0")
    assert chinese_seed_quota() == 0
    monkeypatch.setenv("FEED_DISCOVERY_CHINESE_SEED_HOSTS", "INSIDE.com.tw,bad(host),https://a.org")
    assert chinese_seed_hosts() == ("inside.com.tw",)
    monkeypatch.setenv("FEED_DISCOVERY_CHINESE_SEED_HOSTS", "")
    assert chinese_seed_hosts() == ()
