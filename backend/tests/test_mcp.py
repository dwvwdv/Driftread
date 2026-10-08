from unittest.mock import patch

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
        assert {t['name'] for t in tools} == {'reading_stream','article','digest'}
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
    with patch('services.mcp_server.get_current_user', return_value=AuthUser('sdk-owner')), \
         patch('services.mcp_server.get_client', return_value=object()), \
         patch('services.mcp_server.publications', return_value=[]) as read:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
                async with streamable_http_client('http://testserver/api/mcp/', http_client=http) as (receive, send, _):
                    async with ClientSession(receive, send) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert len(tools.tools) == 3
                        result = await session.call_tool('reading_stream', {'limit':1})
                        assert result.isError is False
                        assert read.call_args.args[1] == 'sdk-owner'
