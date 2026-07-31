from __future__ import annotations
import os
import time
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest

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
    .execute() result matters for these tests."""
    chain = MagicMock()
    for name in ("select", "is_", "in_", "eq", "limit"):
        getattr(chain, name).return_value = chain
    chain.not_.in_.return_value = chain
    chain.execute.return_value = execute_return
    return chain


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


def test_anonymous_no_signals_does_not_filter_by_category(client, monkeypatch):
    """Regression test: the candidate query used to hard-filter with
    query.in_("category", ...), which meant a user with any known category
    signal would never see a feed outside the categories they already
    follow — defeating the platform's "discover new sources" purpose
    (CLAUDE.md, FEATURES.md §2). Category is a scoring signal only now,
    the same as tags/language."""
    c, mock_db = client
    feeds_chain = _chain(MagicMock(data=[_feed_row() for _ in range(3)]))
    mock_db.table.side_effect = lambda name: {"feeds": feeds_chain}[name]
    monkeypatch.setattr("routers.recommendations.random.shuffle", lambda seq: None)

    resp = c.get("/api/recommendations")

    assert resp.status_code == 200
    feeds_chain.in_.assert_not_called()


def test_liked_signal_still_surfaces_candidates_outside_that_category(client):
    c, mock_db = client
    liked_id = str(uuid4())
    other_category_feed = _feed_row(category="art")
    matching_feed = _feed_row(category="tech")

    liked_lookup = _chain(
        MagicMock(data=[{"category": "tech", "tags": [], "language": None}])
    )
    candidate_pool = _chain(MagicMock(data=[matching_feed, other_category_feed]))
    calls = {"n": 0}

    def _table(name):
        assert name == "feeds"
        calls["n"] += 1
        return liked_lookup if calls["n"] == 1 else candidate_pool

    mock_db.table.side_effect = _table

    resp = c.get("/api/recommendations", params={"liked": [liked_id]})

    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert other_category_feed["id"] in ids
    candidate_pool.in_.assert_not_called()


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
        if name == "feeds":
            return _chain(MagicMock(data=[candidate]))
        raise AssertionError(f"unexpected table {name}")

    mock_db.table.side_effect = _table

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
