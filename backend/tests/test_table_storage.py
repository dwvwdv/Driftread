from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_client
from routers.admin_retention import router
from services.table_storage import table_storage_history

T0 = datetime(2026, 10, 20, 0, 5, tzinfo=timezone.utc)


def table(name='articles', ins=100, upd=50, dele=0, hot=10, total=1000, dead=5):
    return {'name': name, 'heap_bytes': total // 2, 'toast_bytes': total // 4, 'index_bytes': total // 4,
            'total_bytes': total, 'n_live_tup': 95, 'n_dead_tup': dead, 'n_tup_ins': ins, 'n_tup_upd': upd,
            'n_tup_del': dele, 'n_tup_hot_upd': hot, 'last_vacuum': None,
            'last_autovacuum': T0.isoformat(), 'vacuum_count': 0, 'autovacuum_count': 3}


def snapshot(at, tables, reset=None):
    return {'observed_at': at.isoformat(), 'stats_reset': reset, 'tables': tables}


class FakeDB:
    """Strict PostgREST stand-in: only the calls the service is allowed to make."""

    def __init__(self, rows, live):
        self.rows, self.live, self.calls = rows, live, []
        self.not_ = self

    def table(self, name):
        assert name == 'article_storage_snapshots'
        return self

    def select(self, fields):
        assert fields == 'snapshot_date,tables'
        return self

    def gte(self, field, value):
        self.calls.append(('gte', field, value))
        return self

    def is_(self, field, value):
        self.calls.append(('not_is', field, value))
        return self

    def order(self, field):
        assert field == 'snapshot_date'
        return self

    def limit(self, value):
        self.calls.append(('limit', value))
        return self

    def rpc(self, name, params):
        assert (name, params) == ('table_storage_stats', {})
        self._rpc = True
        return self

    def execute(self):
        if getattr(self, '_rpc', False):
            self._rpc = False
            return SimpleNamespace(data=self.live)
        return SimpleNamespace(data=self.rows)


def row(at, tables, reset=None):
    return {'snapshot_date': at.date().isoformat(), 'tables': snapshot(at, tables, reset)}


@pytest.mark.asyncio
async def test_adjacent_samples_become_interval_deltas_newest_first():
    rows = [row(T0, [table()]),
            row(T0 + timedelta(days=1), [table(ins=160, upd=250, dele=7, hot=90, total=1500)]),
            row(T0 + timedelta(days=3), [table(ins=200, upd=260, dele=7, hot=95, total=1400),
                                        table('sync_changes', ins=10)])]
    db = FakeDB(rows, snapshot(T0 + timedelta(days=3, hours=2), [table(ins=210)]))
    history = await table_storage_history(db, 7)
    assert history.current.tables[0].n_tup_ins == 210
    newest, older = history.intervals
    assert older.elapsed_hours == 24 and newest.elapsed_hours == 48
    first = older.tables[0]
    assert (first.comparable, first.inserted, first.updated, first.deleted, first.hot_updated) == (True, 60, 200, 7, 80)
    assert first.total_bytes_delta == 500
    # A shrinking file is reported as is, not hidden.
    assert newest.tables[0].total_bytes_delta == -100
    # A table without a previous sample is shown but not differenced.
    assert newest.tables[1].name == 'sync_changes' and newest.tables[1].comparable is False
    assert newest.tables[1].inserted is None and newest.tables[1].total_bytes_delta is None
    today = datetime.now(timezone.utc).date()
    assert ('gte', 'snapshot_date', (today - timedelta(days=7)).isoformat()) in db.calls
    assert ('not_is', 'tables', 'null') in db.calls and ('limit', 8) in db.calls


@pytest.mark.asyncio
async def test_stats_reset_or_shrinking_counter_is_unknown_not_negative():
    reset_at = (T0 + timedelta(hours=12)).isoformat()
    rows = [row(T0, [table(ins=500), table('article_revisions', ins=50)]),
            row(T0 + timedelta(days=1), [table(ins=20), table('article_revisions', ins=10)], reset=reset_at),
            row(T0 + timedelta(days=2), [table(ins=30), table('article_revisions', ins=5)], reset=reset_at)]
    history = await table_storage_history(FakeDB(rows, snapshot(T0, [])), 7)
    newest, older = history.intervals
    assert older.stats_reset_changed is True
    assert all(t.comparable is False and t.inserted is None for t in older.tables)
    # Same database reset time, but one table's counter went backwards.
    assert newest.stats_reset_changed is False
    assert newest.tables[0].inserted == 10
    assert newest.tables[1].comparable is False and newest.tables[1].updated is None


@pytest.mark.asyncio
async def test_single_or_no_sample_has_live_snapshot_only():
    history = await table_storage_history(FakeDB([], snapshot(T0, [table(dead=5)])), 7)
    assert history.intervals == []
    assert history.retention_days == 90
    assert history.current.tables[0].dead_ratio == pytest.approx(5 / 100)
    assert history.model_dump()['current']['tables'][0]['dead_ratio'] == pytest.approx(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize('days', [0, 91, True])
async def test_invalid_window_rejected_before_db(days):
    with pytest.raises(ValueError):
        await table_storage_history(None, days)


def test_tables_endpoint_requires_admin_and_bounds_days():
    app = FastAPI()
    app.include_router(router)
    db = object()
    app.dependency_overrides[get_client] = lambda: db
    payload = {'observed_at': T0.isoformat(), 'retention_days': 90,
               'current': snapshot(T0, []), 'intervals': []}
    with TestClient(app) as client, patch('routers.admin_retention.table_storage_history',
                                          return_value=payload) as history:
        assert client.get('/admin/retention/tables').status_code == 422
        assert client.get('/admin/retention/tables', headers={'X-API-Key': 'wrong'}).status_code == 403
        history.assert_not_called()
        assert client.get('/admin/retention/tables?days=91', headers={'X-API-Key': 'test-admin-key'}).status_code == 422
        result = client.get('/admin/retention/tables', headers={'X-API-Key': 'test-admin-key'})
        assert result.status_code == 200
        history.assert_called_once_with(db, 7)
