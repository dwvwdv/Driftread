"""HTTP discovery, public-client inputs and browser authorization boundaries."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers import review_oauth as routes
from services import review_oauth as oauth


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('SOURCE_REVIEW_OAUTH_BASE_URL', 'https://reader.example')
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_discovery_is_canonical_and_disabled_fails_closed(client, monkeypatch):
    expected = 'https://reader.example'
    for path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/api/admin/mcp',
                 '/.well-known/oauth-protected-resource/api/admin/mcp/'):
        r = client.get(path, headers={'Host': 'attacker.example'})
        assert r.status_code == 200
        assert r.json()['resource'] == expected + '/api/admin/mcp/'
        assert r.json()['authorization_servers'] == [expected]
        assert r.headers['cache-control'] == 'no-store'
    r = client.get('/.well-known/oauth-authorization-server')
    assert r.json()['issuer'] == expected
    assert r.json()['code_challenge_methods_supported'] == ['S256']
    assert r.json()['token_endpoint_auth_methods_supported'] == ['none']
    monkeypatch.delenv('SOURCE_REVIEW_OAUTH_BASE_URL')
    assert client.get('/.well-known/oauth-authorization-server').status_code == 503


def test_register_requires_bounded_json_and_public_client_metadata(client, monkeypatch):
    register = Mock(return_value={'client_id': 'registered', 'token_endpoint_auth_method': 'none'})
    monkeypatch.setattr(oauth, 'register_client', register)
    r = client.post('/api/review-oauth/register', json={'redirect_uris': ['https://chatgpt.com/connector_platform_oauth_redirect']})
    assert r.status_code == 201 and r.json()['client_id'] == 'registered'
    assert r.headers['cache-control'] == 'no-store'
    register.reset_mock()
    for content, content_type in [('[]', 'application/json'), ('{', 'application/json'),
                                   ('{}', 'text/plain'), ('x' * 8193, 'application/json')]:
        r = client.post('/api/review-oauth/register', content=content, headers={'Content-Type': content_type})
        assert r.status_code == 400 and r.json()['error'] == 'invalid_request'
    register.assert_not_called()


def test_authorize_preserves_params_and_rejects_duplicate_fields(client, monkeypatch):
    begin = Mock(return_value={'authorize_url': 'https://reader.example/manage/mcp-authorize?request=opaque'})
    monkeypatch.setattr(oauth, 'begin_authorization', begin)
    r = client.get('/api/review-oauth/authorize?client_id=test&state=abc%2B123', follow_redirects=False)
    assert r.status_code == 302 and r.headers['location'].endswith('?request=opaque')
    assert r.headers['referrer-policy'] == 'no-referrer'
    begin.assert_called_once_with({'client_id': 'test', 'state': 'abc+123'})
    begin.reset_mock()
    assert client.get('/api/review-oauth/authorize?state=one&state=two').status_code == 400
    begin.assert_not_called()
    for query in ('extension=one&extension=two', 'extension=' + 'x'*8192,
                  '&'.join(f'hint{i}=x' for i in range(21))):
        assert client.get('/api/review-oauth/authorize?' + query).status_code == 400
    begin.assert_not_called()


def test_token_only_accepts_bounded_unambiguous_form_and_safe_errors(client, monkeypatch):
    exchange = Mock(return_value={'access_token': 'one-time-result', 'token_type': 'Bearer'})
    monkeypatch.setattr(oauth, 'exchange_token', exchange)
    r = client.post('/api/review-oauth/token', data={'grant_type': 'authorization_code', 'client_id': 'public'})
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    exchange.assert_called_once_with({'grant_type': 'authorization_code', 'client_id': 'public'})
    exchange.reset_mock()
    for content in ('client_id=one&client_id=two', 'bad-form', 'client_id=' + 'x'*8193):
        r = client.post('/api/review-oauth/token', content=content,
                        headers={'Content-Type': 'application/x-www-form-urlencoded'})
        assert r.status_code == 400
    assert client.post('/api/review-oauth/token', json={'client_id': 'public'}).status_code == 400
    r = client.post('/api/review-oauth/token', data={'client_id': 'public'}, headers={'Authorization': 'Basic secret'})
    assert r.status_code == 401 and 'secret' not in r.text
    exchange.assert_not_called()
    exchange.side_effect = oauth.OAuthError('invalid_grant', description='Authorization is expired or revoked')
    r = client.post('/api/review-oauth/token', data={'grant_type': 'refresh_token'})
    assert r.status_code == 400 and r.json()['error'] == 'invalid_grant'


def test_consent_and_grants_require_telegram_session_not_machine_key(client, monkeypatch):
    required = Mock(side_effect=HTTPException(401, '登入已失效'))
    consent = Mock()
    monkeypatch.setattr(routes.manage_auth, 'require_session', required)
    monkeypatch.setattr(oauth, 'get_consent', consent)
    for path in ('/api/review-oauth/consent/request', '/api/review-oauth/grants'):
        r = client.get(path, headers={'X-API-Key': 'test-admin-key'})
        assert r.status_code == 401 and r.headers['cache-control'] == 'no-store'
    consent.assert_not_called()
    user = SimpleNamespace(id='owner')
    required.side_effect = None
    required.return_value = user
    consent.return_value = {'client_name': 'ChatGPT', 'scope': 'source:review offline_access'}
    headers = {'Authorization': 'Bearer verified-user', 'X-Manage-Session': 'telegram-session'}
    assert client.get('/api/review-oauth/consent/request', headers=headers).status_code == 200
    consent.assert_called_once_with('request', user, 'telegram-session')
    decide = Mock(return_value={'redirect_url': 'https://chatgpt.com/connector_platform_oauth_redirect?code=one-time'})
    monkeypatch.setattr(oauth, 'decide_authorization', decide)
    assert client.post('/api/review-oauth/consent/request', headers=headers, json={'approved': True}).status_code == 200
    decide.assert_called_once_with('request', True, user, 'telegram-session')
    assert client.post('/api/review-oauth/consent/request', headers=headers,
                       json={'approved': True, 'scope': 'admin'}).status_code == 422
    assert client.post('/api/review-oauth/consent/request', headers=headers, json={'approved': 'true'}).status_code == 422


def test_rfc7009_revoke_returns_200_and_browser_revoke_204(client, monkeypatch):
    revoke = Mock(return_value={})
    monkeypatch.setattr(oauth, 'revoke_token', revoke)
    r = client.post('/api/review-oauth/revoke', data={'client_id': 'public', 'token': 'opaque'})
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    revoke.assert_called_once_with({'client_id': 'public', 'token': 'opaque'})
    user = SimpleNamespace(id='owner')
    monkeypatch.setattr(routes.manage_auth, 'require_session', lambda a, t: user)
    revoke_grant = Mock(return_value={'revoked': True})
    monkeypatch.setattr(oauth, 'revoke_grant', revoke_grant)
    gid = '10000000-0000-4000-8000-000000000001'
    r = client.delete('/api/review-oauth/grants/' + gid, headers={'X-Manage-Session': 'admin-session'})
    assert r.status_code == 204 and not r.content
    revoke_grant.assert_called_once_with(gid, user, 'admin-session')
