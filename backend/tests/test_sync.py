from unittest.mock import Mock

import pytest

from auth import AuthUser, get_current_user
from routers.sync import decode_sync_cursor, sync_cursor


def test_offline_replay_auth_validation_owner_scope_and_revocation(client):
    c, db = client
    operation = {'article_id': '11111111-1111-1111-1111-111111111111', 'kind': 'favorite', 'enabled': True}
    assert c.post('/api/me/sync/operations', json=operation).status_code == 401
    db.rpc.assert_not_called()
    from main import app
    app.dependency_overrides[get_current_user] = lambda: AuthUser('owner')
    try:
        for invalid in ({**operation, 'user_id': 'other'}, {**operation, 'kind': 'subscribe'}, {**operation, 'enabled': 'true'}):
            assert c.post('/api/me/sync/operations', json=invalid).status_code == 422
        db.rpc.assert_not_called()
        call = Mock(spec=['execute']); call.execute.return_value = Mock(data='applied')
        db.rpc.return_value = call
        for user_id in ('owner', 'other'):
            app.dependency_overrides[get_current_user] = lambda uid=user_id: AuthUser(uid)
            assert c.post('/api/me/sync/operations', json=operation).status_code == 204
            db.rpc.assert_called_with('replay_personal_article_state', {
                'p_user_id': user_id, 'p_article_id': operation['article_id'], 'p_kind': 'favorite', 'p_enabled': True})
        call.execute.return_value = Mock(data='unavailable')
        assert c.post('/api/me/sync/operations', json=operation).status_code == 404
        call.execute.return_value = Mock(data='retry')
        busy = c.post('/api/me/sync/operations', json=operation)
        assert busy.status_code == 503
        assert busy.headers['retry-after'] == '1'
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_sync_cursor_rejects_other_account_and_malformed_input():
    from fastapi import HTTPException
    cursor = sync_cursor('owner', 123)
    assert decode_sync_cursor(cursor, 'owner') == 123
    for value in (cursor, '[]', 'e30', '%%%'):
        with pytest.raises(HTTPException):
            decode_sync_cursor(value, 'another')


def test_sync_requires_auth_and_does_not_trust_cursor_user(client):
    c, db = client
    assert c.get('/api/me/sync').status_code == 401
    db.rpc.assert_not_called()
    from main import app
    app.dependency_overrides[get_current_user] = lambda: AuthUser('owner')
    try:
        assert c.get('/api/me/sync?cursor=' + sync_cursor('other', 2)).status_code == 400
        db.rpc.assert_not_called()
        call = Mock(spec=['execute']); call.execute.return_value = Mock(data={
            'sequence':5,'changed':True,'reset':False,'items':[],'cache_limit':100})
        db.rpc.return_value = call
        response = c.get('/api/me/sync?cursor=' + sync_cursor('owner', 2))
        assert response.status_code == 200
        assert response.json()['account_id'] == 'owner'
        assert decode_sync_cursor(response.json()['cursor'],'owner') == 5
        db.rpc.assert_called_once_with('personal_sync_snapshot', {'p_user_id':'owner','p_since':2,'p_pending_ids':[]})
        assert response.headers['cache-control'] == 'private, no-store'
    finally:
        app.dependency_overrides.pop(get_current_user,None)
