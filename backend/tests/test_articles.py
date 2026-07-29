from __future__ import annotations
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


@pytest.mark.parametrize("execute_return", [MagicMock(data=None), None])
def test_get_article_not_found_returns_404(client, execute_return):
    # Same postgrest-py gotcha as test_feeds.py: .single() raises on 0 rows,
    # and some maybe_single() versions return bare None from execute() rather
    # than a response object with data=None — both shapes must 404 cleanly.
    c, mock_db = client
    chain = mock_db.table.return_value.select.return_value.eq.return_value
    chain.maybe_single.return_value.execute.return_value = execute_return

    resp = c.get(f"/api/articles/{uuid4()}")

    assert resp.status_code == 404
    chain.single.assert_not_called()
    chain.maybe_single.assert_called_once()
