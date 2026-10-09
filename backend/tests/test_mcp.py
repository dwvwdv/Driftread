from unittest.mock import Mock, patch

import pytest

from auth import AuthUser

HEADERS = {'Accept':'application/json, text/event-stream'}


def test_official_mcp_protocol_and_user_scope(client):
    c, db = client
    with patch('services.mcp_server.get_current_user', return_value=AuthUser('owner')), \
         patch('services.mcp_server.get_client', return_value=db), \
         patch('services.mcp_server.publications', return_value=[{'id':'a','title':'Only yours','content':'hidden'}]) as read:
        response = c.post('/api/mcp/', headers=HEADERS, json={
            'jsonrpc':'2.0','id':1,'method':'initialize','params':{
                'protocolVersion':'2025-11-25','capabilities':{},
                'clientInfo':{'name':'test','version':'1'}}})
        assert response.status_code == 200, response.text
        assert response.json()['result']['serverInfo']['name'] == 'Driftread'
        notification = c.post('/api/mcp/', headers=HEADERS, json={
            'jsonrpc':'2.0','method':'notifications/initialized'})
        assert notification.status_code == 202
        tools = c.post('/api/mcp/', headers=HEADERS, json={
            'jsonrpc':'2.0','id':2,'method':'tools/list'}).json()['result']['tools']
        assert {t['name'] for t in tools} == {'reading_stream','article','digest','subscriptions','search'}
        assert all(t['annotations']['readOnlyHint'] for t in tools)
        result = c.post('/api/mcp/', headers=HEADERS, json={
            'jsonrpc':'2.0','id':3,'method':'tools/call',
            'params':{'name':'reading_stream','arguments':{'limit':2}}}).json()
        assert result['result'].get('isError') is not True, result
        assert 'hidden' not in str(result)
        assert read.call_args.args[1] == 'owner'
        invalid = c.post('/api/mcp/', headers=HEADERS, json={
            'jsonrpc':'2.0','id':4,'method':'tools/call',
            'params':{'name':'reading_stream','arguments':{'limit':1000}}}).json()
        assert invalid['result']['isError'] is True


def test_mcp_auth_and_origin_fail_before_database(client):
    c, db = client
    assert c.post('/api/mcp/', headers=HEADERS, json={}).status_code == 401
    assert c.post('/api/mcp/', headers={**HEADERS,'Origin':'https://attacker.invalid'}, json={}).status_code == 403
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_official_sdk_client_interoperability():
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from main import app
    db = Mock(spec=['table','rpc'])
    chain = Mock(spec=['select','eq','is_','order','limit','execute'])
    for method in ['select','eq','is_','order','limit']:
        getattr(chain,method).return_value = chain
    chain.execute.return_value = Mock(data=[{'custom_title':'Yours','feeds':{
        'id':'feed','title':'Original','url':'https://example.test/rss','participation_mode':'normal'}}])
    db.table.return_value = chain
    rpc = Mock(spec=['execute']); rpc.execute.return_value = Mock(data=[{'id':'article','title':'Yours','content':'HIDDEN'}])
    db.rpc.return_value = rpc
    with patch('services.mcp_server.get_current_user', return_value=AuthUser('sdk-owner')), \
         patch('services.mcp_server.get_client', return_value=db), \
         patch('services.mcp_server.publications', return_value=[]) as read:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                async with streamable_http_client('http://testserver/api/mcp/', http_client=http) as (receive, send, _):
                    async with ClientSession(receive, send) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert len(tools.tools) == 5
                        result = await session.call_tool('reading_stream', {'limit':1})
                        assert result.isError is False
                        assert read.call_args.args[1] == 'sdk-owner'

                        subscriptions = await session.call_tool('subscriptions', {'limit':2})
                        assert subscriptions.isError is False
                        chain.eq.assert_any_call('user_id','sdk-owner')
                        chain.limit.assert_called_with(2)
                        search = await session.call_tool('search', {'query':'example','limit':2,'user_id':'attacker'})
                        assert search.isError is False
                        db.rpc.assert_called_once_with('personal_publication_search', {
                            'p_user_id':'sdk-owner','p_query':'example','p_limit':2})
                        assert 'HIDDEN' not in str(search)
                        for arguments in ({'query':'x'*201},{'query':'x','limit':101}):
                            invalid = await session.call_tool('search', arguments)
                            assert invalid.isError is True
                        assert db.rpc.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('valid_signing_key', [True, False])
async def test_slow_jwks_keeps_event_loop_available_and_request_identity_isolated(monkeypatch, valid_signing_key):
    import asyncio
    import threading

    import httpx
    from jwt import PyJWKClient
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    import auth
    from services import mcp_server
    from tests.test_auth import _asymmetric_token_and_jwks, _token

    token, jwks = _asymmetric_token_and_jwks(user_id='slow-owner')
    if not valid_signing_key:
        _, jwks = _asymmetric_token_and_jwks(user_id='another-key-owner')
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    fetch_started = asyncio.Event()
    release_fetch = threading.Event()
    fetch_threads = []
    identities = []

    def slow_fetch(self):
        fetch_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(fetch_started.set)
        if not release_fetch.wait(timeout=3):
            raise AssertionError('JWKS fetch blocked the event loop before another request could finish')
        return jwks

    async def transport(scope, receive, send):
        identity = Request(scope).state.driftread_user.id
        identities.append(identity)
        await JSONResponse({'owner': identity})(scope, receive, send)

    database = Mock(spec=[])
    get_database = Mock(return_value=database)
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_JWT_SECRET', 'mcp-threadpool-test-secret-with-32-plus-bytes')
    monkeypatch.setattr(PyJWKClient, 'fetch_data', slow_fetch)
    monkeypatch.setattr(mcp_server, 'mcp_http', transport)
    monkeypatch.setattr(mcp_server, 'get_client', get_database)
    auth.reset_jwks_client()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_server.AuthenticatedMCP()),
                                     base_url='http://testserver') as client:
            slow_request = asyncio.create_task(client.post('/', headers={'Authorization':f'Bearer {token}'}))
            try:
                await asyncio.wait_for(fetch_started.wait(), timeout=1)
                # This request verifies its own JWT and completes while A waits on JWKS.
                fast = await asyncio.wait_for(client.post('/', headers={
                    'Authorization':f'Bearer {_token(user_id="fast-owner")}'
                }), timeout=1)
                assert fast.status_code == 200 and fast.json() == {'owner':'fast-owner'}
                assert not slow_request.done()
                assert get_database.call_count == 1
                denied = await asyncio.wait_for(client.post('/', headers={
                    'Authorization':'Bearer invalid-token'
                }), timeout=1)
                assert denied.status_code == 401
                assert denied.headers['www-authenticate'] == 'Bearer'
                assert get_database.call_count == 1  # Failed auth cannot reach the SDK/DB.
            finally:
                release_fetch.set()
                result = await asyncio.wait_for(slow_request, timeout=2)
            assert len(fetch_threads) == 1
            assert fetch_threads[0] != loop_thread
            if valid_signing_key:
                assert result.status_code == 200 and result.json() == {'owner':'slow-owner'}
                assert identities == ['fast-owner','slow-owner']
            else:
                assert result.status_code == 401
                assert identities == ['fast-owner']
                assert get_database.call_count == 1
    finally:
        release_fetch.set()
        auth.reset_jwks_client()
