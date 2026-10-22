from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_client
from routers.admin_operations import router
from services import ingest_activity as activity


def source(feed_id, **counts):
    base = {'feed_id': feed_id, 'title': feed_id, 'url': f'https://{feed_id}.example/rss', 'archived': False,
            'new_articles': 0, 'backfill_articles': 0, 'revision_updates': 0, 'revised_articles': 0,
            'content_changes': 0, 'metadata_only_changes': 0}
    return {**base, **counts}


class RPC:
    def __init__(self, data):
        self.data, self.calls = data, []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


@pytest.mark.asyncio
async def test_thresholds_flag_sources_without_changing_anything():
    data = {'observed_at': '2026-10-21T00:00:00Z', 'since': '2026-10-20T00:00:00Z',
            'top_ingest': [source('dev', new_articles=645), source('ok', new_articles=499)],
            'top_revisions': [
                source('korea', new_articles=137, revision_updates=1589, revised_articles=160,
                       metadata_only_changes=1586, content_changes=3),
                source('churn', revision_updates=40, revised_articles=10),
                source('noise', revision_updates=29, revised_articles=1),
                source('edits', revision_updates=60, revised_articles=30),
            ]}
    db = RPC(data)
    result = await activity.ingest_activity(db)
    assert db.calls == [('source_ingest_activity', {'p_hours': 24, 'p_limit': 10})]
    alerts = {s.feed_id: s.alerts for s in (*result.top_ingest, *result.top_revisions)}
    assert alerts == {'dev': ['new_articles'], 'ok': [],
                      'korea': ['revision_updates', 'revisions_per_article'],
                      'churn': ['revisions_per_article'], 'noise': [], 'edits': []}
    assert result.top_revisions[0].revisions_per_article == pytest.approx(1589 / 160)
    assert result.flagged_sources == 3
    assert result.thresholds.revision_updates == activity.REVISION_ALERT_UPDATES
    assert result.window_hours == 24


@pytest.mark.asyncio
async def test_empty_window_and_unrevised_sources():
    result = await activity.ingest_activity(RPC({'observed_at': '2026-10-21T00:00:00Z',
        'since': '2026-10-20T00:00:00Z', 'top_ingest': [source('a', new_articles=1)], 'top_revisions': []}), 5)
    assert result.top_ingest[0].revisions_per_article is None
    assert result.flagged_sources == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [0, 51, True])
async def test_invalid_limit_rejected_before_db(limit):
    with pytest.raises(ValueError):
        await activity.ingest_activity(None, limit)


def test_ingest_endpoint_requires_admin_and_bounds_limit():
    app = FastAPI()
    app.include_router(router)
    db = object()
    app.dependency_overrides[get_client] = lambda: db
    payload = {'observed_at': '2026-10-21T00:00:00Z', 'since': '2026-10-20T00:00:00Z',
               'top_ingest': [], 'top_revisions': [], 'flagged_sources': 0}
    with TestClient(app) as client, patch('routers.admin_operations.ingest_activity',
                                          return_value=payload) as call:
        assert client.get('/admin/operations/ingest').status_code == 422
        assert client.get('/admin/operations/ingest', headers={'X-API-Key': 'wrong'}).status_code == 403
        call.assert_not_called()
        assert client.get('/admin/operations/ingest?limit=51', headers={'X-API-Key': 'test-admin-key'}).status_code == 422
        result = client.get('/admin/operations/ingest?limit=3', headers={'X-API-Key': 'test-admin-key'})
        assert result.status_code == 200
        assert result.json()['window_hours'] == 24
        call.assert_called_once_with(db, 3)
