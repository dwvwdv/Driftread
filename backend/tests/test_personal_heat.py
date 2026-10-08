from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from routers.recommendations import Signals, _boundary_score, _reason, _load_signals
from services.personal_heat import rank_personal_heat
from tests.test_recommendations import _empty_authenticated_tables, _chain, _token

AT = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
FEED = "11111111-1111-1111-1111-111111111111"
ARTICLE = "22222222-2222-2222-2222-222222222222"


def source(ok=AT):
    return {
        "id": FEED,
        "last_ok_at": ok.isoformat() if ok else None,
        "fetch_interval_minutes": 60,
    }


def candidate(id=ARTICLE, category="tech", group="url:https://example.org/a"):
    return {
        "id": id,
        "feed_id": FEED,
        "feed_title": "Source",
        "title": "Post",
        "url": "https://example.org/a",
        "fetched_at": AT.isoformat(),
        "timeline_at": AT.isoformat(),
        "published_at": AT.isoformat(),
        "is_read": False,
        "group_key": group,
        "category": category,
        "tags": [],
        "language": "en",
    }


def evidence(group="url:https://example.org/a", participant="feed:one", at=AT):
    return {
        "group_key": group,
        "participant_key": participant,
        "source_time": at.isoformat(),
    }


def test_fixture_independent_mirrors_decay_and_personal_dominance():
    liked = candidate(category="tech")
    viral = candidate(
        id="33333333-3333-3333-3333-333333333333", category="sports", group="url:viral"
    )
    signals = Signals()
    signals.add_liked({"category": "tech"})
    snapshot = {
        "sources": [source()],
        "candidates": [viral, liked],
        "evidence": [evidence(), evidence(at=AT - timedelta(hours=24))]
        + [evidence(group="url:viral", participant=f"feed:{i}") for i in range(100)],
    }
    result = rank_personal_heat(snapshot, signals, AT, 100)
    assert result["items"][0]["id"] == ARTICLE
    assert result["items"][0]["heat"] == 1
    assert result["items"][0]["participant_count"] == 1
    assert "喜歡" in result["items"][0]["why"]
    assert result["complete"]
    historical = rank_personal_heat(
        {
            "sources": [source()],
            "candidates": [liked],
            "evidence": [evidence(at=AT - timedelta(hours=24))],
        },
        signals,
        AT,
        100,
    )
    assert historical["items"][0]["heat"] == 0.5


def test_incomplete_does_not_claim_a_decline_and_catchup_recomputes():
    snapshot = {
        "sources": [source(None)],
        "candidates": [candidate()],
        "evidence": [evidence()],
    }
    result = rank_personal_heat(snapshot, Signals(), AT, 10)
    assert not result["complete"] and result["behind_participant_count"] == 1
    assert "資訊未完整" in result["items"][0]["why"]
    snapshot["sources"] = [source()]
    corrected = rank_personal_heat(snapshot, Signals(), AT, 10)
    assert (
        corrected["complete"]
        and corrected["items"][0]["heat"] == result["items"][0]["heat"]
    )


def test_future_unknown_and_old_evidence_never_creates_current_heat():
    rows = [
        evidence(at=AT + timedelta(days=1)),
        evidence(at=AT - timedelta(days=8)),
        dict(evidence(), source_time=None),
    ]
    result = rank_personal_heat(
        {"sources": [source()], "candidates": [candidate()], "evidence": rows},
        Signals(),
        AT,
        10,
    )
    assert (
        result["items"][0]["heat"] == 0 and result["items"][0]["participant_count"] == 0
    )


def test_same_manual_story_is_deduped_without_touching_article_ids():
    first = candidate(group="story:one")
    second = candidate(id="33333333-3333-3333-3333-333333333333", group="story:one")
    result = rank_personal_heat(
        {"sources": [source()], "candidates": [first, second], "evidence": []},
        Signals(),
        AT,
        10,
    )
    assert len(result["items"]) == 1 and result["items"][0]["id"] == second["id"]


def test_empty_cohort_is_not_complete():
    assert not rank_personal_heat(
        {"sources": [], "candidates": [], "evidence": []}, Signals(), AT, 10
    )["complete"]


def test_preferences_reason_is_truthful_and_boundary_prefers_shared_tags():
    signals = Signals(preferred_categories={"science"}, preferred_languages={"zh"})
    assert _reason({"category": "science"}, signals) == "因為你偏好 science 類別"
    assert _reason({"language": "zh"}, signals) == "因為你偏好 zh 語言的來源"
    signals.add_subscribed({"tags": ["astronomy"], "language": "zh"})
    assert _boundary_score(
        {"category": "art", "tags": ["astronomy"], "language": "zh"}, signals
    ) > _boundary_score({"category": "sports"}, signals)


def test_read_signal_is_bounded_and_queries_each_authenticated_user(client):
    _, db = client
    tables = _empty_authenticated_tables(db)
    tables["user_article_reads"] = _chain(
        SimpleNamespace(
            data=[{"articles": {"feeds": {"category": "tech", "tags": ["python"]}}}]
            * 200
        )
    )
    for user in ["one", "two"]:
        signals = Signals()
        _load_signals(db, user, signals)
        assert (
            signals.category_weight["tech"] == 0.25
            and signals.tag_weight["python"] == 0.1
        )
    user_calls = [
        call.args
        for call in tables["user_article_reads"].eq.call_args_list
        if call.args[0] == "user_id"
    ]
    assert user_calls == [("user_id", "one"), ("user_id", "two")]
    tables["user_article_reads"].limit.assert_called_with(200)


def test_personal_heat_api_bounds_filters_and_user_isolation(client):
    c, db = client
    snapshots = []

    def rpc(name, params):
        assert name == "personal_heat_snapshot"
        snapshots.append(params)
        return SimpleNamespace(
            execute=lambda: SimpleNamespace(
                data={
                    "sources": [source()],
                    "candidates": [candidate()],
                    "evidence": [],
                }
            )
        )

    db.rpc.side_effect = rpc
    with patch("routers.personal_heat._load_signals"):
        for user in ["one", "two"]:
            response = c.get(
                "/api/me/personal-heat",
                params={"feed_id": FEED, "unread_only": True, "limit": 1},
                headers={"Authorization": f"Bearer {_token(user)}"},
            )
            assert response.status_code == 200
            assert (
                response.json()["next_cursor"] is None
                and response.json()["candidate_limit"] == 500
            )
            assert "why" in response.json()["items"][0]
    assert [p["p_user_id"] for p in snapshots] == ["one", "two"]
    assert all(
        p["p_feed_id"] == FEED and p["p_unread_only"] and p["p_limit"] == 500
        for p in snapshots
    )
    assert c.get("/api/me/personal-heat").status_code == 401
    assert (
        c.get(
            "/api/me/personal-heat?limit=101",
            headers={"Authorization": f"Bearer {_token()}"},
        ).status_code
        == 422
    )
