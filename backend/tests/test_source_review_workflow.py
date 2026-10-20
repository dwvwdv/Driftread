"""Admission settings, legacy bypass and durable worker hot reload invariants."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

import worker
from services.discovery import run_cycle
from services.job_queue import JobQueue
from services.settings import (
    SOURCE_REVIEW_KEY, SourceReviewValue, get_setting, load_source_review_settings,
    save_setting,
)
from tests.discovery_fakes import FakeDB
from tests.test_discovery_cycle import _db
from tests.worker_fakes import MemoryQueue


class RPCClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        return SimpleNamespace(data=next(self.replies))


def test_settings_default_off_and_hot_reload_without_cache():
    db = FakeDB(app_settings=[])
    assert get_setting(db, SOURCE_REVIEW_KEY).version == 0
    assert load_source_review_settings(db).enabled is False
    db.rows('app_settings').append({'key': SOURCE_REVIEW_KEY, 'version': 1,
                                      'value': {'enabled': True, 'skip_human_review': True}})
    assert load_source_review_settings(db).enabled is True
    assert load_source_review_settings(db).skip_human_review is True
    db.rows('app_settings')[0]['value']['enabled'] = False
    assert load_source_review_settings(db).enabled is False
    db.rows('app_settings')[0]['value']['enabled'] = 'false'
    with pytest.raises(ValidationError):
        load_source_review_settings(db)


@pytest.mark.parametrize('changes', [
    {'enabled': 'true'}, {'skip_human_review': 1}, {'engine': 'unknown'},
    {'sample_size': 0}, {'sample_size': 11}, {'sample_size': True},
    {'policy_text': '  '}, {'policy_text': 'x' * 8001},
    {'policy_text': 'bad\x00instruction'}, {'min_confidence': 1.1},
    {'min_quality': -0.1}, {'max_spam_probability': float('nan')},
    {'daily_budget_usd': -1}, {'daily_budget_usd': float('inf')},
    {'api_key': 'secret'}, {'provider_url': 'http://localhost'},
])
def test_settings_bound_and_forbid_secrets(changes):
    with pytest.raises(ValidationError):
        SourceReviewValue.model_validate(changes)


@pytest.mark.asyncio
async def test_saving_review_settings_does_not_resolve_or_fetch(monkeypatch):
    db = RPCClient([{'key': SOURCE_REVIEW_KEY, 'version': 1,
                     'value': SourceReviewValue(enabled=True).model_dump()}])
    fetch = AsyncMock(side_effect=AssertionError('must not fetch'))
    monkeypatch.setattr('services.settings.validate_fetch_url', fetch)
    saved = await save_setting(db, SOURCE_REVIEW_KEY, {'enabled': True}, 0)
    assert saved.value['enabled'] is True
    fetch.assert_not_awaited()
    assert db.calls[0][1]['p_seeds'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt', [False, True])
async def test_review_enabled_or_corrupt_prevents_legacy_bypass_but_preserves_manual(corrupt):
    db = _db(app_settings=[{'key': SOURCE_REVIEW_KEY, 'version': 1,
                            'value': {'enabled': 'true' if corrupt else True}}])
    with patch('services.discovery.build_host_index', return_value=None), \
         patch('services.discovery.probe_due', AsyncMock(return_value=[])), \
         patch('services.discovery.auto_promote_due') as auto, \
         patch('services.discovery.promote_approved', return_value=[{'id': 'manual'}]) as manual:
        result = await asyncio.wait_for(run_cycle(db), 1)
    auto.assert_not_called()
    manual.assert_called_once()
    assert result.imported == 1
    assert ('auto_promote' in result.errors) is corrupt


@pytest.mark.asyncio
async def test_queue_registers_review_timeout():
    db = RPCClient(['review-job'])
    assert await JobQueue(db).enqueue('source_review', 60) == 'review-job'
    assert db.calls[0][1]['p_timeout_seconds'] == 900
    assert db.calls[0][1]['p_singleton_key'] == 'scheduler'


@pytest.mark.asyncio
async def test_review_loop_disabled_then_hot_enabled_preserves_queue(monkeypatch):
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'true')
    monkeypatch.setattr(worker, 'POLL_SECONDS', 0.001)
    stop = asyncio.Event()
    db = FakeDB(app_settings=[])
    calls = []

    class TrackingQueue(MemoryQueue):
        async def reconcile(self):
            calls.append('reconcile')
            if calls.count('reconcile') == 2:
                db.rows('app_settings').append({'key': SOURCE_REVIEW_KEY, 'version': 1,
                                                 'value': {'enabled': True}})
            return 0

        async def enqueue(self, kind, interval):
            calls.append('enqueue')

        async def claim(self, kind, worker_id):
            calls.append('claim')
            return await super().claim(kind, worker_id)

    async def action(_db):
        calls.append('prepare')
        stop.set()
        return {'processed': 1, 'failed': 0}

    async def pulse(done):
        await done.wait()

    recorder = SimpleNamespace(worker_id='review-worker', pulse=AsyncMock(side_effect=pulse),
                               begin=AsyncMock(return_value='run'), finish=AsyncMock())
    with patch.object(worker, 'get_client', return_value=db), \
         patch.object(worker, 'JobQueue', TrackingQueue), \
         patch.object(worker, '_prepare_source_reviews', action), \
         patch.object(worker, 'OperationsRecorder', return_value=recorder):
        await asyncio.wait_for(worker.run_source_review_forever(stop), 1)
    assert calls == ['reconcile', 'reconcile', 'enqueue', 'claim', 'prepare']
    recorder.finish.assert_awaited_once_with('run', 'succeeded', {'processed': 1, 'failed': 0}, None)


@pytest.mark.asyncio
async def test_corrupt_review_settings_fail_closed_independently_of_discovery(monkeypatch):
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'true')
    db = FakeDB(app_settings=[{'key': SOURCE_REVIEW_KEY, 'version': 1,
                              'value': {'enabled': 'true'}}])
    with pytest.raises(ValidationError):
        await worker._source_review_enabled(db)
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'false')
    with pytest.raises(ValidationError):
        await worker._source_review_enabled(db)


@pytest.mark.asyncio
async def test_corrupt_hot_settings_do_not_enqueue_or_claim(monkeypatch):
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'true')
    stop = asyncio.Event()
    calls = []

    class TrackingQueue(MemoryQueue):
        async def enqueue(self, kind, interval):
            calls.append('enqueue')

        async def claim(self, kind, worker_id):
            calls.append('claim')

    async def unavailable_settings(_db):
        stop.set()
        raise RuntimeError('settings unavailable')

    async def pulse(done):
        await done.wait()

    recorder = SimpleNamespace(worker_id='review-worker', pulse=AsyncMock(side_effect=pulse),
                               begin=AsyncMock(return_value='run'), finish=AsyncMock())
    with patch.object(worker, 'get_client', return_value=None), \
         patch.object(worker, 'JobQueue', TrackingQueue), \
         patch.object(worker, '_source_review_enabled', unavailable_settings), \
         patch.object(worker, '_prepare_source_reviews', AsyncMock()) as prepare, \
         patch.object(worker, 'OperationsRecorder', return_value=recorder):
        await asyncio.wait_for(worker.run_source_review_forever(stop), 1)
    assert calls == []
    prepare.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('discovery_enabled', [False, True])
async def test_main_always_starts_independent_review_monitor(monkeypatch, discovery_enabled):
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', str(discovery_enabled).lower())
    monkeypatch.setenv('FEED_REFRESH_ENABLED', 'false')
    monkeypatch.setenv('ARTICLE_RETENTION_ENABLED', 'false')
    with patch.object(worker, 'run_discovery_forever', AsyncMock()) as discovery, \
         patch.object(worker, 'run_source_review_forever', AsyncMock()) as review, \
         patch.object(worker, 'run_storage_forever', AsyncMock()) as storage:
        assert await worker.main() == 0
    assert discovery.await_count == int(discovery_enabled)
    review.assert_awaited_once()
    storage.assert_awaited_once()
    assert review.call_args.args[0] is storage.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('discovery,paused,review,skip', [
    (d, p, r, s) for d in (False, True) for p in (False, True)
    for r in (False, True) for s in (False, True)
])
async def test_only_review_enabled_controls_scheduling(monkeypatch, discovery, paused, review, skip):
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', str(discovery).lower())
    db = FakeDB(app_settings=[
        {'key': 'discovery.control', 'version': 3, 'value': {'enabled': not paused}},
        {'key': SOURCE_REVIEW_KEY, 'version': 8, 'value': {'enabled': review, 'skip_human_review': skip}},
    ])
    assert await worker._source_review_enabled(db) is review
    assert await worker.discovery_can_start(db) is (discovery and not paused)


@pytest.mark.asyncio
async def test_paused_discovery_prepares_existing_candidate_but_disabled_review_does_not(monkeypatch):
    from services import source_review as service
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'false')
    db = FakeDB(app_settings=[
        {'key': 'discovery.control', 'version': 1, 'value': {'enabled': False}},
        {'key': SOURCE_REVIEW_KEY, 'version': 1, 'value': {'enabled': True}},
    ])
    preparing = {'id': 'review', 'candidate_id': 'candidate', 'policy': {'sample_size': 5}, 'lease_token': 'lease'}
    calls = []

    def rpc(_db, name, params):
        calls.append(name)
        return {'prune_source_reviews': {}, 'request_due_source_reviews': {'requested': 1},
                'claim_source_review': preparing if params.get('p_preparing') else None,
                'prepare_source_review': {'status': 'ready', 'engine': 'external_agent'}}[name]

    with patch.object(service, '_rpc', side_effect=rpc), \
         patch.object(service, 'get_candidate', return_value={'id': 'candidate'}), \
         patch.object(service, '_fetch_snapshot', AsyncMock(return_value={'samples': []})) as fetch:
        result = await service.prepare_due_reviews(db, limit=1)
        assert result == {'requested': 1, 'prepared': 1, 'failed': 0, 'submitted': 0}
        fetch.assert_awaited_once()
        calls.clear()
        db.rows('app_settings')[1]['value']['enabled'] = False
        assert (await service.prepare_due_reviews(db))['prepared'] == 0
        assert calls == ['prune_source_reviews']
