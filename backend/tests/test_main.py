from __future__ import annotations

from main import MAX_REQUEST_BODY_BYTES


def test_oversized_request_body_rejected(client):
    c, mock_db = client
    oversized = b"x" * (MAX_REQUEST_BODY_BYTES + 1)
    resp = c.post(
        "/api/discover",
        content=oversized,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    # Rejected by Content-Length before the body is read or the route runs.
    mock_db.table.assert_not_called()


def test_small_request_reaches_the_route(client):
    c, mock_db = client
    # A well-formed, small request must pass the middleware and reach route
    # logic — proven by getting the route's own 400 (private-host rejection)
    # rather than the middleware's 413.
    resp = c.post("/api/discover/import", json={"feed_url": "http://127.0.0.1/secret"})
    assert resp.status_code == 400
    mock_db.table.assert_not_called()
