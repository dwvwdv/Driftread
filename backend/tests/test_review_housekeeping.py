from types import SimpleNamespace

import pytest

from services.watchdog import prune_review_payloads


class CleanupDB:
    def __init__(self, fail_source=False):
        self.calls = []
        self.fail_source = fail_source

    def rpc(self, name, params):
        self.calls.append((name, params))
        def execute():
            if self.fail_source and name == 'prune_source_reviews':
                raise RuntimeError('private provider response must not be logged')
            return SimpleNamespace(data=1)
        return SimpleNamespace(execute=execute)


@pytest.mark.asyncio
async def test_housekeeping_runs_with_review_and_discovery_disabled(monkeypatch):
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'false')
    db = CleanupDB()
    await prune_review_payloads(db)
    assert db.calls == [('prune_source_reviews', {}), ('prune_ai_receipts', {'p_limit': 100})]


@pytest.mark.asyncio
async def test_housekeeping_failure_does_not_skip_other_payloads_or_log_data(caplog):
    db = CleanupDB(fail_source=True)
    await prune_review_payloads(db)
    assert db.calls[-1] == ('prune_ai_receipts', {'p_limit': 100})
    assert 'RuntimeError' in caplog.text
    assert 'private provider response' not in caplog.text
