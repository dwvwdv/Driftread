from __future__ import annotations
import base64
import os
import time
from unittest.mock import MagicMock

import jwt

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-please-change-and-make-32-bytes-long")


def _token() -> str:
    return jwt.encode(
        {
            "sub": "user-abc",
            "aud": "authenticated",
            "is_anonymous": False,
            "exp": int(time.time()) + 3600,
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def test_list_bookmarks_omits_article_content(client):
    """Bookmark rows only need summary fields for the list view; returning the
    full cached article HTML in `content` would bloat every fetch for data the
    list never renders."""
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(
        data=[
            {
                "articles": {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "feed_id": "22222222-2222-2222-2222-222222222222",
                    "title": "Some Article",
                    "url": "https://example.com/a",
                    "summary": "short summary",
                    "author": "Jane",
                    "published_at": None,
                }
            }
        ]
    )
    resp = c.get(
        "/api/me/bookmarks",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["title"] == "Some Article"
    assert "content" not in body[0]
    assert "fetched_at" not in body[0]

    select_args = mock_db.table.return_value.select.call_args[0][0]
    assert "content" not in select_args


def test_list_bookmarks_rejects_invalid_type(client):
    c, _ = client
    resp = c.get(
        "/api/me/bookmarks",
        params={"bookmark_type": "not-a-real-type"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 400


def _reads_chain(mock_db, with_cursor: bool = False):
    query = mock_db.table.return_value.select.return_value.eq.return_value
    if with_cursor:
        query = query.or_.return_value
    return query.order.return_value.order.return_value.limit.return_value


def test_list_reads_returns_next_cursor_on_a_full_page(client):
    # A full page (len(items) == limit) means there may be more rows, so the
    # route must hand back a cursor the caller can use to fetch the next one.
    c, mock_db = client
    _reads_chain(mock_db).execute.return_value = MagicMock(
        data=[
            {
                "article_id": "11111111-1111-1111-1111-111111111111",
                "read_at": "2026-08-14T10:00:00+00:00",
            }
        ]
    )

    resp = c.get(
        "/api/me/reads",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] is not None
    mock_db.table.return_value.select.return_value.eq.return_value.or_.assert_not_called()


def test_list_reads_omits_next_cursor_on_a_partial_page(client):
    # Fewer rows than `limit` means this is the last page.
    c, mock_db = client
    _reads_chain(mock_db).execute.return_value = MagicMock(data=[])

    resp = c.get(
        "/api/me/reads",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "next_cursor": None}


def test_list_reads_applies_keyset_filter_from_cursor(client):
    c, mock_db = client
    cursor = base64.urlsafe_b64encode(
        b"2026-08-14T10:00:00+00:00|11111111-1111-1111-1111-111111111111"
    ).decode()
    _reads_chain(mock_db, with_cursor=True).execute.return_value = MagicMock(data=[])

    resp = c.get(
        "/api/me/reads",
        params={"cursor": cursor},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    or_query = mock_db.table.return_value.select.return_value.eq.return_value.or_
    or_query.assert_called_once()
    filter_expr = or_query.call_args[0][0]
    assert "read_at.lt." in filter_expr
    assert "11111111-1111-1111-1111-111111111111" in filter_expr


def test_list_reads_rejects_malformed_cursor(client):
    c, _ = client
    resp = c.get(
        "/api/me/reads",
        params={"cursor": "not-valid-base64!!"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 400


# --- mark unread / mark-all-as-read ------------------------------------------


def test_mark_unread_deletes_the_read_receipt(client):
    c, mock_db = client
    chain = mock_db.table.return_value.delete.return_value.eq.return_value.eq.return_value
    chain.execute.return_value = MagicMock(data=[])

    resp = c.delete(
        "/api/me/articles/11111111-1111-1111-1111-111111111111/read",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 204
    mock_db.table.assert_any_call("user_article_reads")
    args, _ = mock_db.table.return_value.delete.return_value.eq.call_args_list[0]
    assert args == ("user_id", "user-abc")


def test_mark_all_read_with_article_ids_upserts_exactly_those(client):
    c, mock_db = client
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    resp = c.post(
        "/api/me/reads/mark-all",
        json={
            "article_ids": [
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ]
        },
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"marked": 2}
    args, kwargs = mock_db.table.return_value.upsert.call_args
    rows = args[0]
    assert len(rows) == 2
    assert all(row["user_id"] == "user-abc" for row in rows)
    assert kwargs["on_conflict"] == "user_id,article_id"
    # The explicit-ids path never needs the scoped RPC.
    mock_db.rpc.assert_not_called()


def test_mark_all_read_without_ids_uses_scoped_rpc(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[{"marked": 7}])

    resp = c.post(
        "/api/me/reads/mark-all",
        json={"feed_id": "33333333-3333-3333-3333-333333333333"},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"marked": 7}
    name, params = mock_db.rpc.call_args[0]
    assert name == "mark_reading_stream_read"
    assert params["p_user_id"] == "user-abc"
    assert params["p_feed_id"] == "33333333-3333-3333-3333-333333333333"
    assert params["p_before"] is None
    # The batch-upsert path never fires when there's no explicit id list.
    mock_db.table.return_value.upsert.assert_not_called()


def test_mark_all_read_without_ids_or_scope_marks_everything(client):
    """No article_ids, no feed_id, no before: an explicit "mark the whole
    stream read" — still routed through the RPC, just with both filters
    left NULL server-side."""
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[{"marked": 42}])

    resp = c.post(
        "/api/me/reads/mark-all",
        json={},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"marked": 42}
    _, params = mock_db.rpc.call_args[0]
    assert params["p_feed_id"] is None
    assert params["p_before"] is None


def test_mark_all_read_handles_empty_rpc_result(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[])

    resp = c.post(
        "/api/me/reads/mark-all",
        json={},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"marked": 0}


# --- reading stream -----------------------------------------------------------


def _stream_row(**overrides):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "feed_id": "22222222-2222-2222-2222-222222222222",
        "feed_title": "Some Feed",
        "title": "Some Article",
        "url": "https://example.com/a",
        "summary": "short summary",
        "author": "Jane",
        "published_at": "2026-08-14T10:00:00+00:00",
        "fetched_at": "2026-08-14T10:05:00+00:00",
        "is_read": False,
        "read_at": None,
    }
    row.update(overrides)
    return row


def test_list_stream_returns_next_cursor_on_a_full_page(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_stream_row()])

    resp = c.get(
        "/api/me/stream",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["feed_title"] == "Some Feed"
    assert body["next_cursor"] is not None


def test_list_stream_omits_next_cursor_on_a_partial_page(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[_stream_row()])

    resp = c.get(
        "/api/me/stream",
        params={"limit": 30},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    assert resp.json()["next_cursor"] is None


def test_list_stream_rejects_malformed_cursor(client):
    c, _ = client
    resp = c.get(
        "/api/me/stream",
        params={"cursor": "not-valid-base64!!"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 400


def test_list_stream_passes_feed_and_unread_filters_to_rpc(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[])

    resp = c.get(
        "/api/me/stream",
        params={
            "feed_id": "22222222-2222-2222-2222-222222222222",
            "unread_only": "true",
        },
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    name, params = mock_db.rpc.call_args[0]
    assert name == "list_reading_stream"
    assert params["p_feed_id"] == "22222222-2222-2222-2222-222222222222"
    assert params["p_unread_only"] is True
    assert params["p_user_id"] == "user-abc"


def test_list_stream_decodes_cursor_into_rpc_params(client):
    c, mock_db = client
    cursor = base64.urlsafe_b64encode(
        b"2026-08-14T10:00:00+00:00|11111111-1111-1111-1111-111111111111"
    ).decode()
    mock_db.rpc.return_value.execute.return_value = MagicMock(data=[])

    resp = c.get(
        "/api/me/stream",
        params={"cursor": cursor},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    _, params = mock_db.rpc.call_args[0]
    assert params["p_cursor_sort_at"] == "2026-08-14T10:00:00+00:00"
    assert params["p_cursor_id"] == "11111111-1111-1111-1111-111111111111"


def test_stream_unread_counts_sums_total(client):
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(
        data=[
            {"feed_id": "22222222-2222-2222-2222-222222222222", "feed_title": "A", "unread_count": 3},
            {"feed_id": "33333333-3333-3333-3333-333333333333", "feed_title": "B", "unread_count": 0},
        ]
    )

    resp = c.get(
        "/api/me/stream/unread-counts",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_unread"] == 3
    assert len(body["feeds"]) == 2
    name, params = mock_db.rpc.call_args[0]
    assert name == "reading_stream_unread_counts"
    assert params == {"p_user_id": "user-abc"}
