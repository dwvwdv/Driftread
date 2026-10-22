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
async def test_thresholds_are_passed_to_sql_and_alerts_shown_as_returned():
    # The predicate lives only in SQL (tests/sql/test_storage_observability.sql
    # covers its boundaries); Python passes the thresholds and adds the average.
    data = {'observed_at': '2026-10-21T00:00:00Z', 'since': '2026-10-20T00:00:00Z',
            'top_ingest': [source('dev', new_articles=645, alerts=['new_articles']), source('ok', new_articles=499)],
            'top_revisions': [
                source('korea', new_articles=137, revision_updates=1589, revised_articles=160,
                       metadata_only_changes=1586, content_changes=3,
                       alerts=['revision_updates', 'revisions_per_article']),
                source('noise', revision_updates=29, revised_articles=1),
            ],
            # SQL counts every source over a threshold, including one that is
            # in neither (truncated) top list.
            'flagged': [source('dev', new_articles=645, alerts=['new_articles']),
                        source('hidden', revision_updates=31, revised_articles=5, alerts=['revisions_per_article'])],
            'flagged_total': 4}
    db = RPC(data)
    result = await activity.ingest_activity(db)
    assert db.calls == [('source_ingest_activity', {
        'p_hours': 24, 'p_limit': 10,
        'p_alert_new_articles': activity.INGEST_ALERT_NEW_ARTICLES,
        'p_alert_revision_updates': activity.REVISION_ALERT_UPDATES,
        'p_alert_per_article': activity.REVISION_ALERT_PER_ARTICLE,
        'p_alert_min_updates': activity.REVISION_ALERT_PER_ARTICLE_MIN_UPDATES})]
    alerts = {s.feed_id: s.alerts for s in (*result.top_ingest, *result.top_revisions)}
    assert alerts == {'dev': ['new_articles'], 'ok': [],
                      'korea': ['revision_updates', 'revisions_per_article'], 'noise': []}
    assert result.top_revisions[0].revisions_per_article == pytest.approx(1589 / 160)
    assert result.flagged_sources == 4
    assert {s.feed_id: s.alerts for s in result.flagged} == {
        'dev': ['new_articles'], 'hidden': ['revisions_per_article']}
    assert result.thresholds.revision_updates == activity.REVISION_ALERT_UPDATES
    assert result.window_hours == 24


def test_unknown_alert_reason_is_rejected():
    with pytest.raises(ValueError):
        activity.classify(source('x', alerts=['made_up']))


@pytest.mark.asyncio
async def test_empty_window_and_unrevised_sources():
    result = await activity.ingest_activity(RPC({'observed_at': '2026-10-21T00:00:00Z',
        'since': '2026-10-20T00:00:00Z', 'top_ingest': [source('a', new_articles=1)], 'top_revisions': []}), 5)
    assert result.top_ingest[0].revisions_per_article is None
    assert result.flagged_sources == 0
    assert result.flagged == []


@pytest.mark.asyncio
async def test_seven_day_window_scales_only_the_new_article_threshold():
    # Revision counts are capped at 10 kept versions per article; scaling the
    # update thresholds would hide in 7d a source the 24h view flags.
    db = RPC({'observed_at': '2026-10-22T00:00:00Z', 'since': '2026-10-15T00:00:00Z',
              'top_ingest': [], 'top_revisions': []})
    result = await activity.ingest_activity(db, 5, 168)
    assert db.calls == [('source_ingest_activity', {
        'p_hours': 168, 'p_limit': 5,
        'p_alert_new_articles': activity.INGEST_ALERT_NEW_ARTICLES * 7,
        'p_alert_revision_updates': activity.REVISION_ALERT_UPDATES,
        'p_alert_per_article': activity.REVISION_ALERT_PER_ARTICLE,
        'p_alert_min_updates': activity.REVISION_ALERT_PER_ARTICLE_MIN_UPDATES})]
    assert result.window_hours == 168
    assert result.thresholds.new_articles == activity.INGEST_ALERT_NEW_ARTICLES * 7
    assert result.thresholds.revisions_per_article == activity.REVISION_ALERT_PER_ARTICLE
    assert result.thresholds.revision_updates == activity.REVISION_ALERT_UPDATES


@pytest.mark.asyncio
@pytest.mark.parametrize('hours', [0, 48, 169, '24', True])
async def test_invalid_window_rejected_before_db(hours):
    with pytest.raises(ValueError):
        await activity.ingest_activity(None, 10, hours)


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
               'window_hours': 24, 'thresholds': activity.thresholds_for(24).model_dump(),
               'top_ingest': [], 'top_revisions': [], 'flagged': [], 'flagged_sources': 0}
    with TestClient(app) as client, patch('routers.admin_operations.ingest_activity',
                                          return_value=payload) as call:
        assert client.get('/admin/operations/ingest').status_code == 422
        assert client.get('/admin/operations/ingest', headers={'X-API-Key': 'wrong'}).status_code == 403
        call.assert_not_called()
        assert client.get('/admin/operations/ingest?limit=51', headers={'X-API-Key': 'test-admin-key'}).status_code == 422
        result = client.get('/admin/operations/ingest?limit=3', headers={'X-API-Key': 'test-admin-key'})
        assert result.status_code == 200
        assert result.json()['window_hours'] == 24
        call.assert_called_once_with(db, 3, 24)
        call.reset_mock()
        for bad in ('48', '0', '169', 'week'):
            assert client.get(f'/admin/operations/ingest?hours={bad}',
                              headers={'X-API-Key': 'test-admin-key'}).status_code == 422
        call.assert_not_called()
        assert client.get('/admin/operations/ingest?hours=168',
                          headers={'X-API-Key': 'test-admin-key'}).status_code == 200
        call.assert_called_once_with(db, 10, 168)
