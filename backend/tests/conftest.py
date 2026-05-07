from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")


@pytest.fixture
def mock_db():
    mock = MagicMock()
    return mock


@pytest.fixture
def client(mock_db):
    with patch("database.get_client", return_value=mock_db):
        from main import app
        with TestClient(app) as c:
            yield c, mock_db
