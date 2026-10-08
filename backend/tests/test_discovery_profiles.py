"""Settings-backed reservations exercise real validated profile loading."""
from uuid import uuid4

import pytest

from services.discovery_probe import select_due_targets
from tests.discovery_fakes import FakeDB


def profile(id, host, *, quota=1, enabled=True, language="ja", category="culture"):
    return {"id": id, "name": id, "language": language, "category": category,
            "enabled": enabled, "quota": quota, "seed_urls": [f"https://{host}/"]}


def target(host, *, source="seed", refs=0, status="pending",
           due="2020-01-01T00:00:00+00:00", url=None):
    return {"id": str(uuid4()), "host": host, "url": url or f"https://{host}/",
            "source": source, "status": status, "referring_feed_count": refs,
            "next_probe_at": due}


def database(profiles, targets):
    return FakeDB(app_settings=[{"key": "discovery.profiles", "version": 1,
                                "value": {"profiles": profiles}}], discovery_targets=targets)


def test_flexible_languages_categories_reserve_fair_rounds_and_half_batch():
    profiles = [profile("japanese-art", "art.example.org", quota=20),
                profile("french-science", "science.example.org", quota=20,
                        language="fr", category="science")]
    rows = [target("art.example.org", url=f"https://art.example.org/{i}") for i in range(5)]
    rows += [target("science.example.org", url=f"https://science.example.org/{i}") for i in range(5)]
    rows += [target(f"organic{i}.example.org", source="article_link", refs=100) for i in range(4)]
    selected = select_due_targets(database(profiles, rows), 8)
    assert len(selected) == 8
    assert [row["host"] for row in selected[:4]] == [
        "science.example.org", "art.example.org", "science.example.org", "art.example.org"]
    assert all(row["source"] == "article_link" for row in selected[4:])
    assert all(row["referring_feed_count"] == 0 for row in selected[:4])


def test_overlapping_profiles_deduplicate_and_still_fill_each_quota():
    first = profile("first", "shared.example.org")
    second = profile("second", "shared.example.org")
    second["seed_urls"].append("https://unique.example.org/")
    shared = target("shared.example.org")
    unique = target("unique.example.org", due="2021-01-01T00:00:00+00:00")
    organic = [target(f"organic{i}.example.org", source="article_link", refs=10) for i in range(2)]
    selected = select_due_targets(database([first, second], [shared, unique] + organic), 4)
    assert {row["id"] for row in selected[:2]} == {shared["id"], unique["id"]}
    assert len({row["id"] for row in selected}) == 4


def test_disabled_exclusive_seed_hosts_do_not_fall_back_but_organic_is_unchanged():
    paused = target("paused.example.org", refs=1000)
    organic_same = target("paused.example.org", source="article_link", refs=20,
                          url="https://paused.example.org/article")
    unprofiled = target("unprofiled.example.org", refs=10)
    other = target("other.example.org", source="directory", refs=5)
    db = database([profile("disabled", "paused.example.org", enabled=False)],
                  [paused, organic_same, unprofiled, other])
    assert [row["id"] for row in select_due_targets(db, 3)] == [
        organic_same["id"], unprofiled["id"], other["id"]]


def test_enabled_shared_host_survives_another_disabled_profile():
    shared = target("shared.example.org")
    profiles = [profile("disabled", "shared.example.org", enabled=False),
                profile("enabled", "shared.example.org")]
    assert select_due_targets(database(profiles, [shared]), 2) == [shared]


def test_only_exact_due_pending_seed_hosts_receive_reserved_slots():
    rows = [target("sub.match.example.org"), target("match.example.org", status="rejected"),
            target("match.example.org", due="2099-01-01T00:00:00+00:00"),
            target("match.example.org", source="article_link", refs=1),
            target("strong.example.org", source="article_link", refs=100)]
    selected = select_due_targets(database([profile("profile", "match.example.org", quota=10)], rows), 2)
    assert selected[0]["host"] == "strong.example.org"
    assert selected[1]["source"] == "article_link"


def test_small_batch_keeps_normal_priority_and_zero_limit_performs_no_reads():
    seed = target("seed.example.org")
    organic = target("normal.example.org", source="article_link", refs=10)
    db = database([profile("priority", "seed.example.org", quota=100)], [seed, organic])
    assert select_due_targets(db, 0) == []
    assert db.ops == []
    assert select_due_targets(db, 1) == [organic]


def test_unsafe_host_text_never_reaches_postgrest_set_filters():
    unsafe = profile("unsafe", "unsafe.example.org),host.eq.attacker.example.org")
    normal = target("normal.example.org", source="article_link", refs=1)
    db = database([unsafe], [normal])
    with pytest.raises(ValueError):
        select_due_targets(db, 2)
    assert not any(name in ("in_", "not_.in_") and args[0] == "host"
                   for name, args in db.ops_for("discovery_targets"))


def test_zero_quota_profile_uses_normal_evidence_order():
    seed = target("seed.example.org", refs=1)
    organic = target("normal.example.org", source="article_link", refs=10)
    db = database([profile("zero", "seed.example.org", quota=0)], [seed, organic])
    assert select_due_targets(db, 2) == [organic, seed]


def test_reservations_respect_each_profile_quota_even_with_spare_half_batch():
    profiles = [profile("small", "small.example.org", quota=1),
                profile("large", "large.example.org", quota=3)]
    rows = [target("small.example.org", url=f"https://small.example.org/{i}") for i in range(5)]
    rows += [target("large.example.org", url=f"https://large.example.org/{i}") for i in range(5)]
    rows += [target(f"normal{i}.example.org", source="article_link", refs=10) for i in range(6)]
    selected = select_due_targets(database(profiles, rows), 10)
    assert sum(row["host"] == "small.example.org" for row in selected) == 1
    assert sum(row["host"] == "large.example.org" for row in selected) == 3
    assert sum(row["source"] == "article_link" for row in selected) == 6


def test_oldest_profile_head_wins_when_reserved_budget_cannot_fit_all_profiles():
    newer = target("newer.example.org", due="2022-01-01T00:00:00+00:00")
    oldest = target("oldest.example.org", due="2020-01-01T00:00:00+00:00")
    organic = target("normal.example.org", source="article_link", refs=10)
    profiles = [profile("a-newer", "newer.example.org"),
                profile("z-oldest", "oldest.example.org")]
    selected = select_due_targets(database(profiles, [newer, oldest, organic]), 2)
    assert selected == [oldest, organic]


def test_high_ranked_profile_seed_overflow_cannot_consume_normal_half():
    configured = [target("priority.example.org", refs=1000,
                         url=f"https://priority.example.org/{i}") for i in range(10)]
    ordinary_seed = target("unprofiled.example.org", refs=20)
    organic_same_host = target("priority.example.org", source="article_link", refs=30,
                               url="https://priority.example.org/article")
    ordinary = target("normal.example.org", source="directory", refs=10)
    profiles = [profile("priority", "priority.example.org", quota=100)]
    selected = select_due_targets(database(profiles, configured + [ordinary_seed, organic_same_host, ordinary]), 6)
    assert len(selected) == 6
    assert sum(row["source"] == "seed" and row["host"] == "priority.example.org"
               for row in selected) == 3
    assert {row["id"] for row in selected[3:]} == {
        ordinary_seed["id"], organic_same_host["id"], ordinary["id"],
    }


def test_profile_seed_quota_does_not_expand_to_fill_underfull_normal_pool():
    configured = [target("priority.example.org", refs=1000,
                         url=f"https://priority.example.org/{i}") for i in range(10)]
    profiles = [profile("priority", "priority.example.org", quota=1)]
    selected = select_due_targets(database(profiles, configured), 8)
    assert len(selected) == 1
