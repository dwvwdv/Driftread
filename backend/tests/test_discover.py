from __future__ import annotations


def test_discover_import_rejects_private_url(client):
    c, mock_db = client
    resp = c.post("/api/discover/import", json={"feed_url": "http://127.0.0.1/secret"})
    assert resp.status_code == 400
    # Must fail before ever touching the database.
    mock_db.table.assert_not_called()


def test_discover_import_rejects_metadata_url(client):
    c, mock_db = client
    resp = c.post(
        "/api/discover/import",
        json={"feed_url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert resp.status_code == 400
    mock_db.table.assert_not_called()
