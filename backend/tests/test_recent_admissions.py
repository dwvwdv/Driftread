"""Bounded admin read projection: one RPC, no review work or private packets."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from services.discovery_candidates import recent_admissions

BASE = '/api/admin/discovery/recent-admissions'
KEY = {'x-api-key': 'test-admin-key'}

class RecentDB:
    def __init__(self, items=()):
        self.items = list(items)
        self.calls = []
    def rpc(self, name, args):
        assert name == 'list_recent_feed_admissions'
        self.calls.append((name, args))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data={'items': self.items, 'limit': args['p_limit']}))

def attach(db):
    from database import get_client
    from main import app
    app.dependency_overrides[get_client] = lambda: db

@pytest.mark.parametrize('headers,status', [({}, 422), ({'x-api-key': 'wrong'}, 403)])
def test_auth_before_query(client, headers, status):
    c, _ = client
    db = RecentDB()
    attach(db)
    assert c.get(BASE, headers=headers).status_code == status
    assert not db.calls

@pytest.mark.parametrize('limit', ['0', '51', '-1', 'nope'])
def test_invalid_limit(client, limit):
    c, _ = client
    db = RecentDB()
    attach(db)
    assert c.get(BASE, params={'limit': limit}, headers=KEY).status_code == 422
    assert not db.calls

@pytest.mark.parametrize('limit', [1, 50, None])
def test_projection_single_rpc(client, limit):
    c, _ = client
    row = {'feed_id': str(uuid4()), 'title': '<untrusted>', 'feed_url': 'https://publisher.example/rss',
           'website_url': None, 'admitted_at': '2026-10-16T08:00:00+00:00',
           'method': 'human_disagree', 'engine': 'external_agent',
           'candidate_id': str(uuid4()), 'review_id': str(uuid4()),
           'snapshot': 'private', 'lease_token': 'private'}
    db = RecentDB([row]); attach(db)
    response = c.get(BASE, params={} if limit is None else {'limit': limit}, headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert data['limit'] == (limit or 10)
    assert data['items'][0]['method'] == 'human_disagree'
    assert 'snapshot' not in data['items'][0] and 'lease_token' not in data['items'][0]
    assert len(db.calls) == 1

def test_empty(client):
    c, _ = client
    attach(RecentDB())
    assert c.get(BASE, headers=KEY).json() == {'items': [], 'limit': 10}

@pytest.mark.parametrize('limit', [0, 51])
def test_service_validates(limit):
    db = RecentDB()
    with pytest.raises(ValueError): recent_admissions(db, limit)
    assert not db.calls
