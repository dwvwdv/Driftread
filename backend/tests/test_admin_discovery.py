from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests.discovery_fakes import FakeDB

KEY = {"x-api-key": "test-admin-key"}
BASE = "/api/admin/discovery"

ENDPOINTS = [
    ("post", f"{BASE}/targets", {"urls": ["https://example.com/"]}),
    ("get", f"{BASE}/targets", None),
    ("patch", f"{BASE}/targets/{uuid4()}/block", None),
    ("get", f"{BASE}/candidates", None),
    ("post", f"{BASE}/candidates/{uuid4()}/approve", {}),
    ("post", f"{BASE}/candidates/{uuid4()}/reject", {}),
    ("get", f"{BASE}/sources", None),
    ("post", f"{BASE}/sources", {"items": [{"url": "https://d.example.com/"}]}),
    ("patch", f"{BASE}/sources/{uuid4()}", {"enabled": False}),
    ("post", f"{BASE}/sources/reload-defaults", None),
    ("post", f"{BASE}/run", None),
    ("get", f"{BASE}/stats", None),
]


def _call(client, method, url, body, headers):
    fn = getattr(client, method)
    if body is None:
        return fn(url, headers=headers)
    return fn(url, json=body, headers=headers)


# ── auth ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,url,body", ENDPOINTS)
def test_missing_api_key_is_rejected(client, method, url, body):
    c, _db = client
    assert _call(c, method, url, body, {}).status_code == 422


@pytest.mark.parametrize("method,url,body", ENDPOINTS)
def test_wrong_api_key_is_rejected(client, method, url, body):
    c, db = client
    resp = _call(c, method, url, body, {"x-api-key": "nope"})
    assert resp.status_code == 403
    db.table.assert_not_called()


# ── seeding targets ──────────────────────────────────────────────────────────

def _fake_db_client(app_client, fake: FakeDB):
    """Swap the MagicMock the `client` fixture installs for a real FakeDB."""
    from database import get_client
    from main import app

    app.dependency_overrides[get_client] = lambda: fake
    return app_client


def test_seed_targets_rejects_a_private_url_before_touching_the_db(client):
    """The SSRF gate must precede every DB write, the same assertion shape as
    test_admin.py's import-from-url test."""
    c, db = client
    resp = c.post(f"{BASE}/targets", json={"urls": ["http://10.0.0.5/"]}, headers=KEY)

    assert resp.status_code == 200
    assert resp.json()["accepted"] == 0
    assert len(resp.json()["rejected"]) == 1
    db.table.assert_not_called()


def test_seed_targets_rejects_a_denylisted_host(client):
    c, db = client
    with patch("services.feed_discovery._is_safe_host", return_value=True):
        resp = c.post(f"{BASE}/targets", json={"urls": ["https://twitter.com/"]},
                      headers=KEY)

    assert resp.json()["accepted"] == 0
    assert "denylist" in resp.json()["rejected"][0]
    db.table.assert_not_called()


def test_seed_targets_accepts_a_new_host(client):
    c, _mock = client
    fake = FakeDB(discovery_targets=[])
    _fake_db_client(c, fake)

    with patch("services.feed_discovery._is_safe_host", return_value=True):
        resp = c.post(f"{BASE}/targets", json={"urls": ["https://blog.example.org/x"]},
                      headers=KEY)

    assert resp.json() == {"accepted": 1, "requeued": 0, "skipped": 0, "rejected": []}
    row = fake.rows("discovery_targets")[0]
    assert row["host"] == "blog.example.org"
    # The origin, not the deep link the admin happened to paste.
    assert row["url"] == "https://blog.example.org/"
    assert row["source"] == "seed"


def test_seed_targets_requeues_an_exhausted_host(client):
    c, _mock = client
    target = {"id": str(uuid4()), "host": "blog.example.org",
              "url": "https://blog.example.org/", "status": "exhausted", "attempts": 3}
    fake = FakeDB(discovery_targets=[target])
    _fake_db_client(c, fake)

    with patch("services.feed_discovery._is_safe_host", return_value=True):
        resp = c.post(f"{BASE}/targets", json={"urls": ["https://blog.example.org/"]},
                      headers=KEY)

    assert resp.json()["requeued"] == 1
    assert target["status"] == "pending"
    assert target["attempts"] == 0


def test_seed_targets_never_resurrects_a_rejected_host(client):
    """Re-seeding must not be a back door around an admin's block."""
    c, _mock = client
    target = {"id": str(uuid4()), "host": "blog.example.org",
              "url": "https://blog.example.org/", "status": "rejected", "attempts": 0}
    fake = FakeDB(discovery_targets=[target])
    _fake_db_client(c, fake)

    with patch("services.feed_discovery._is_safe_host", return_value=True):
        resp = c.post(f"{BASE}/targets", json={"urls": ["https://blog.example.org/"]},
                      headers=KEY)

    assert resp.json()["skipped"] == 1
    assert target["status"] == "rejected"


def test_seed_targets_bad_entries_do_not_sink_the_batch(client):
    c, _mock = client
    fake = FakeDB(discovery_targets=[])
    _fake_db_client(c, fake)

    def safe(host: str, timeout: float | None = None) -> bool:
        return host != "10.0.0.5"

    with patch("services.feed_discovery._is_safe_host", side_effect=safe):
        resp = c.post(
            f"{BASE}/targets",
            json={"urls": ["http://10.0.0.5/", "https://good.example.org/",
                           "https://github.com/x"]},
            headers=KEY,
        )

    body = resp.json()
    assert body["accepted"] == 1
    assert len(body["rejected"]) == 2
    assert {r["host"] for r in fake.rows("discovery_targets")} == {"good.example.org"}


@pytest.mark.parametrize(
    "payload",
    [
        {"urls": []},
        {"urls": ["https://x.example.com/"] * 501},
        {"urls": ["https://" + "a" * 2100]},
        {},
    ],
)
def test_seed_targets_validates_the_payload(client, payload):
    c, db = client
    assert c.post(f"{BASE}/targets", json=payload, headers=KEY).status_code == 422
    db.table.assert_not_called()


# ── listing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [f"{BASE}/targets", f"{BASE}/candidates"])
def test_listing_rejects_an_oversized_page(client, path):
    c, _db = client
    assert c.get(f"{path}?page_size=201", headers=KEY).status_code == 422
    assert c.get(f"{path}?page=0", headers=KEY).status_code == 422


def test_list_candidates_returns_pending_by_default(client):
    c, _mock = client
    rows = [
        {"id": str(uuid4()), "feed_url": "https://a.example/f", "status": "pending",
         "referring_feed_count": 2, "discovered_at": "2026-01-01T00:00:00+00:00"},
        {"id": str(uuid4()), "feed_url": "https://b.example/f", "status": "rejected",
         "referring_feed_count": 9, "discovered_at": "2026-01-01T00:00:00+00:00"},
    ]
    _fake_db_client(c, FakeDB(discovery_candidates=rows))

    body = c.get(f"{BASE}/candidates", headers=KEY).json()
    assert [i["feed_url"] for i in body["items"]] == ["https://a.example/f"]
    assert body["total"] == 1


# ── approve / reject ─────────────────────────────────────────────────────────

def _candidate(**overrides) -> dict:
    row = {
        "id": str(uuid4()),
        "target_id": str(uuid4()),
        "feed_url": "https://blog.example.org/feed",
        "title": "A Blog",
        "website_url": "https://blog.example.org/",
        "source_host": "blog.example.org",
        "referring_feed_count": 1,
        "status": "pending",
        "feed_id": None,
        "discovered_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_approve_creates_a_feed_due_for_the_refresh_worker(client):
    c, _mock = client
    row = _candidate()
    fake = FakeDB(discovery_candidates=[row], feeds=[])
    _fake_db_client(c, fake)

    resp = c.post(f"{BASE}/candidates/{row['id']}/approve",
                  json={"category": "tech", "tags": ["blog"]}, headers=KEY)

    assert resp.status_code == 200
    feed = resp.json()
    assert feed["url"] == "https://blog.example.org/feed"
    assert feed["category"] == "tech"
    assert feed["next_fetch_at"] is not None
    assert row["status"] == "imported"


def test_approve_unknown_candidate_is_404(client):
    c, _mock = client
    _fake_db_client(c, FakeDB(discovery_candidates=[], feeds=[]))
    resp = c.post(f"{BASE}/candidates/{uuid4()}/approve", json={}, headers=KEY)
    assert resp.status_code == 404


def test_approve_a_rejected_candidate_is_409(client):
    c, _mock = client
    row = _candidate(status="rejected")
    _fake_db_client(c, FakeDB(discovery_candidates=[row], feeds=[]))
    resp = c.post(f"{BASE}/candidates/{row['id']}/approve", json={}, headers=KEY)
    assert resp.status_code == 409
    assert row["status"] == "rejected"


def test_reject_with_block_host_also_rejects_the_target(client):
    c, _mock = client
    row = _candidate()
    target = {"id": row["target_id"], "host": "blog.example.org", "status": "done"}
    _fake_db_client(c, FakeDB(discovery_candidates=[row], discovery_targets=[target]))

    resp = c.post(f"{BASE}/candidates/{row['id']}/reject",
                  json={"note": "spam", "block_host": True}, headers=KEY)

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert target["status"] == "rejected"


def test_reject_unknown_candidate_is_404(client):
    c, _mock = client
    _fake_db_client(c, FakeDB(discovery_candidates=[], discovery_targets=[]))
    assert c.post(f"{BASE}/candidates/{uuid4()}/reject", json={},
                  headers=KEY).status_code == 404


def test_block_target(client):
    c, _mock = client
    target = {"id": str(uuid4()), "host": "bad.example.org",
              "url": "https://bad.example.org/", "status": "pending",
              "source": "seed", "created_at": "2026-01-01T00:00:00+00:00",
              "updated_at": "2026-01-01T00:00:00+00:00"}
    _fake_db_client(c, FakeDB(discovery_targets=[target]))

    resp = c.patch(f"{BASE}/targets/{target['id']}/block", headers=KEY)
    assert resp.json()["status"] == "rejected"


def test_block_unknown_target_is_404(client):
    c, _mock = client
    _fake_db_client(c, FakeDB(discovery_targets=[]))
    assert c.patch(f"{BASE}/targets/{uuid4()}/block", headers=KEY).status_code == 404


# ── sources ──────────────────────────────────────────────────────────────────

def test_add_sources_validates_urls(client):
    c, db = client
    resp = c.post(f"{BASE}/sources", json={"items": [{"url": "http://10.0.0.5/"}]},
                  headers=KEY)
    assert resp.status_code == 400
    db.table.assert_not_called()


def test_add_sources_rejects_an_unknown_kind(client):
    c, db = client
    resp = c.post(f"{BASE}/sources",
                  json={"items": [{"url": "https://d.example/", "kind": "scrape"}]},
                  headers=KEY)
    assert resp.status_code == 422
    db.table.assert_not_called()


def test_add_sources_upserts_on_url(client):
    c, _mock = client
    fake = FakeDB(discovery_sources=[])
    _fake_db_client(c, fake)

    with patch("services.feed_discovery._is_safe_host", return_value=True):
        resp = c.post(
            f"{BASE}/sources",
            json={"items": [{"url": "https://d.example.org/list", "kind": "opml",
                             "label": "A directory"}]},
            headers=KEY,
        )

    assert resp.status_code == 200
    assert fake.upserts[0][2] == "url"
    assert fake.rows("discovery_sources")[0]["kind"] == "opml"


def test_update_source_requires_something_to_change(client):
    c, db = client
    assert c.patch(f"{BASE}/sources/{uuid4()}", json={}, headers=KEY).status_code == 400
    db.table.assert_not_called()


def test_update_unknown_source_is_404(client):
    c, _mock = client
    _fake_db_client(c, FakeDB(discovery_sources=[]))
    resp = c.patch(f"{BASE}/sources/{uuid4()}", json={"enabled": False}, headers=KEY)
    assert resp.status_code == 404


def test_update_source_toggles_enabled(client):
    c, _mock = client
    row = {"id": str(uuid4()), "url": "https://d.example/", "kind": "links_page",
           "enabled": True, "interval_hours": 168,
           "created_at": "2026-01-01T00:00:00+00:00",
           "updated_at": "2026-01-01T00:00:00+00:00"}
    _fake_db_client(c, FakeDB(discovery_sources=[row]))

    resp = c.patch(f"{BASE}/sources/{row['id']}", json={"enabled": False}, headers=KEY)
    assert resp.json()["enabled"] is False


# ── run / stats ──────────────────────────────────────────────────────────────

def test_run_is_503_while_discovery_is_disabled(client, monkeypatch):
    """The flag is a real kill switch here, unlike FEED_REFRESH_ENABLED which
    gates only the worker — otherwise 'disabled' is a claim an operator can't
    stand behind when a site owner writes in."""
    c, db = client
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "false")

    with patch("routers.admin_discovery.run_cycle") as run:
        resp = c.post(f"{BASE}/run", headers=KEY)

    assert resp.status_code == 503
    run.assert_not_called()
    db.table.assert_not_called()


def test_run_forwards_bounds(client, monkeypatch):
    c, _db = client
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")

    from services.discovery import CycleSummary

    async def fake_cycle(_db, **kwargs):
        fake_cycle.kwargs = kwargs
        return CycleSummary()

    with patch("routers.admin_discovery.run_cycle", side_effect=fake_cycle):
        resp = c.post(
            f"{BASE}/run?harvest_limit=5&probe_limit=7&max_concurrency=2", headers=KEY
        )

    assert resp.status_code == 200
    assert fake_cycle.kwargs["harvest_limit"] == 5
    assert fake_cycle.kwargs["probe_limit"] == 7
    assert fake_cycle.kwargs["max_concurrency"] == 2


def test_run_falls_back_to_env_defaults_when_bounds_are_omitted(client, monkeypatch):
    c, _db = client
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")

    from services.discovery import CycleSummary

    async def fake_cycle(_db, **kwargs):
        fake_cycle.kwargs = kwargs
        return CycleSummary()

    with patch("routers.admin_discovery.run_cycle", side_effect=fake_cycle):
        c.post(f"{BASE}/run", headers=KEY)

    assert fake_cycle.kwargs == {
        "harvest_limit": None, "probe_limit": None,
        "max_concurrency": None, "directory_limit": None,
    }


@pytest.mark.parametrize(
    "query", ["harvest_limit=0", "probe_limit=500", "max_concurrency=99",
              "directory_limit=0"]
)
def test_run_rejects_out_of_range_bounds(client, monkeypatch, query):
    c, _db = client
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")
    with patch("routers.admin_discovery.run_cycle") as run:
        assert c.post(f"{BASE}/run?{query}", headers=KEY).status_code == 422
    run.assert_not_called()


def test_stats_reports_every_status(client):
    c, _mock = client
    _fake_db_client(c, FakeDB(
        discovery_targets=[{"id": "1", "status": "pending"},
                           {"id": "2", "status": "done"}],
        discovery_candidates=[{"id": "c", "status": "pending"}],
        discovery_sources=[{"id": "s", "enabled": True}],
    ))

    body = c.get(f"{BASE}/stats", headers=KEY).json()
    assert body["targets_pending"] == 1
    assert body["targets_done"] == 1
    assert body["candidates_pending"] == 1
    assert body["sources_enabled"] == 1


def test_reload_defaults_reports_how_many_were_loaded(client):
    c, _mock = client
    _fake_db_client(c, FakeDB(discovery_sources=[]))
    body = c.post(f"{BASE}/sources/reload-defaults", headers=KEY).json()
    assert body["loaded"] > 0
