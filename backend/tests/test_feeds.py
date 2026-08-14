from __future__ import annotations
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


@pytest.mark.parametrize("execute_return", [MagicMock(data=None), None])
def test_get_feed_not_found_returns_404(client, execute_return):
    # Real postgrest-py's .single() raises APIError (PGRST116) when 0 rows
    # match, instead of returning data=None — only .maybe_single() does that.
    # Some postgrest-py versions have also shipped maybe_single().execute()
    # returning bare None (rather than a response object with data=None) on
    # 0 rows, so both shapes must be handled without raising AttributeError.
    c, mock_db = client
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.maybe_single.return_value.execute.return_value = execute_return

    resp = c.get(f"/api/feeds/{uuid4()}")

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()


def test_list_categories_uses_db_side_dedup(client):
    # The dedup, null-filtering and sort all happen in
    # list_feed_categories() (migration 011) now, not in Python — this
    # asserts the route calls the RPC rather than falling back to
    # `.table("feeds").select(...)`.
    c, mock_db = client
    mock_db.rpc.return_value.execute.return_value = MagicMock(
        data=[{"category": "News"}, {"category": "Tech"}]
    )

    resp = c.get("/api/feeds/categories")

    assert resp.status_code == 200
    assert resp.json() == ["News", "Tech"]
    mock_db.rpc.assert_called_once_with("list_feed_categories", {})
    mock_db.table.assert_not_called()
