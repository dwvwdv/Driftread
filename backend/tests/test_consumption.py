from datetime import date
from unittest.mock import patch

import pytest
from defusedxml.ElementTree import fromstring
from fastapi import HTTPException

from auth import AuthUser, get_current_user
from services.consumption import daily_digest, day_bounds, rss_remix


def test_local_day_dst_and_invalid_zone():
    _, start, end = day_bounds(date(2026, 3, 8), 'America/New_York')
    assert (end - start).total_seconds() == 23 * 3600
    _, start, end = day_bounds(date(2026, 10, 9), 'Asia/Taipei')
    assert start.isoformat() == '2026-10-08T16:00:00+00:00'
    with pytest.raises(HTTPException):
        day_bounds(None, '../etc/passwd')


def test_digest_bounds_scope_excerpts_and_truncation():
    with patch('services.consumption.publications', return_value=[
        {'id':'a', 'title':'A', 'content':'private raw', 'summary':'source'}, {'id':'b'}
    ]) as read:
        result = daily_digest(object(), 'caller', date(2026, 10, 9), 'Asia/Taipei', 1)
    assert result['truncated'] is True
    assert result['summary_source'] == 'feed'
    assert 'content' not in result['items'][0]
    assert read.call_args.args[1] == 'caller'
    assert read.call_args.kwargs['exclude_backfill'] is True
    assert read.call_args.kwargs['end'].isoformat() == '2026-10-09T16:00:00+00:00'


def test_rss_escapes_and_never_transmits_content():
    with patch('services.consumption.publications', return_value=[
        {'id':'a','title':'A < B\x00','url':'https://example.org/?a=1&b=2',
         'summary':'<source>','content':'FORBIDDEN', 'published_at':'2026-10-08T12:00:00Z'}
    ]):
        xml = rss_remix(object(), 'owner')
    item = fromstring(xml).find('channel/item')
    assert item.findtext('title') == 'A < B'
    assert item.findtext('description') == '<source>'
    assert item.findtext('pubDate') == 'Thu, 08 Oct 2026 12:00:00 GMT'
    assert 'FORBIDDEN' not in xml


def test_rss_uses_corrected_timeline_for_future_source_date():
    with patch('services.consumption.publications', return_value=[
        {'id': 'new', 'title': 'New article', 'url': 'https://example.org/new',
         'published_at': '2030-01-01T00:00:00Z', 'timeline_at': '2026-10-09T14:00:00+02:00'},
        {'id': 'older', 'title': 'Older article', 'url': 'https://example.org/older',
         'published_at': '2026-10-08T12:00:00Z', 'timeline_at': '2026-10-08T12:00:00Z'},
    ]):
        items = fromstring(rss_remix(object(), 'owner')).findall('channel/item')
    assert [item.findtext('pubDate') for item in items] == [
        'Fri, 09 Oct 2026 12:00:00 GMT', 'Thu, 08 Oct 2026 12:00:00 GMT',
    ]
    assert [item.findtext('guid') for item in items] == [
        'urn:driftread:article:new', 'urn:driftread:article:older',
    ]


def test_rss_legacy_date_fallback_and_undated_item():
    with patch('services.consumption.publications', return_value=[
        {'id': 'legacy', 'title': 'Legacy', 'url': 'https://example.org/legacy',
         'timeline_at': None, 'published_at': '2026-10-08T12:00:00Z'},
        {'id': 'undated', 'title': 'Undated', 'url': 'https://example.org/undated'},
    ]):
        items = fromstring(rss_remix(object(), 'owner')).findall('channel/item')
    assert items[0].findtext('pubDate') == 'Thu, 08 Oct 2026 12:00:00 GMT'
    assert items[1].find('pubDate') is None


@pytest.mark.parametrize('path', ['/api/me/digest', '/api/me/rss'])
def test_private_surfaces_require_auth(client, path):
    c, db = client
    response = c.get(path)
    assert response.status_code == 401
    db.table.assert_not_called()
    db.rpc.assert_not_called()


def test_rss_auth_scope_and_cache_policy(client):
    from main import app
    c, db = client
    app.dependency_overrides[get_current_user] = lambda: AuthUser('caller')
    try:
        with patch('services.consumption.publications', return_value=[]) as read:
            result = c.get('/api/me/rss')
        assert result.status_code == 200
        assert result.headers['cache-control'] == 'private, no-store'
        assert read.call_args.args[1] == 'caller'
        assert c.get('/api/me/digest?timezone=unknown').status_code == 422
        assert c.get('/api/me/rss?limit=101').status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
