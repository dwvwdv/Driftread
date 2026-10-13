from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.storage_history import record_storage_snapshot, storage_history


class HistoryDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def table(self, name):
        assert name == 'article_storage_snapshots'
        return self

    def select(self, fields):
        assert fields == 'snapshot_date,observed_at,stats'
        return self

    def gte(self, field, value):
        self.calls.append((field, value))
        return self

    def order(self, field):
        assert field == 'snapshot_date'
        return self

    def limit(self, value):
        assert 1 <= value <= 90
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def row(at, count=100, total=1024):
    return {'snapshot_date': at.date().isoformat(), 'observed_at': at.isoformat(), 'stats': {
        'article_count': count, 'full_content_count': 60, 'summary_only_count': 40,
        'pending_discovery_count': 0, 'compacted_count': 0, 'table_bytes': total,
        'index_bytes': total, 'total_bytes': total * 2, 'database_bytes': total * 4,
    }}


@pytest.mark.asyncio
async def test_missing_days_use_actual_elapsed_time_and_keep_negative_growth():
    now = datetime.now(timezone.utc)
    db = HistoryDB([row(now - timedelta(days=4), 100, 1000), row(now, 120, 800)])
    history = await storage_history(db)
    assert history.growth.elapsed_days == 4
    assert history.growth.article_count_per_day == 5
    assert history.growth.total_bytes_per_day == -100
    assert history.growth.database_bytes_per_day == -200
    assert db.calls == [('snapshot_date', (now.date() - timedelta(days=29)).isoformat())]


@pytest.mark.asyncio
@pytest.mark.parametrize('hours', [0, 0.25, 23.5])
async def test_short_or_single_sample_does_not_invent_growth(hours):
    now = datetime.now(timezone.utc)
    rows = [row(now)] if hours == 0 else [row(now - timedelta(hours=hours)), row(now)]
    assert (await storage_history(HistoryDB(rows))).growth is None


@pytest.mark.asyncio
async def test_empty_history_is_known_empty():
    history = await storage_history(HistoryDB([]))
    assert history.points == []
    assert history.growth is None
    assert history.retention_days == 90


@pytest.mark.asyncio
@pytest.mark.parametrize('days', [0, 91, True])
async def test_invalid_window_rejected_before_db(days):
    with pytest.raises(ValueError):
        await storage_history(None, days)


@pytest.mark.asyncio
async def test_recorder_does_not_hide_failed_snapshot_rpc():
    class DB:
        def rpc(self, name, params):
            assert (name, params) == ('record_article_storage_snapshot', {})
            return self

        def execute(self):
            raise RuntimeError('unavailable')

    with pytest.raises(RuntimeError, match='unavailable'):
        await record_storage_snapshot(DB())
