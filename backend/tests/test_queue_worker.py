"""Lease loss, queue outages and bounded lifecycle checks use explicit protocols."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import main
import worker
from services.job_queue import Job, JobQueue
from services import watchdog
from tests.worker_fakes import MemoryQueue


class RPCClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(data=reply)


@pytest.mark.asyncio
async def test_adapter_parses_claim_and_requires_literal_boolean_ack():
    db = RPCClient(['j',[{'id':'j','lease_token':'token','kind':'refresh','attempts':2,'payload':{},'timeout_seconds':1800}],True,False,4])
    queue = JobQueue(db)
    assert await queue.enqueue('refresh',300) == 'j'
    job = await queue.claim('refresh','worker')
    assert job == Job('j','token','refresh',2,{})
    assert await queue.renew(job) is True
    assert await queue.finish(job,True) is False
    assert await queue.reconcile() == 4
    assert [name for name,_ in db.calls] == ['enqueue_background_job','claim_background_job',
        'renew_background_job','finish_background_job','reconcile_background_jobs']
    assert db.calls[0][1]['p_repeat_seconds'] == 300


@pytest.mark.asyncio
async def test_lease_loss_cancels_action_and_never_acknowledges(monkeypatch):
    monkeypatch.setattr(worker,'RENEW_SECONDS',0.001)
    cancelled = asyncio.Event()
    class LostQueue(MemoryQueue):
        async def renew(self, job):
            return False
    async def action(db):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    with pytest.raises(worker.LeaseLost):
        await worker._leased_action(LostQueue(None), Job('j','t','refresh',1,{}), action, None)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_queue_outage_starts_no_action(monkeypatch):
    stop = asyncio.Event()
    invoked=[]
    class DownQueue(MemoryQueue):
        async def reconcile(self):
            stop.set()
            raise RuntimeError('private database details')
    async def action(db):
        invoked.append(True)
    with patch.object(worker,'JobQueue',DownQueue), patch.object(worker,'get_client',return_value=RPCClient([])), \
         patch.object(worker,'OperationsRecorder') as recorder:
        async def pulse(done):
            await done.wait()
        recorder.return_value.pulse.side_effect=pulse
        await worker._run_loop(stop,'refresh',0,action,worker.summarize)
    assert invoked == []


@pytest.mark.asyncio
async def test_stop_during_claim_starts_no_action(monkeypatch):
    stop=asyncio.Event()
    invoked=[]
    class StopQueue(MemoryQueue):
        async def claim(self, kind, worker_id):
            stop.set()
            return await super().claim(kind,worker_id)
    async def action(db):
        invoked.append(True)
    with patch.object(worker,'JobQueue',StopQueue), patch.object(worker,'get_client',return_value=RPCClient([])), \
         patch.object(worker,'OperationsRecorder') as recorder:
        async def pulse(done):
            await done.wait()
        recorder.return_value.pulse.side_effect=pulse
        await worker._run_loop(stop,'refresh',0,action,worker.summarize)
    assert invoked == []
    recorder.return_value.begin.assert_not_called()


@pytest.mark.asyncio
async def test_sigterm_drains_then_cancels_within_bound(monkeypatch):
    monkeypatch.setenv('FEED_REFRESH_ENABLED','true')
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED','false')
    monkeypatch.setenv('ARTICLE_RETENTION_ENABLED','false')
    monkeypatch.setattr(worker,'SHUTDOWN_SECONDS',0.01)
    stopped=[]
    def install(stop):
        asyncio.get_running_loop().call_later(.001,stop.set)
    async def stuck(stop):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(True)
    with patch.object(worker,'_install_signal_handlers',install), patch.object(worker,'run_forever',stuck):
        assert await asyncio.wait_for(worker.main(),1) == 0
    assert stopped == [True]


@pytest.mark.asyncio
async def test_watchdog_records_sweep_and_stops_without_leaking_task(monkeypatch, caplog):
    stop=asyncio.Event()
    loop = asyncio.get_running_loop()
    class WatchClient(RPCClient):
        def execute(self):
            result = super().execute()
            loop.call_soon_threadsafe(stop.set)
            return result
    db=WatchClient([{'new_alerts':1,'recovered':0,'interrupted':1,'reconciled':0}])
    monkeypatch.setattr(watchdog,'WATCHDOG_SECONDS',.001)
    monkeypatch.setattr(watchdog,'get_client',lambda:db)
    task=asyncio.create_task(watchdog.run_watchdog(stop))
    await asyncio.wait_for(task,1)
    assert db.calls == [('watchdog_worker_operations',{})]
    assert 'new_alerts=1' in caplog.text


@pytest.mark.asyncio
async def test_api_lifespan_owns_watchdog_task():
    started=asyncio.Event()
    stopped=asyncio.Event()
    async def monitor(stop):
        started.set()
        try:
            await stop.wait()
        finally:
            stopped.set()
    with patch.object(main,'run_migrations'),patch.object(main,'run_backfills'),patch.object(main,'run_watchdog',monitor):
        async with main.lifespan(main.app):
            await asyncio.wait_for(started.wait(),1)
        assert stopped.is_set()


@pytest.mark.asyncio
async def test_total_job_timeout_cancels_action_and_retries(monkeypatch):
    from unittest.mock import AsyncMock
    from services.operations import OperationsRecorder
    stop=asyncio.Event()
    outcomes=[]
    cancelled=asyncio.Event()
    class TimedQueue(MemoryQueue):
        async def claim(self,kind,worker_id):
            return Job('j','t',kind,1,{},.001)
        async def finish(self,job,succeeded,error=None):
            outcomes.append((succeeded,error))
            stop.set()
            return True
    async def never_finishes(db):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async def pulse(self,done):
        await done.wait()
    with patch.object(worker,'JobQueue',TimedQueue),patch.object(worker,'get_client',return_value=None), \
         patch.object(OperationsRecorder,'pulse',pulse),patch.object(OperationsRecorder,'begin',AsyncMock(return_value='run')), \
         patch.object(OperationsRecorder,'finish',AsyncMock()):
        await asyncio.wait_for(worker._run_loop(stop,'refresh',0,never_finishes,worker.summarize),1)
    assert outcomes==[(False,'TimeoutError')]
    assert cancelled.is_set()
