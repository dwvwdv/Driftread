"""Simulated Web linking over HTTP with real PostgreSQL OAuth state and SDK MCP."""
from contextlib import asynccontextmanager
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from routers import review_oauth as routes
from services import admin_review_mcp as transport, manage_auth
from services.settings import AppSetting, SourceReviewValue
from tests.test_review_oauth_postgres import setup, query, CALLBACK, VERIFIER, CHALLENGE
from tests.test_postgres_lifecycle import pg_database


@pytest.mark.asyncio
async def test_web_link_consent_token_tools_refresh_and_revoke(setup, monkeypatch):
    # Browser JWT verification uses a fake signed identity, while management
    # session, consent, codes, access, refresh and revocation are all real SQL.
    monkeypatch.setattr(manage_auth, 'get_current_user', lambda _: setup[0])
    monkeypatch.delenv('SOURCE_REVIEW_MCP_KEY', raising=False)
    monkeypatch.setenv('FEED_DISCOVERY_ENABLED', 'true')
    monkeypatch.setattr(transport, 'get_client', lambda: Mock(spec=[]))
    monkeypatch.setattr(transport, 'get_setting', lambda db, key:
        AppSetting(key='source.review', version=1, value=SourceReviewValue(enabled=True).model_dump()))

    @asynccontextmanager
    async def lifespan(app):
        async with transport.reset_review_transport().run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(routes.router)
    app.mount('/api/admin/mcp', transport.AuthenticatedReviewMCP())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://reader.example') as browser:
            challenge = await browser.post('/api/admin/mcp/', json={})
            assert challenge.status_code == 401 and 'resource_metadata=' in challenge.headers['www-authenticate']
            metadata = (await browser.get('/.well-known/oauth-authorization-server')).json()
            registered = await browser.post(metadata['registration_endpoint'], json={
                'client_name': 'ChatGPT HTTP test', 'redirect_uris': [CALLBACK],
                'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token'],
                'response_types': ['code'], 'scope': 'source:review offline_access'})
            assert registered.status_code == 201
            client_id = registered.json()['client_id']
            assert len(client_id) > 100  # the signed ID must survive every HTTP form/query hop
            assert not query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s', (client_id,))
            resource = 'https://reader.example/api/admin/mcp/'
            begin = await browser.get(metadata['authorization_endpoint'], params={
                'response_type': 'code', 'client_id': client_id, 'redirect_uri': CALLBACK,
                'scope': 'source:review offline_access', 'state': 'client-state+exact',
                'audience': 'https://other-resource.example', 'access_type': 'offline',
                'id_token_hint': 'untrusted-client-hint',
                'resource': resource, 'code_challenge': CHALLENGE, 'code_challenge_method': 'S256'})
            assert begin.status_code == 302
            rid = parse_qs(urlsplit(begin.headers['location']).query)['request'][0]
            headers = {'Authorization': 'Bearer signed-browser-user', 'X-Manage-Session': setup[1]}
            consent = await browser.get('/api/review-oauth/consent/' + rid, headers=headers)
            assert consent.status_code == 200 and consent.json()['scope'] == 'source:review offline_access'
            assert not query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s', (client_id,))
            decision = await browser.post('/api/review-oauth/consent/' + rid, headers=headers, json={'approved': True})
            assert query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s', (client_id,))
            callback = parse_qs(urlsplit(decision.json()['redirect_url']).query)
            assert callback['state'] == ['client-state+exact']
            exchanged = await browser.post(metadata['token_endpoint'], data={
                'grant_type': 'authorization_code', 'client_id': client_id, 'redirect_uri': CALLBACK,
                'code': callback['code'][0], 'code_verifier': VERIFIER, 'resource': resource})
            assert exchanged.status_code == 200
            tokens = exchanged.json()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         headers={'Authorization': 'Bearer ' + tokens['access_token']}) as http:
                async with streamable_http_client(resource, http_client=http) as (receive, send, _):
                    async with ClientSession(receive, send) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
                        assert len(tools) == 9 and all(t.meta['securitySchemes'][0]['type'] == 'oauth2' for t in tools)
                        result = await session.call_tool('review_policy')
                        assert result.structuredContent['runtime_available'] is True
            rotated = await browser.post(metadata['token_endpoint'], data={
                'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'],
                'client_id': client_id, 'resource': resource})
            assert rotated.status_code == 200 and rotated.json()['refresh_token'] != tokens['refresh_token']
            grants = await browser.get('/api/review-oauth/grants', headers=headers)
            gid = grants.json()[0]['id']
            assert (await browser.delete('/api/review-oauth/grants/' + gid, headers=headers)).status_code == 204
            denied = await browser.post('/api/admin/mcp/', json={},
                headers={'Authorization': 'Bearer ' + rotated.json()['access_token']})
            assert denied.status_code == 401
