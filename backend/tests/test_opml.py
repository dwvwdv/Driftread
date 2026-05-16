from __future__ import annotations
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
            "exp": int(time.time()) + 3600,
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def test_export_opml(client):
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[
            {"feeds": {"title": "Feed A", "url": "https://a.example/rss", "website_url": "https://a.example"}},
            {"feeds": {"title": "Feed B", "url": "https://b.example/atom", "website_url": "https://b.example"}},
        ]
    )
    resp = c.get(
        "/api/me/export/opml",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "opml" in body
    assert "https://a.example/rss" in body
    assert "https://b.example/atom" in body


def test_import_opml_invalid_xml(client):
    c, _ = client
    resp = c.post(
        "/api/me/import/opml",
        headers={"Authorization": f"Bearer {_token()}"},
        files={"file": ("bad.opml", b"<not xml", "text/x-opml")},
    )
    assert resp.status_code == 400
