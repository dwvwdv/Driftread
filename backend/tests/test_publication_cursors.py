from datetime import datetime, timezone
from unittest.mock import MagicMock
import base64
import json
import pytest
from utils import cursor_scope, encode_keyset_cursor, encode_scoped_cursor, decode_scoped_cursor
from tests.test_articles import _article_row
from tests.test_me_isolation import USER_A, USER_B, _auth


def test_scope_is_stable_and_includes_every_filter():
    scope = cursor_scope("stream", user_id=USER_A, unread_only=False)
    assert scope == cursor_scope("stream", unread_only=False, user_id=USER_A)
    for other in [cursor_scope("stream", user_id=USER_B, unread_only=False),
                  cursor_scope("reads", user_id=USER_A, unread_only=False),
                  cursor_scope("stream", user_id=USER_A, unread_only=True)]:
        with pytest.raises(ValueError):
            decode_scoped_cursor(encode_scoped_cursor("position", scope), other)


@pytest.mark.parametrize("payload", [{"v": 2, "scope": "s", "position": "p"},
                                      {"v": 1, "scope": "s", "position": 3}, [], None])
def test_scoped_cursor_rejects_unknown_versions_and_shapes(payload):
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(ValueError):
        decode_scoped_cursor(cursor, "s")


def test_legacy_and_oversized_cursors_require_restart():
    for cursor in [encode_keyset_cursor(datetime.now(timezone.utc), _article_row()["id"]), "a" * 2049]:
        with pytest.raises(ValueError):
            decode_scoped_cursor(cursor, "s")


@pytest.mark.parametrize("target,changes", [
    ("/api/me/stream", {"unread_only": "true"}),
    ("/api/me/stream", {"feed_id": "22222222-2222-2222-2222-222222222222"}),
    ("/api/me/reads", {}),
])
def test_stream_cursor_rejects_changed_query_before_database(client, target, changes):
    c, db = client
    db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row(feed_title="source")])
    first = c.get("/api/me/stream", params={"limit": 1}, headers=_auth(USER_A))
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    db.reset_mock()
    result = c.get(target, params={"cursor": cursor, **changes}, headers=_auth(USER_A))
    assert result.status_code == 400
    db.rpc.assert_not_called()
    db.table.assert_not_called()


def test_stream_cursor_rejects_other_user_and_accepts_page_size_change(client):
    c, db = client
    db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row(feed_title="source")])
    first = c.get("/api/me/stream", params={"limit": 1}, headers=_auth(USER_A))
    cursor = first.json()["next_cursor"]
    db.reset_mock()
    assert c.get("/api/me/stream", params={"cursor": cursor}, headers=_auth(USER_B)).status_code == 400
    db.rpc.assert_not_called()
    db.rpc.return_value.execute.return_value = MagicMock(data=[])
    assert c.get("/api/me/stream", params={"cursor": cursor, "limit": 10}, headers=_auth(USER_A)).status_code == 200
    assert db.rpc.call_args[0][1]["p_user_id"] == USER_A


@pytest.mark.parametrize("changes", [{"q": "different"}, {"language": "zh"}])
def test_search_cursor_rejects_changed_query(client, changes):
    c, db = client
    db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row(feed_title="source", rank=0.5)])
    first = c.get("/api/search/articles", params={"q": "hello", "limit": 1})
    assert first.status_code == 200
    db.reset_mock()
    response = c.get("/api/search/articles", params={"q": "hello", "cursor": first.json()["next_cursor"], **changes})
    assert response.status_code == 400
    db.rpc.assert_not_called()


def test_feed_cursor_rejects_another_feed(client):
    c, db = client
    feed = _article_row()["feed_id"]
    db.rpc.return_value.execute.return_value = MagicMock(data=[_article_row()])
    first = c.get(f"/api/feeds/{feed}/articles", params={"limit": 1})
    db.reset_mock()
    response = c.get("/api/feeds/33333333-3333-3333-3333-333333333333/articles", params={"cursor": first.json()["next_cursor"]})
    assert response.status_code == 400
    db.rpc.assert_not_called()
