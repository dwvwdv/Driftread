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


def test_import_opml_rejects_private_outline_url(client):
    c, mock_db = client
    opml = b"""<opml version="2.0"><body>
      <outline text="internal" xmlUrl="http://127.0.0.1:8000/admin"/>
    </body></opml>"""
    resp = c.post(
        "/api/me/import/opml",
        headers={"Authorization": f"Bearer {_token()}"},
        files={"file": ("feeds.opml", opml, "text/x-opml")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 0
    assert len(body["failed"]) == 1
    assert "127.0.0.1" in body["failed"][0]
    # The unsafe URL must never reach the feeds table.
    mock_db.table.assert_not_called()


def test_import_opml_caps_outline_count(client):
    c, _ = client
    outlines = "".join(
        f'<outline text="f{i}" xmlUrl="http://127.0.0.1/{i}"/>' for i in range(250)
    )
    opml = f'<opml version="2.0"><body>{outlines}</body></opml>'.encode()
    resp = c.post(
        "/api/me/import/opml",
        headers={"Authorization": f"Bearer {_token()}"},
        files={"file": ("feeds.opml", opml, "text/x-opml")},
    )
    assert resp.status_code == 200
    body = resp.json()
    # All rejected (private IP), but capped at MAX_OPML_OUTLINES regardless.
    assert len(body["failed"]) == 200
