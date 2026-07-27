from __future__ import annotations
import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-please-change")


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """rate_limit._hits is process-global state shared across every test via
    the single imported module — without clearing it, an earlier test's
    requests count toward a later test's quota (they share TestClient's fixed
    fake client IP), making rate-limit assertions order-dependent."""
    from rate_limit import _hits

    _hits.clear()
    yield


@pytest.fixture
def client(mock_db):
    """TestClient with database.get_client overridden via FastAPI's
    dependency_overrides — patching the function with a MagicMock breaks
    FastAPI introspection (MagicMock's signature is *args, **kwargs)."""
    from main import app
    from database import get_client

    app.dependency_overrides[get_client] = lambda: mock_db
    try:
        with TestClient(app) as c:
            yield c, mock_db
    finally:
        app.dependency_overrides.pop(get_client, None)
