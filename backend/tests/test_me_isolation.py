"""Cross-user data isolation for every /me/* endpoint (TODO.md 技術與可靠性優化
"為登入後的 user-scoped API 加上跨使用者資料隔離測試").

None of these tables are protected by RLS against this backend's own queries —
the service_role client bypasses RLS entirely (see docs/FEATURES.md #5 and
TODO.md Phase 0's "使用者隔離目前仍全靠應用層手動加 user_id 條件"). The only
thing standing between one user and another user's subscriptions, read state,
bookmarks or preferences is every handler in routers/me.py (and
routers/opml.py's export) remembering to filter by `user.id` taken from the
verified JWT — never a client-supplied value, and never stale state left over
from a previous request.

Each test below calls the same endpoint twice against a shared mock_db, once
per user, and asserts the id threaded into the Supabase call is that
request's own authenticated user and differs between the two calls. A
regression that hardcodes a user id, reuses one across requests, or reads it
from the wrong place would make one of these calls silently look like the
other user, and none of the single-user tests in test_me.py would catch it.

Covers all of routers/me.py plus routers/opml.py (both import and export).
"""
from __future__ import annotations
import os
import time
from unittest.mock import MagicMock, patch

import jwt

from rss_parser import ParsedFeed

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-please-change-and-make-32-bytes-long")

USER_A = "user-aaaaaaaa"
USER_B = "user-bbbbbbbb"

FEED_ID = "11111111-1111-1111-1111-111111111111"
ARTICLE_ID = "22222222-2222-2222-2222-222222222222"


def _token(sub: str) -> str:
    return jwt.encode(
        {
            "sub": sub,
            "aud": "authenticated",
            "is_anonymous": False,
            "exp": int(time.time()) + 3600,
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def _auth(sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(sub)}"}


def _assert_isolated(call_args_list, *, index: int = 1) -> None:
    """Both calls happened, and the user id argument differs between them —
    the two prerequisites for "each request only ever touched its own
    caller's row", i.e. the exact filter value at `index` in each call's
    positional args. Default index 1 matches `.eq("user_id", <value>)` —
    index 0 is the column name literal, not the id."""
    assert len(call_args_list) == 2
    user_ids = [c[0][index] for c in call_args_list]
    assert user_ids == [USER_A, USER_B]


# --- Subscriptions -------------------------------------------------------


def test_list_subscriptions_scoped_per_user(client):
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.get("/api/me/feeds", headers=_auth(USER_A))
    r2 = c.get("/api/me/feeds", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    _assert_isolated(mock_db.table.return_value.select.return_value.eq.call_args_list)


def test_subscribe_writes_calling_users_id(client):
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": FEED_ID}]
    )
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.post(f"/api/me/feeds/{FEED_ID}", headers=_auth(USER_A))
    r2 = c.post(f"/api/me/feeds/{FEED_ID}", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 204

    upserted = [c_[0][0]["user_id"] for c_ in mock_db.table.return_value.upsert.call_args_list]
    assert upserted == [USER_A, USER_B]


def test_unsubscribe_scoped_per_user(client):
    c, mock_db = client
    mock_db.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[]
    )

    r1 = c.delete(f"/api/me/feeds/{FEED_ID}", headers=_auth(USER_A))
    r2 = c.delete(f"/api/me/feeds/{FEED_ID}", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 204

    _assert_isolated(mock_db.table.return_value.delete.return_value.eq.call_args_list)


# --- Read receipts ---------------------------------------------------------


def test_mark_read_writes_calling_users_id(client):
    c, mock_db = client
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.post(f"/api/me/articles/{ARTICLE_ID}/read", headers=_auth(USER_A))
    r2 = c.post(f"/api/me/articles/{ARTICLE_ID}/read", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 204

    upserted = [c_[0][0]["user_id"] for c_ in mock_db.table.return_value.upsert.call_args_list]
    assert upserted == [USER_A, USER_B]


def test_mark_unread_scoped_per_user(client):
    c, mock_db = client
    mock_db.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[]
    )

    r1 = c.delete(f"/api/me/articles/{ARTICLE_ID}/read", headers=_auth(USER_A))
    r2 = c.delete(f"/api/me/articles/{ARTICLE_ID}/read", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 204

    _assert_isolated(mock_db.table.return_value.delete.return_value.eq.call_args_list)


def test_list_reads_scoped_per_user(client):
    c, mock_db = client
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.order.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.get("/api/me/reads", headers=_auth(USER_A))
    r2 = c.get("/api/me/reads", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    _assert_isolated(mock_db.table.return_value.select.return_value.eq.call_args_list)


def test_mark_all_read_explicit_ids_write_calling_users_id(client):
    c, mock_db = client
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.post(
        "/api/me/reads/mark-all",
        json={"article_ids": [ARTICLE_ID]},
        headers=_auth(USER_A),
    )
    r2 = c.post(
        "/api/me/reads/mark-all",
        json={"article_ids": [ARTICLE_ID]},
        headers=_auth(USER_B),
    )
    assert r1.status_code == r2.status_code == 200

    rows = [c_[0][0] for c_ in mock_db.table.return_value.upsert.call_args_list]
    assert [row[0]["user_id"] for row in rows] == [USER_A, USER_B]


def test_mark_all_read_scoped_uses_calling_users_id(client):
    """No article_ids -> the server-computed-scope path, which goes through
    the mark_reading_stream_read RPC instead of a table upsert."""
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[{"marked": 0}])

    r1 = c.post("/api/me/reads/mark-all", json={}, headers=_auth(USER_A))
    r2 = c.post("/api/me/reads/mark-all", json={}, headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    rpc_calls = [c_ for c_ in mock_db.rpc.call_args_list if c_[0][0] == "mark_reading_stream_read"]
    assert [c_[0][1]["p_user_id"] for c_ in rpc_calls] == [USER_A, USER_B]


# --- Reading stream ---------------------------------------------------------


def test_stream_scoped_per_user(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.get("/api/me/stream", headers=_auth(USER_A))
    r2 = c.get("/api/me/stream", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    rpc_calls = [c_ for c_ in mock_db.rpc.call_args_list if c_[0][0] == "list_reading_stream"]
    assert [c_[0][1]["p_user_id"] for c_ in rpc_calls] == [USER_A, USER_B]


def test_stream_unread_counts_scoped_per_user(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.get("/api/me/stream/unread-counts", headers=_auth(USER_A))
    r2 = c.get("/api/me/stream/unread-counts", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    rpc_calls = [
        c_ for c_ in mock_db.rpc.call_args_list if c_[0][0] == "reading_stream_unread_counts"
    ]
    assert [c_[0][1]["p_user_id"] for c_ in rpc_calls] == [USER_A, USER_B]


# --- Bookmarks ---------------------------------------------------------------


def test_list_bookmarks_scoped_per_user(client):
    c, mock_db = client
    chain = mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value
    chain.order.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.get("/api/me/bookmarks", headers=_auth(USER_A))
    r2 = c.get("/api/me/bookmarks", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    _assert_isolated(mock_db.table.return_value.select.return_value.eq.call_args_list)


def test_add_bookmark_writes_calling_users_id(client):
    c, mock_db = client
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    body = {"article_id": ARTICLE_ID, "bookmark_type": "favorite"}
    r1 = c.post("/api/me/bookmarks", json=body, headers=_auth(USER_A))
    r2 = c.post("/api/me/bookmarks", json=body, headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 204

    upserted = [c_[0][0]["user_id"] for c_ in mock_db.table.return_value.upsert.call_args_list]
    assert upserted == [USER_A, USER_B]


def test_remove_bookmark_scoped_per_user(client):
    c, mock_db = client
    chain = mock_db.table.return_value.delete.return_value.eq.return_value.eq.return_value
    chain.eq.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.delete(f"/api/me/bookmarks/{ARTICLE_ID}", headers=_auth(USER_A))
    r2 = c.delete(f"/api/me/bookmarks/{ARTICLE_ID}", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 204

    _assert_isolated(mock_db.table.return_value.delete.return_value.eq.call_args_list)


# --- Preferences -------------------------------------------------------------


def test_get_preferences_scoped_per_user(client):
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.get("/api/me/preferences", headers=_auth(USER_A))
    r2 = c.get("/api/me/preferences", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    _assert_isolated(mock_db.table.return_value.select.return_value.eq.call_args_list)


def test_update_preferences_writes_calling_users_id(client):
    c, mock_db = client
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    body = {"preferred_categories": ["tech"], "preferred_languages": ["en"]}
    r1 = c.put("/api/me/preferences", json=body, headers=_auth(USER_A))
    r2 = c.put("/api/me/preferences", json=body, headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    upserted = [c_[0][0]["user_id"] for c_ in mock_db.table.return_value.upsert.call_args_list]
    assert upserted == [USER_A, USER_B]


# --- OPML import/export -------------------------------------------------------


def test_export_opml_scoped_per_user(client):
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    r1 = c.get("/api/me/export/opml", headers=_auth(USER_A))
    r2 = c.get("/api/me/export/opml", headers=_auth(USER_B))
    assert r1.status_code == r2.status_code == 200

    _assert_isolated(mock_db.table.return_value.select.return_value.eq.call_args_list)


def test_import_opml_subscribes_with_calling_users_id(client):
    """The one /me/* endpoint left out of the isolation batch above (see the
    module docstring) — it needs the external fetch path (validate_fetch_url,
    fetch_and_parse) mocked out rather than just the DB, since a successful
    import has to get past both before it ever writes to user_feeds."""
    c, mock_db = client
    feed_id_a = "33333333-3333-3333-3333-333333333333"
    feed_id_b = "44444444-4444-4444-4444-444444444444"
    # Two calls per import (feeds upsert, then user_feeds upsert), in that
    # order, once per request — both land on the same mock_db.table.upsert
    # mock regardless of which table name was passed.
    mock_db.table.return_value.upsert.return_value.execute.side_effect = [
        MagicMock(data=[{"id": feed_id_a}]),
        MagicMock(data=[{"user_id": USER_A, "feed_id": feed_id_a}]),
        MagicMock(data=[{"id": feed_id_b}]),
        MagicMock(data=[{"user_id": USER_B, "feed_id": feed_id_b}]),
    ]

    opml_body = b'<opml version="2.0"><body><outline text="f" xmlUrl="https://example.com/rss"/></body></opml>'
    parsed = ParsedFeed(title="Example", url="https://example.com/rss")

    async def fake_validate_fetch_url(url, **kw):
        return url

    async def fake_fetch_and_parse(url):
        return parsed

    with patch("routers.opml.validate_fetch_url", new=fake_validate_fetch_url), patch(
        "routers.opml.fetch_and_parse", new=fake_fetch_and_parse
    ):
        r1 = c.post(
            "/api/me/import/opml",
            headers=_auth(USER_A),
            files={"file": ("feeds.opml", opml_body, "text/x-opml")},
        )
        r2 = c.post(
            "/api/me/import/opml",
            headers=_auth(USER_B),
            files={"file": ("feeds.opml", opml_body, "text/x-opml")},
        )

    assert r1.status_code == r2.status_code == 200
    assert r1.json()["subscribed"] == 1
    assert r2.json()["subscribed"] == 1

    user_feeds_writes = [
        c_[0][0] for c_ in mock_db.table.return_value.upsert.call_args_list if "user_id" in c_[0][0]
    ]
    assert [row["user_id"] for row in user_feeds_writes] == [USER_A, USER_B]
