from __future__ import annotations
from unittest.mock import MagicMock
from uuid import uuid4


def test_get_article_not_found_returns_404(client):
    # Same postgrest-py gotcha as test_feeds.py: .single() raises on 0 rows
    # instead of returning data=None, so the not-found branch must use
    # .maybe_single() to actually be reachable.
    c, mock_db = client
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.maybe_single.return_value.execute.return_value = MagicMock(data=None)

    resp = c.get(f"/api/articles/{uuid4()}")

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()
