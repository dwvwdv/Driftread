from __future__ import annotations
import os
import time
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest

from rate_limit import DEFAULT_MAX_REQUESTS
from routers.recommendations import _score_candidates

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-please-change")


def _token(user_id: str = "user-abc") -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": int(time.time()) + 3600},
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def _chain(execute_return):
    """A MagicMock query-builder node where every filter method returns
    itself, so any combination/order of .select/.is_/.in_/.not_.in_/.eq/
    .limit calls resolves back to the same node — only the terminal
    .execute() result matters unless a test asserts on a specific call."""
    chain = MagicMock()
    for name in ("select", "is_", "in_", "eq", "limit"):
        getattr(chain, name).return_value = chain
    chain.not_.in_.return_value = chain
    chain.execute.return_value = execute_return
    return chain


def _rpc_chain(execute_return):
    """A MagicMock node for a single `db.rpc(...)` call — `.execute()` is
    the only thing the caller does with it."""
    chain = MagicMock()
    chain.execute.return_value = execute_return
    return chain


def _sampling_rpc(pools):
    """Fake `db.rpc("sample_feed_candidates", params)` that truncates each
    mode's configured dataset to `p_limit` rows — simulating the DB's real
    `LIMIT` (which migration 007's `ORDER BY random()` makes a true random
    sample, not just "first N") — so a test can catch a request that
    under-asks for rows even though more are available. `pools` maps
    `p_mode` -> full dataset for that mode."""

    def _rpc(name, params):
        assert name == "sample_feed_candidates"
        data = pools[params["p_mode"]][: params["p_limit"]]
        return _rpc_chain(MagicMock(data=data))

    return _rpc


def _feed_row(feed_id=None, category=None, tags=None, language=None):
    return {
        "id": str(feed_id or uuid4()),
        "title": "Feed",
        "url": "https://example.com/rss",
        "category": category,
        "tags": tags or [],
        "language": language,
        "article_count": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


class TestScoreCandidates:
    def test_category_match_scores_three(self):
        rows = [_feed_row(category="art"), _feed_row(category="tech")]
        scored = _score_candidates(rows, {"tech"}, set(), set())
        assert scored[0]["category"] == "tech"

    def test_tag_matches_stack(self):
        rows = [_feed_row(tags=[]), _feed_row(tags=["a"]), _feed_row(tags=["a", "b"])]
        scored = _score_candidates(rows, set(), {"a", "b"}, set())
        assert [len(r["tags"]) for r in scored] == [2, 1, 0]

    def test_language_match_scores_one(self):
        rows = [_feed_row(language="fr"), _feed_row(language="en")]
        scored = _score_candidates(rows, set(), set(), {"en"})
        assert scored[0]["language"] == "en"

    def test_combined_signals_outrank_a_single_signal(self):
        weak = _feed_row(category="tech")
        strong = _feed_row(category="tech", tags=["a"], language="en")
        scored = _score_candidates([weak, strong], {"tech"}, {"a"}, {"en"})
        assert scored[0] is strong


def test_anonymous_no_signals_fetches_a_single_unfiltered_pool(client):
    """With no category/tag/language signal at all there's nothing to split
    the pool for — one plain RPC call in "unfiltered" mode, no category
    predicate either way."""
    c, mock_db = client
    mock_db.rpc.side_effect = _sampling_rpc(
        {"unfiltered": [_feed_row() for _ in range(3)]}
    )

    resp = c.get("/api/recommendations")

    assert resp.status_code == 200
    mock_db.rpc.assert_called_once()
    name, params = mock_db.rpc.call_args[0]
    assert name == "sample_feed_candidates"
    assert params["p_mode"] == "unfiltered"


def test_recommendations_rate_limited_after_threshold(client):
    """Codex review (P1): migration 007 makes each call to this endpoint run
    up to three `ORDER BY random()` full-table passes via
    sample_feed_candidates — restricting who can call that RPC directly
    doesn't stop plain request-volume flooding straight at this route, which
    (unlike /discover and /discover/import, rate-limited since
    SECURITY.md #18) had no limiter of its own until now."""
    c, mock_db = client
    mock_db.rpc.side_effect = _sampling_rpc({"unfiltered": []})

    for _ in range(DEFAULT_MAX_REQUESTS):
        resp = c.get("/api/recommendations")
        assert resp.status_code == 200
    resp = c.get("/api/recommendations")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_category_signal_splits_into_three_pools(client):
    """Regression test for two Codex findings on PR #26's first pass at
    this fix:
    - naively dropping the category predicate meant that on a catalog
      bigger than the fetch limit, the unfiltered first batch could
      contain zero matches for the caller's known categories, silently
      killing personalization;
    - a plain "not in known categories" predicate never matches a NULL
      column, so a feed with no category at all (a normal catalog state)
      was invisible to every personalized caller.
    Candidates now come from three independent DB-function calls
    (migration 007's `sample_feed_candidates`, one per `p_mode`):
    category-matching (preferred), a different known category, and no
    category at all (the latter two both count as exploratory)."""
    c, mock_db = client
    liked_id = str(uuid4())
    matching_feed = _feed_row(category="tech")
    other_category_feed = _feed_row(category="art")
    uncategorized_feed = _feed_row(category=None)

    liked_lookup = _chain(
        MagicMock(data=[{"category": "tech", "tags": [], "language": None}])
    )
    mock_db.table.side_effect = lambda name: {"feeds": liked_lookup}[name]
    mock_db.rpc.side_effect = _sampling_rpc(
        {
            "in_categories": [matching_feed],
            "not_in_categories": [other_category_feed],
            "uncategorized": [uncategorized_feed],
        }
    )

    resp = c.get("/api/recommendations", params={"liked": [liked_id]})

    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert matching_feed["id"] in ids
    assert other_category_feed["id"] in ids
    assert uncategorized_feed["id"] in ids

    calls_by_mode = {
        call.args[1]["p_mode"]: call.args[1] for call in mock_db.rpc.call_args_list
    }
    # preferred slice: positive category match, plus the usual id exclusion
    assert calls_by_mode["in_categories"]["p_categories"] == ["tech"]
    assert calls_by_mode["in_categories"]["p_excluded_ids"] == [liked_id]
    # exploratory slices carry the same category set for their own (negated
    # / null) predicate, and the same id exclusion
    assert calls_by_mode["not_in_categories"]["p_categories"] == ["tech"]
    assert calls_by_mode["not_in_categories"]["p_excluded_ids"] == [liked_id]
    assert calls_by_mode["uncategorized"]["p_excluded_ids"] == [liked_id]


def test_exploration_slots_survive_scoring_on_a_populated_catalog(client):
    """P1 regression: reserving exploration slots only in the candidate
    *pool* isn't enough. A preferred row always outscores an exploratory
    one with no other matching signal (+3 vs 0), so once every pool is
    merged and re-sorted by score, `top[:limit]` would be 100% preferred
    rows whenever the preferred pool alone already has >= limit matches —
    the pool-level split would never actually reach the caller. Slots must
    be reserved on the scored output too."""
    c, mock_db = client
    limit = 5
    liked_id = str(uuid4())
    # far more preferred matches than `limit`, so naive score-and-slice
    # would fill the entire page before an exploratory row is ever reached
    preferred_matches = [_feed_row(category="tech") for _ in range(limit * 4)]
    exploratory_match = _feed_row(category="art")

    liked_lookup = _chain(
        MagicMock(data=[{"category": "tech", "tags": [], "language": None}])
    )
    mock_db.table.side_effect = lambda name: {"feeds": liked_lookup}[name]
    mock_db.rpc.side_effect = _sampling_rpc(
        {
            "in_categories": preferred_matches,
            "not_in_categories": [exploratory_match],
            "uncategorized": [],
        }
    )

    resp = c.get(
        "/api/recommendations", params={"liked": [liked_id], "limit": limit}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == limit
    assert exploratory_match["id"] in [row["id"] for row in body]


def test_exploratory_subpool_can_use_the_full_budget_when_the_other_is_empty(client):
    """P2 regression: the exploration budget used to be pre-split in half
    between the two exploratory sub-queries (other-category /
    uncategorized), each independently capped at that half. If one
    subtype had no matching rows at all — e.g. a catalog with zero
    uncategorized feeds — the other was still capped at half the budget
    even though it alone could have filled the whole thing, silently
    under-delivering `limit` results despite plenty of eligible feeds
    existing. Each subquery must be able to draw on the full budget."""
    c, mock_db = client
    limit = 10
    liked_id = str(uuid4())
    # more than half of the exploration budget (round(limit * 5 * 0.3) ==
    # 15 for limit=10) — a pre-split-in-half cap would have silently
    # thrown the rest away even though the catalog has them.
    other_category_matches = [_feed_row(category="art") for _ in range(12)]

    liked_lookup = _chain(
        MagicMock(data=[{"category": "tech", "tags": [], "language": None}])
    )
    mock_db.table.side_effect = lambda name: {"feeds": liked_lookup}[name]
    mock_db.rpc.side_effect = _sampling_rpc(
        {
            "in_categories": [],
            "not_in_categories": other_category_matches,
            "uncategorized": [],
        }
    )

    resp = c.get(
        "/api/recommendations", params={"liked": [liked_id], "limit": limit}
    )

    assert resp.status_code == 200
    assert len(resp.json()) == limit


def test_uncategorized_candidates_are_not_starved_when_other_category_fills_the_budget(
    client, monkeypatch
):
    """P2 regression (round 2): `other_category` is fetched — and listed —
    before `uncategorized`, and each is independently capped at the full
    exploration budget, so a plain (other_category + uncategorized)
    [:exploration_n] deterministically drops every uncategorized row
    whenever other_category alone already fills the budget, which is the
    common case in a populated catalog. The fix shuffles the combined list
    *before* applying the cap. Monkeypatch `random.shuffle` to a
    deterministic reversal: this only recovers tail rows (here,
    `uncategorized`) if the shuffle genuinely runs before the cap — a
    shuffle applied after slicing could never bring back rows the cap
    already dropped, which is exactly the ordering mistake this pins."""
    c, mock_db = client
    limit = 10
    liked_id = str(uuid4())
    other_category_matches = [_feed_row(category="art") for _ in range(30)]
    uncategorized_matches = [_feed_row(category=None) for _ in range(5)]
    monkeypatch.setattr(
        "routers.recommendations.random.shuffle", lambda seq: seq.reverse()
    )

    liked_lookup = _chain(
        MagicMock(data=[{"category": "tech", "tags": [], "language": None}])
    )
    mock_db.table.side_effect = lambda name: {"feeds": liked_lookup}[name]
    mock_db.rpc.side_effect = _sampling_rpc(
        {
            "in_categories": [],
            "not_in_categories": other_category_matches,
            "uncategorized": uncategorized_matches,
        }
    )

    resp = c.get(
        "/api/recommendations", params={"liked": [liked_id], "limit": limit}
    )

    assert resp.status_code == 200
    result_ids = {row["id"] for row in resp.json()}
    uncategorized_ids = {row["id"] for row in uncategorized_matches}
    assert result_ids & uncategorized_ids


def test_final_order_reflects_score_not_quota_origin(client):
    """P2 regression: the preferred/exploratory quota decides *which* rows
    make the page, not their display order — concatenating "all preferred
    slots, then all exploratory slots" ignores that an exploratory row
    matching several tags can genuinely outscore a preferred row that only
    matches category. The response order must reflect the real score."""
    c, mock_db = client
    liked_id = str(uuid4())
    weak_preferred = _feed_row(category="tech")
    strong_exploratory = _feed_row(category="art", tags=["a", "b"])

    liked_lookup = _chain(
        MagicMock(data=[{"category": "tech", "tags": ["a", "b"], "language": None}])
    )
    mock_db.table.side_effect = lambda name: {"feeds": liked_lookup}[name]
    mock_db.rpc.side_effect = _sampling_rpc(
        {
            "in_categories": [weak_preferred],
            "not_in_categories": [strong_exploratory],
            "uncategorized": [],
        }
    )

    resp = c.get("/api/recommendations", params={"liked": [liked_id]})

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["id"] == strong_exploratory["id"]


def test_authenticated_user_excludes_their_subscriptions(client):
    c, mock_db = client
    subscribed_id = str(uuid4())
    candidate = _feed_row(category="tech")

    def _table(name):
        if name == "user_feeds":
            return _chain(
                MagicMock(
                    data=[
                        {
                            "feed_id": subscribed_id,
                            "feeds": {"category": "tech", "tags": ["ai"], "language": "en"},
                        }
                    ]
                )
            )
        if name == "user_preferences":
            return _chain(MagicMock(data=[]))
        raise AssertionError(f"unexpected table {name}")

    mock_db.table.side_effect = _table

    def _rpc(name, params):
        assert subscribed_id in params["p_excluded_ids"]
        return _rpc_chain(MagicMock(data=[candidate]))

    mock_db.rpc.side_effect = _rpc

    resp = c.get(
        "/api/recommendations",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    assert all(row["id"] != subscribed_id for row in resp.json())


@pytest.mark.parametrize("limit", [0, 51])
def test_limit_out_of_bounds_is_rejected(client, limit):
    c, _ = client
    resp = c.get("/api/recommendations", params={"limit": limit})
    assert resp.status_code == 422
