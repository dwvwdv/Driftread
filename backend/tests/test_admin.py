from __future__ import annotations
import os


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


def test_admin_import_from_url_rejects_private_ip(client):
    c, mock_db = client
    resp = c.post(
        "/api/admin/feeds/from-url",
        json={"feed_url": "http://10.0.0.5/internal"},
        headers={"x-api-key": os.environ["ADMIN_API_KEY"]},
    )
    assert resp.status_code == 400
    mock_db.table.assert_not_called()
