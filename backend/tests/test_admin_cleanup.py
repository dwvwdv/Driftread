from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest
import psycopg2
from fastapi import HTTPException
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


def test_confirmed_delete_uses_bounded_direct_transaction(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://cleanup-test')
    item = uuid4()
    result = {'items': [{'id': str(item), 'ok': True}], 'dry_run': False}
    conn = MagicMock(spec=['__enter__', '__exit__', 'cursor', 'close'])
    cursor = MagicMock(spec=['__enter__', '__exit__', 'execute', 'fetchone'])
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = (result,)
    db = Mock(spec=['rpc'])
    with patch('services.cleanup.psycopg2.connect', return_value=conn) as connect:
        report = apply_batch(db, CleanupBatch(kind='feeds', ids=[item, item],
                            action='delete', dry_run=False, confirm_delete=True))
    assert report == result
    connect.assert_called_once_with('postgresql://cleanup-test', connect_timeout=5)
    assert cursor.execute.call_args_list[0].args == ('SET LOCAL ROLE service_role',)
    assert cursor.execute.call_args_list[1].args == ("SET LOCAL statement_timeout = '45s'",)
    assert cursor.execute.call_args_list[2].args == ("SET LOCAL lock_timeout = '3s'",)
    assert cursor.execute.call_args.args[1] == ('feeds', [str(item)], 'delete', False, True)
    conn.__exit__.assert_called_once_with(None, None, None)
    conn.close.assert_called_once()
    db.rpc.assert_not_called()


@pytest.mark.parametrize('code', ['57014', '55P03', '40P01', '08006'])
def test_delete_db_errors_rollback_and_do_not_leak_details(monkeypatch, caplog, code):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://secret')
    class DatabaseError(psycopg2.DatabaseError):
        pgcode = code
    error = DatabaseError('private connection details')
    conn = MagicMock(spec=['__enter__', '__exit__', 'cursor', 'close'])
    cursor = MagicMock(spec=['__enter__', '__exit__', 'execute', 'fetchone'])
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.side_effect = error
    with patch('services.cleanup.psycopg2.connect', return_value=conn):
        with pytest.raises(HTTPException) as raised:
            apply_batch(Mock(spec=['rpc']), CleanupBatch(kind='feeds', ids=[uuid4()],
                        action='delete', dry_run=False, confirm_delete=True))
    assert raised.value.status_code == 503
    assert 'private' not in raised.value.detail + caplog.text
    conn.__exit__.assert_called_once_with(type(error), error, error.__traceback__)
    conn.close.assert_called_once()


def test_delete_without_database_url_fails_before_mutation(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    db = Mock(spec=['rpc'])
    with pytest.raises(HTTPException) as raised:
        apply_batch(db, CleanupBatch(kind='feeds', ids=[uuid4()], action='delete',
                    dry_run=False, confirm_delete=True))
    assert raised.value.status_code == 503
    db.rpc.assert_not_called()
