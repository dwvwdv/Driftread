from unittest.mock import Mock

import pytest

from auth import AuthUser, get_current_user
from routers.sync import decode_sync_cursor, sync_cursor


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
