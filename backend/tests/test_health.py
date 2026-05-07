from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")


def test_health():
    mock_db = MagicMock()
    with patch("backend.database.get_client", return_value=mock_db):
        from backend.main import app
        with TestClient(app) as client:
            resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
