import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from services import discovery_control as control
from services.settings import DiscoveryControlValue
from tests.discovery_fakes import FakeDB


def db(enabled=True, version=1):
    return FakeDB(app_settings=[{'key': 'discovery.control', 'value': {'enabled': enabled}, 'version': version}])


def test_persisted_switch_and_deployment_ceiling(monkeypatch):
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'true')
    database = db(False)
    assert control.read_control(database)['effective_enabled'] is False
    database.rows('app_settings')[0]['value']['enabled'] = True
    assert control.read_control(database)['effective_enabled'] is True
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'false')
    assert control.read_control(database)['enabled'] is True
    assert control.read_control(database)['effective_enabled'] is False
    assert control.runtime_enabled(FakeDB(app_settings=[])) is True


@pytest.mark.parametrize('value', [{}, {'enabled': 'true'}, {'enabled': 1}])
def test_corrupt_control_does_not_enable(value):
    database = db()
    database.rows('app_settings')[0]['value'] = value
    with pytest.raises(ValidationError):
        control.runtime_enabled(database)


@pytest.mark.asyncio
async def test_paused_cycle_starts_no_work():
    action = AsyncMock()
    with pytest.raises(control.DiscoveryPaused):
        await control.while_enabled(db(False), action)
    action.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_and_manual_cycle_observe_pause_without_restarting(monkeypatch):
    import worker
    from services.discovery import run_cycle
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'true')
    database = db(False)
    assert not await worker.discovery_can_start(database)
    assert not await worker._source_review_enabled(database)
    with patch('services.discovery._run_cycle') as action:
        assert (await run_cycle(database)).paused is True
    action.assert_not_called()
    database.rows('app_settings')[0]['value']['enabled'] = True
    assert await worker.discovery_can_start(database)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_active_cycle_cancels_on_pause_or_read_failure(monkeypatch, failure):
    monkeypatch.setattr(control, 'POLL_SECONDS', 0.001)
    database = db()
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def action():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    task = asyncio.create_task(control.while_enabled(database, action))
    await asyncio.wait_for(entered.wait(), 1)
    if failure:
        monkeypatch.setattr(control, 'runtime_enabled', lambda db: (_ for _ in ()).throw(RuntimeError('database unavailable')))
    else:
        database.rows('app_settings')[0]['value']['enabled'] = False
    with pytest.raises(RuntimeError if failure else control.DiscoveryPaused):
        await asyncio.wait_for(task, 1)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_resume_and_conflict_without_lost_update():
    with patch.object(control, 'save_setting', return_value=None):
        with pytest.raises(control.ControlError) as exc:
            await control.update_control(db(False), True, 1)
    assert exc.value.status_code == 409
    assert await control.while_enabled(db(True), AsyncMock(return_value='resumed')) == 'resumed'


def test_control_rest_auth_validation_and_pause_rejects_manual_run(client, monkeypatch):
    http, _ = client
    key = {'X-API-Key': 'test-admin-key'}
    assert http.get('/api/admin/discovery/control', headers={'X-API-Key': 'wrong'}).status_code == 403
    assert http.patch('/api/admin/discovery/control', headers=key, json={'enabled': 'false', 'expected_version': 1}).status_code == 422
    from main import app
    from database import get_client
    app.dependency_overrides[get_client] = lambda: db(False)
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'true')
    assert http.get('/api/admin/discovery/control', headers=key).json()['effective_enabled'] is False
    with patch('routers.admin_discovery.run_cycle') as run:
        assert http.post('/api/admin/discovery/run', headers=key).status_code == 503
    run.assert_not_called()
