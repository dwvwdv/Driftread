from __future__ import annotations
from unittest.mock import MagicMock
from uuid import uuid4


def test_get_feed_not_found_returns_404(client):
    # Real postgrest-py's .single() raises APIError (PGRST116) when 0 rows
    # match, instead of returning data=None — only .maybe_single() does that.
    # A MagicMock can't reproduce the raise, so we assert the code calls the
    # right method: .maybe_single(), never .single().
    c, mock_db = client
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.maybe_single.return_value.execute.return_value = MagicMock(data=None)

    resp = c.get(f"/api/feeds/{uuid4()}")

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()
