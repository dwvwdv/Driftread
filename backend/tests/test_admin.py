from __future__ import annotations
import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from rss_parser import ConditionalFetch, ParsedFeed
from services.feed_refresh import RefreshResult


def test_admin_endpoint_rejects_missing_key(client):
    c, _ = client
    resp = c.post("/api/admin/feeds/from-url", json={"feed_url": "https://example.com/rss"})
    # x_api_key is a required Header(...), so FastAPI's request validation
    # rejects a missing header (422) before _require_api_key ever runs.
    assert resp.status_code == 422


def test_admin_endpoint_rejects_wrong_key(client):
    c, _ = client
    resp = c.post(
        "/api/admin/feeds/from-url",
        json={"feed_url": "https://example.com/rss"},
        headers={"x-api-key": "wrong-key"},
    )
    assert resp.status_code == 403


def test_admin_endpoint_rejects_non_ascii_key(client):
    # secrets.compare_digest() raises TypeError on non-ASCII str operands;
    # a malformed header must still 403, not 500. httpx's own TestClient
    # refuses to encode a non-ASCII str header value client-side, so send
    # it as a pre-encoded (latin-1, per HTTP header convention) byte header
    # instead — this is what actually reaches the server on the wire.
    c, _ = client
    resp = c.post(
        "/api/admin/feeds/from-url",
        json={"feed_url": "https://example.com/rss"},
        headers=[(b"x-api-key", "wrong-ké".encode("latin-1"))],
    )
    assert resp.status_code == 403


def test_admin_import_from_url_rejects_private_ip(client):
    c, mock_db = client
    resp = c.post(
        "/api/admin/feeds/from-url",
        json={"feed_url": "http://10.0.0.5/internal"},
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )
    assert resp.status_code == 400
    mock_db.table.assert_not_called()


def _mock_feed_lookup_miss(mock_db, execute_return):
    """Real postgrest-py's .single() raises on 0 rows instead of returning
    data=None (only .maybe_single() does that) — a MagicMock can't reproduce
    the raise, so callers assert .single() was never invoked instead. Some
    maybe_single() versions have also returned bare None from execute() on
    0 rows rather than a response object with data=None; both must 404."""
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.maybe_single.return_value.execute.return_value = execute_return
    return chain


@pytest.mark.parametrize("execute_return", [MagicMock(data=None), None])
def test_archive_feed_not_found_returns_404(client, execute_return):
    c, mock_db = client
    chain = _mock_feed_lookup_miss(mock_db, execute_return)

    resp = c.patch(
        f"/api/admin/feeds/{uuid4()}/archive",
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()


@pytest.mark.parametrize("execute_return", [MagicMock(data=None), None])
def test_unarchive_feed_not_found_returns_404(client, execute_return):
    c, mock_db = client
    chain = _mock_feed_lookup_miss(mock_db, execute_return)

    resp = c.patch(
        f"/api/admin/feeds/{uuid4()}/unarchive",
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()


@pytest.mark.parametrize("execute_return", [MagicMock(data=None), None])
def test_refresh_feed_not_found_returns_404(client, execute_return):
    c, mock_db = client
    chain = _mock_feed_lookup_miss(mock_db, execute_return)

    resp = c.post(
        f"/api/admin/feeds/{uuid4()}/refresh",
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()


def _found_feed(mock_db, feed_id):
    row = {
        "id": str(feed_id),
        "url": "https://example.com/feed.xml",
        "consecutive_failures": 0,
        "fetch_interval_minutes": 60,
        "etag": None,
        "last_modified": None,
    }
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.maybe_single.return_value.execute.return_value = MagicMock(data=row)
    return row


def test_refresh_feed_returns_502_when_fetch_fails(client):
    """The refactor onto services.feed_refresh must keep the failure contract:
    the feed's health row is updated, and the caller still sees a 502."""
    feed_id = uuid4()
    c, mock_db = client
    _found_feed(mock_db, feed_id)

    with patch(
        "services.feed_refresh.fetch_and_parse_conditional",
        side_effect=RuntimeError("unreachable"),
    ):
        resp = c.post(
            f"/api/admin/feeds/{feed_id}/refresh",
            headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
        )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Failed to fetch feed"


def test_refresh_feed_success_keeps_inserted_key(client):
    """`inserted` is part of the existing response contract (the browser
    extension and external scripts read it) — it must survive the refactor."""
    feed_id = uuid4()
    c, mock_db = client
    _found_feed(mock_db, feed_id)

    with (
        patch(
            "services.feed_refresh.fetch_and_parse_conditional",
            return_value=ConditionalFetch(
                not_modified=False,
                parsed=ParsedFeed(title="F", url="https://example.com", articles=[]),
            ),
        ),
        patch("services.feed_refresh.upsert_articles", return_value=4),
        patch("services.feed_refresh.count_articles", side_effect=[1, 5]),
    ):
        resp = c.post(
            f"/api/admin/feeds/{feed_id}/refresh",
            headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] == 4          # rows touched, as before
    assert body["new_articles"] == 4      # count delta 5 - 1
    assert body["total_articles"] == 5
    assert body["feed_id"] == str(feed_id)


def test_import_feeds_marks_new_feeds_due(client):
    """A bulk JSON import doesn't fetch inline, so it must schedule the feeds —
    otherwise they'd sit at 0 articles until someone refreshed each by hand."""
    c, mock_db = client
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

    resp = c.post(
        "/api/admin/feeds",
        json={"feeds": [{"title": "Example", "url": "https://example.com/rss"}]},
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )

    assert resp.status_code == 200
    payload = mock_db.table.return_value.upsert.call_args.args[0]
    assert payload["next_fetch_at"]


def test_refresh_due_requires_api_key(client):
    c, _ = client
    assert c.post("/api/admin/feeds/refresh-due").status_code == 422
    assert c.post(
        "/api/admin/feeds/refresh-due", headers={"x-api-key": "wrong"}
    ).status_code == 403


def test_refresh_due_returns_summary(client):
    c, mock_db = client
    with patch(
        "routers.admin.refresh_due",
        return_value=[
            RefreshResult(feed_id="a", status="updated", new_articles=2),
            RefreshResult(feed_id="b", status="not_modified"),
        ],
    ):
        resp = c.post(
            "/api/admin/feeds/refresh-due?limit=5&max_concurrency=2",
            headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "processed": 2,
        "updated": 1,
        "not_modified": 1,
        "failed": 0,
        "archived": 0,
        "new_articles": 2,
    }


def test_refresh_due_omitting_bounds_falls_back_to_env_defaults(client, monkeypatch):
    """Both bounds are optional — the worker calls refresh_due with no
    arguments, and this endpoint must be usable the same way."""
    monkeypatch.setenv("FEED_REFRESH_BATCH_SIZE", "11")
    monkeypatch.setenv("FEED_REFRESH_CONCURRENCY", "3")
    c, _ = client

    with patch("routers.admin.refresh_due", return_value=[]) as refresh:
        resp = c.post(
            "/api/admin/feeds/refresh-due",
            headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
        )

    assert resp.status_code == 200
    assert refresh.await_args.kwargs["limit"] == 11
    assert refresh.await_args.kwargs["max_concurrency"] == 3


def test_refresh_due_rejects_out_of_range_bounds(client):
    c, _ = client
    key = {"x-api-key": os.environ["ADMIN_API_KEY"]}
    assert c.post("/api/admin/feeds/refresh-due?limit=0", headers=key).status_code == 422
    assert c.post("/api/admin/feeds/refresh-due?limit=501", headers=key).status_code == 422
    assert (
        c.post("/api/admin/feeds/refresh-due?max_concurrency=21", headers=key).status_code
        == 422
    )


def test_list_all_feeds_requires_api_key(client):
    c, _ = client
    assert c.get("/api/admin/feeds").status_code == 422
    assert c.get("/api/admin/feeds", headers={"x-api-key": "wrong"}).status_code == 403


def test_list_all_feeds_includes_archived_by_default(client):
    c, mock_db = client
    chain = mock_db.table.return_value.select.return_value
    chain.range.return_value.order.return_value.execute.return_value = MagicMock(
        data=[], count=0
    )

    resp = c.get(
        "/api/admin/feeds?page=2&page_size=10",
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "page": 2, "page_size": 10}
    # No archived filter applied unless asked for — this is the "enumerate
    # everything" endpoint an external scheduler needs.
    chain.is_.assert_not_called()
    chain.not_.is_.assert_not_called()
    chain.range.assert_called_once_with(10, 19)
