from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.cleanup import CleanupBatch, apply_batch


def test_delete_confirmation_and_batch_bounds():
    with pytest.raises(ValidationError):
        CleanupBatch(kind='articles', ids=[uuid4()], action='delete', dry_run=False)
    with pytest.raises(ValidationError):
        CleanupBatch(kind='feeds', ids=[uuid4()] * 51, action='stage')


def test_preview_default_and_explicit_ids_only():
    item = uuid4()
    request = Mock(spec=['execute'])
    request.execute.return_value = SimpleNamespace(data={'items': []})
    db = Mock(spec=['rpc'])
    db.rpc.return_value = request
    apply_batch(db, CleanupBatch(kind='feeds', ids=[item, item], action='stage'))
    assert db.rpc.call_args.args[1] == {'p_kind': 'feeds', 'p_ids': [str(item)], 'p_action': 'stage', 'p_dry_run': True, 'p_confirm_delete': False}


def test_cleanup_rest_requires_admin(client):
    http, _ = client
    headers = {'X-API-Key': 'wrong-key'}
    assert http.get('/api/admin/cleanup/items', headers=headers).status_code == 403
    assert http.get('/api/admin/discovery/pending-targets', headers=headers).status_code == 403
    assert http.post('/api/admin/discovery/targets/remove', headers=headers, json={'ids': [str(uuid4())]}).status_code == 403


def test_cleanup_rest_validation_and_shared_adapter(client):
    http, db = client
    headers = {'X-API-Key': 'test-admin-key'}
    assert http.get('/api/admin/cleanup/items?limit=51', headers=headers).status_code == 422
    assert http.post('/api/admin/cleanup/batch', headers=headers, json={
        'kind': 'feeds', 'ids': [str(uuid4())], 'action': 'delete', 'dry_run': False,
    }).status_code == 422
    with patch('services.cleanup.list_items', return_value={'items': [], 'total': 0}) as listing:
        assert http.get('/api/admin/cleanup/items?kind=articles&rule=title', headers=headers).status_code == 200
        listing.assert_called_once_with(db, 'articles', 'title', 'active', '', 50, 0)
