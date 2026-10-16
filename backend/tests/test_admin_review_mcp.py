"""Official SDK interoperability and machine/management permission boundaries."""
import asyncio
from contextlib import asynccontextmanager
from threading import BoundedSemaphore, Event
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from services import admin_review_mcp as transport
from services.settings import AppSetting, SourceReviewValue

KEY = "test-source-review-dedicated-key"
REVIEW_ID = "10000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "10000000-0000-4000-8000-000000000002"
LEASE = "10000000-0000-4000-8000-000000000003"
SUBMISSION = {
    "lease_token": LEASE, "snapshot_hash": "a" * 64, "decision": "accept",
    "confidence": .95, "dimensions": {"quality": .9, "spam_probability": .1, "relevance": .9},
    "reason_codes": ["original_reporting"],
}


@pytest.fixture
def mcp_app(monkeypatch):
    monkeypatch.setenv("SOURCE_REVIEW_MCP_KEY", KEY)
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("FEED_DISCOVERY_ENABLED", "true")

    @asynccontextmanager
    async def lifespan(app):
        async with transport.reset_review_transport().run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/api/admin/mcp", transport.AuthenticatedReviewMCP())
    return app


@pytest.mark.asyncio
async def test_official_sdk_source_review_workflow_and_annotations(mcp_app):
    db = Mock(spec=[])
    packet = {"id": REVIEW_ID, "candidate_id": CANDIDATE_ID, "snapshot_hash": "a" * 64,
              "lease_token": LEASE, "samples": [{"id": "sample-1", "text": "untrusted article"}]}
    policy = AppSetting(key="source.review", version=1, value=SourceReviewValue(enabled=True).model_dump())
    with patch.object(transport, "get_client", return_value=db), \
            patch.object(transport, "get_setting", return_value=policy), \
            patch.object(transport.source_review, "request_review", return_value={"id": REVIEW_ID, "status": "queued"}) as enqueue, \
            patch.object(transport.source_review, "claim_review", return_value=packet) as claim, \
            patch.object(transport.source_review, "get_review_packet", return_value=packet) as read, \
            patch.object(transport.source_review, "submit_review", return_value={"id": REVIEW_ID, "status": "awaiting_human"}) as submit:
        async with mcp_app.router.lifespan_context(mcp_app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), headers={"X-API-Key": KEY}) as http:
                async with streamable_http_client("http://testserver/api/admin/mcp/", http_client=http) as (receive, send, _):
                    async with ClientSession(receive, send) as session:
                        init = await session.initialize()
                        assert init.serverInfo.name == "Driftread Source Review"
                        tools = {t.name: t for t in (await session.list_tools()).tools}
                        assert set(tools) == {"review_policy", "request_source_review", "claim_source_review", "source_review_packet", "submit_source_review",
                                              "list_source_review_candidates", "request_source_reviews", "claim_source_reviews", "submit_source_reviews"}
                        assert {name for name, tool in tools.items() if tool.annotations.readOnlyHint} == {"review_policy", "source_review_packet", "list_source_review_candidates"}
                        assert all(not tool.annotations.openWorldHint for tool in tools.values())
                        assert tools["claim_source_review"].annotations.idempotentHint is False
                        assert all(t.inputSchema["additionalProperties"] is False for t in tools.values())
                        policy_result = await session.call_tool("review_policy")
                        assert policy_result.isError is False, policy_result
                        assert policy_result.structuredContent["runtime_available"] is True
                        queued = await session.call_tool("request_source_review", {"candidate_id": CANDIDATE_ID})
                        assert queued.isError is False
                        enqueue.assert_called_once_with(db, CANDIDATE_ID)
                        claimed = await session.call_tool("claim_source_review")
                        assert claimed.structuredContent["review"]["lease_token"] == LEASE
                        claim.assert_called_once_with(db, reviewer_id="external_agent")
                        result = await session.call_tool("source_review_packet", {"review_id": REVIEW_ID})
                        assert result.isError is False
                        read.assert_called_once_with(db, REVIEW_ID, reviewer_id="external_agent")
                        result = await session.call_tool("submit_source_review", {"review_id": REVIEW_ID, "submission": SUBMISSION})
                        assert result.structuredContent["status"] == "awaiting_human"
                        assert submit.call_args.kwargs == {"reviewer_id": "external_agent"}
                        assert str(submit.call_args.args[2].lease_token) == LEASE

                        for name, args in (
                            ("claim_source_review", {"reviewer_id": "jev"}),
                            ("request_source_review", {"candidate_id": CANDIDATE_ID, "url": "http://127.0.0.1"}),
                            ("submit_source_review", {"review_id": REVIEW_ID, "submission": {**SUBMISSION, "engine": "jev"}}),
                            ("submit_source_review", {"review_id": REVIEW_ID, "submission": {**SUBMISSION, "rationale": "x" * 2001}}),
                            ("source_review_packet", {"review_id": "invalid"}),
                            ("resolve_source_review", {"review_id": REVIEW_ID, "decision": "accept"}),
                        ):
                            assert (await session.call_tool(name, args)).isError is True
                        assert submit.call_count == claim.call_count == enqueue.call_count == read.call_count == 1


@pytest.mark.asyncio
async def test_authentication_scope_origin_and_disabled_configuration(mcp_app, monkeypatch):
    with patch.object(transport, "get_client") as get_database:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://testserver") as http:
            for headers, status in (
                ({}, 401), ({"X-API-Key": "test-admin-key"}, 403),
                ({"Authorization": "Bearer personal-jwt"}, 401),
                ({"X-API-Key": KEY, "Authorization": "Bearer management-jwt", "X-Manage-Session": "session"}, 401),
                ({"X-API-Key": KEY, "Origin": "https://attacker.test"}, 403),
                ({"X-API-Key": "é"}, 403),
            ):
                # HTTP headers are latin-1 bytes; UTF-8 values exercise safe comparison.
                encoded = {k: v.encode("utf-8") for k, v in headers.items()}
                result = await http.post("/api/admin/mcp/", headers=encoded, json={})
                assert result.status_code == status
            get_database.assert_not_called()
            monkeypatch.delenv("SOURCE_REVIEW_MCP_KEY")
            assert (await http.post("/api/admin/mcp/", headers={"X-API-Key": KEY}, json={})).status_code == 503
            monkeypatch.setenv("SOURCE_REVIEW_MCP_KEY", "test-admin-key")
            assert (await http.post("/api/admin/mcp/", headers={"X-API-Key": "test-admin-key"}, json={})).status_code == 503
            get_database.assert_not_called()


@pytest.mark.asyncio
async def test_expired_lease_and_provider_packet_are_tool_errors(mcp_app):
    with patch.object(transport, "get_client", return_value=Mock(spec=[])), \
            patch.object(transport.source_review, "get_review_packet", side_effect=transport.source_review.ReviewError(403, "Review belongs to another engine")), \
            patch.object(transport.source_review, "submit_review", side_effect=transport.source_review.ReviewError(409, "Review lease expired")):
        async with mcp_app.router.lifespan_context(mcp_app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), headers={"X-API-Key": KEY}) as http:
                async with streamable_http_client("http://testserver/api/admin/mcp/", http_client=http) as (receive, send, _):
                    async with ClientSession(receive, send) as session:
                        await session.initialize()
                        result = await session.call_tool("submit_source_review", {"review_id": REVIEW_ID, "submission": SUBMISSION})
                        assert result.isError is True and "lease expired" in str(result)
                        assert result.structuredContent == {'error_code': 'REVIEW_CONFLICT', 'status_code': 409,
                                                            'detail': 'Review lease expired'}
                        result = await session.call_tool("source_review_packet", {"review_id": REVIEW_ID})
                        assert result.isError is True and "another engine" in str(result)
                        assert result.structuredContent['error_code'] == 'FORBIDDEN'


@pytest.mark.asyncio
async def test_unexpected_service_error_does_not_expose_credentials():
    operation = Mock(side_effect=RuntimeError("https://provider.test?key=SECRET_PROVIDER_KEY"))
    with pytest.raises(ValueError, match="^Source review operation failed$") as error:
        await transport._call(operation)
    assert "SECRET_PROVIDER_KEY" not in str(error.value)


@pytest.mark.asyncio
async def test_sdk_batches_listing_bounds_and_machine_authority(mcp_app):
    db = Mock(spec=[])
    summary = {'items': [{'id': CANDIDATE_ID, 'latest_review': {'id': REVIEW_ID, 'status': 'ready'}}]}
    partial = {'items': [{'review_id': REVIEW_ID, 'ok': False, 'status_code': 409,
                          'detail': 'Review lease expired'}], 'succeeded': 0, 'failed': 1}
    leases = {'reviews': [{'id': REVIEW_ID, 'lease_token': LEASE}], 'claimed': 1, 'error': None}
    with patch.object(transport, 'get_client', return_value=db), \
            patch.object(transport, 'list_review_candidates', return_value=summary) as listing, \
            patch.object(transport.source_review, 'request_reviews', return_value={'items': [], 'succeeded': 1, 'failed': 0}) as enqueue, \
            patch.object(transport.source_review, 'claim_reviews', return_value=leases) as claims, \
            patch.object(transport.source_review, 'claim_review') as single_claim, \
            patch.object(transport.source_review, 'submit_reviews', return_value=partial) as submit:
        async with mcp_app.router.lifespan_context(mcp_app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), headers={'X-API-Key': KEY}) as http:
                async with streamable_http_client('http://testserver/api/admin/mcp/', http_client=http) as (receive, send, _):
                    async with ClientSession(receive, send) as session:
                        await session.initialize()
                        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                        assert tools['list_source_review_candidates'].annotations.readOnlyHint is True
                        assert tools['claim_source_reviews'].annotations.idempotentHint is False
                        assert tools['request_source_reviews'].annotations.idempotentHint is True
                        assert tools['submit_source_reviews'].annotations.idempotentHint is True
                        listed = await session.call_tool('list_source_review_candidates')
                        assert listed.isError is False and listed.structuredContent == summary
                        listing.assert_called_once_with(db, status='ready', limit=20, offset=0)
                        single_claim.assert_not_called()
                        claims.assert_not_called()

                        # The server accepts both batch bounds and leaves each
                        # submission tied to its own validated packet identity.
                        for size in (1, 50):
                            result = await session.call_tool('request_source_reviews', {'candidate_ids': [CANDIDATE_ID] * size})
                            assert result.isError is False
                            assert enqueue.call_args.args[0] is db
                            assert [str(cid) for cid in enqueue.call_args.args[1]] == [CANDIDATE_ID] * size
                            assert enqueue.call_args.kwargs == {}
                            result = await session.call_tool('submit_source_reviews', {
                                'submissions': [{'review_id': REVIEW_ID, 'submission': SUBMISSION}] * size})
                            assert result.isError is False and result.structuredContent == partial
                            assert submit.call_args.kwargs == {'reviewer_id': 'external_agent'}
                            assert len(submit.call_args.args[1]) == size
                            assert str(submit.call_args.args[1][0].submission.lease_token) == LEASE
                        for limit in (1, 5):
                            result = await session.call_tool('claim_source_reviews', {'limit': limit})
                            assert result.isError is False and result.structuredContent == leases
                            claims.assert_called_with(db, limit, reviewer_id='external_agent')

                        for name, args in (
                            ('request_source_reviews', {'candidate_ids': []}),
                            ('request_source_reviews', {'candidate_ids': [CANDIDATE_ID] * 51}),
                            ('request_source_reviews', {'candidate_ids': [CANDIDATE_ID], 'retry': True}),
                            ('request_source_reviews', {'candidate_ids': [CANDIDATE_ID], 'manual_override': None}),
                            ('claim_source_reviews', {'limit': 0}), ('claim_source_reviews', {'limit': 6}),
                            ('claim_source_reviews', {'reviewer_id': 'jev'}),
                            ('submit_source_reviews', {'submissions': []}),
                            ('submit_source_reviews', {'submissions': [{'review_id': REVIEW_ID, 'submission': SUBMISSION}] * 51}),
                            ('submit_source_reviews', {'submissions': [{'review_id': REVIEW_ID, 'submission': {**SUBMISSION, 'engine': 'jev'}}]}),
                            ('submit_source_reviews', {'submissions': [{'review_id': REVIEW_ID, 'submission': SUBMISSION, 'decision': 'accept'}]}),
                            ('list_source_review_candidates', {'status': 'held'}),
                            ('list_source_review_candidates', {'limit': 51}),
                            ('list_source_review_candidates', {'offset': -1}),
                        ):
                            assert (await session.call_tool(name, args)).isError is True, (name, args)
                        assert enqueue.call_count == submit.call_count == claims.call_count == 2
                        assert listing.call_count == 1


@pytest.mark.asyncio
async def test_sdk_unavailable_error_has_stable_code_without_secret(mcp_app, caplog):
    with patch.object(transport, 'get_client', return_value=Mock(spec=[])), \
            patch.object(transport.source_review, 'claim_reviews', side_effect=RuntimeError('key=SECRET_PROVIDER_KEY')):
        async with mcp_app.router.lifespan_context(mcp_app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), headers={'X-API-Key': KEY}) as http:
                async with streamable_http_client('http://testserver/api/admin/mcp/', http_client=http) as (receive, send, _):
                    async with ClientSession(receive, send) as session:
                        await session.initialize()
                        result = await session.call_tool('claim_source_reviews')
    assert result.isError is True
    assert result.structuredContent == {'error_code': 'UNAVAILABLE', 'status_code': 503,
                                        'detail': 'Source review operation failed'}
    assert 'SECRET_PROVIDER_KEY' not in str(result) + caplog.text


@pytest.mark.asyncio
async def test_oauth_official_sdk_works_without_machine_key_and_advertises_scopes(mcp_app, monkeypatch):
    monkeypatch.setenv('SOURCE_REVIEW_OAUTH_BASE_URL', 'https://reader.example')
    monkeypatch.delenv('SOURCE_REVIEW_MCP_KEY')
    token = 'droa_' + 'a' * 43
    grant = {'owner_user_id': 'owner', 'scope': 'source:review', 'grant_id': 'grant'}
    with patch.object(transport.review_oauth, 'validate_access_token', return_value=grant) as validate, \
            patch.object(transport, 'get_client', return_value=Mock(spec=[])), \
            patch.object(transport.source_review, 'claim_review', return_value=None) as claim:
        async with mcp_app.router.lifespan_context(mcp_app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app),
                                         headers={'Authorization': 'Bearer ' + token}) as http:
                async with streamable_http_client('http://testserver/api/admin/mcp/', http_client=http) as (receive, send, _):
                    async with ClientSession(receive, send) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
                        for tool in tools:
                            assert tool.meta['securitySchemes'] == [{'type': 'oauth2', 'scopes': ['source:review', 'offline_access']}]
                            assert tool.model_dump()['securitySchemes'] == tool.meta['securitySchemes']
                        result = await session.call_tool('claim_source_review')
                        assert result.structuredContent == {'review': None}
                        claim.assert_called_once()
                        assert claim.call_args.kwargs == {'reviewer_id': 'external_agent'}
        assert validate.call_count >= 3
        assert all(call.args == (token,) for call in validate.call_args_list)


@pytest.mark.asyncio
async def test_oauth_challenge_revocation_and_mixed_credentials_fail_before_business(mcp_app, monkeypatch):
    monkeypatch.setenv('SOURCE_REVIEW_OAUTH_BASE_URL', 'https://reader.example')
    token = 'droa_' + 'a' * 43
    with patch.object(transport, 'get_client') as db, \
            patch.object(transport.review_oauth, 'validate_access_token',
                         side_effect=transport.review_oauth.OAuthError('invalid_token', 401)) as validate:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url='http://testserver') as http:
            for headers in ({}, {'Authorization': 'Bearer ' + token},
                            {'Authorization': 'Bearer ' + token, 'X-API-Key': KEY},
                            {'Authorization': 'Bearer ' + token, 'X-Manage-Session': 'admin-session'}):
                result = await http.post('/api/admin/mcp/', headers=headers, json={})
                assert result.status_code == 401
                assert 'https://reader.example/.well-known/oauth-protected-resource/api/admin/mcp' in result.headers['www-authenticate']
                assert 'source:review offline_access' in result.headers['www-authenticate']
            db.assert_not_called()
            validate.assert_called_once_with(token)


@pytest.mark.asyncio
async def test_oauth_ip_limit_rejects_before_verification_and_does_not_trust_forwarded_header(mcp_app):
    token = 'droa_' + 'a' * 43
    with patch.object(transport.review_oauth, 'validate_access_token',
                      side_effect=transport.review_oauth.OAuthError('invalid_token', 401)) as validate, \
            patch.object(transport, 'get_client') as db:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app, client=('192.0.2.1', 123)),
                                     base_url='http://testserver') as http:
            for i in range(60):
                response = await http.post('/api/admin/mcp/', json={}, headers={
                    'Authorization': 'Bearer ' + token, 'X-Forwarded-For': f'198.51.100.{i}'})
                assert response.status_code == 401
            blocked = await http.post('/api/admin/mcp/', json={}, headers={'Authorization': 'Bearer ' + token})
            assert blocked.status_code == 429 and int(blocked.headers['retry-after']) > 0
            assert blocked.headers['cache-control'] == 'no-store'
            assert validate.call_count == 60
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app, client=('192.0.2.2', 123)),
                                     base_url='http://testserver') as other:
            assert (await other.post('/api/admin/mcp/', json={},
                                     headers={'Authorization': 'Bearer ' + token})).status_code == 401
        assert validate.call_count == 61
        db.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_verification_capacity_survives_cancellation_without_blocking_machine_key(mcp_app, monkeypatch):
    monkeypatch.setattr(transport, '_OAUTH_VERIFY_SLOTS', BoundedSemaphore(2))
    monkeypatch.setattr(transport, 'review_mcp_http', transport.JSONResponse({'ok': True}))
    loop = asyncio.get_running_loop()
    started = [asyncio.Event(), asyncio.Event()]
    release = Event()
    tokens = ['droa_' + c * 43 for c in 'abc']

    def verify(token):
        index = tokens.index(token)
        if index < 2:
            loop.call_soon_threadsafe(started[index].set)
            assert release.wait(5), 'test verification worker was not released'
        raise transport.review_oauth.OAuthError('invalid_token', 401)

    with patch.object(transport.review_oauth, 'validate_access_token', side_effect=verify) as validate, \
            patch.object(transport, 'get_client', return_value=Mock(spec=[])):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url='http://testserver') as http:
            tasks = [asyncio.create_task(http.post('/api/admin/mcp/', json={}, headers={
                'Authorization': 'Bearer ' + token})) for token in tokens[:2]]
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 2)
                tasks[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks[0]
                blocked = await http.post('/api/admin/mcp/', json={}, headers={'Authorization': 'Bearer ' + tokens[2]})
                assert blocked.status_code == 429 and blocked.headers['retry-after'] == '1'
                assert validate.call_count == 2
                # The OAuth workers cannot exhaust the shared pool used by key-authenticated tools.
                keyed = await asyncio.wait_for(http.post('/api/admin/mcp/', json={}, headers={'X-API-Key': KEY}), 2)
                assert keyed.status_code == 200
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
            assert tasks[1].result().status_code == 401
            # Verification failures release capacity for subsequent requests.
            assert (await http.post('/api/admin/mcp/', json={}, headers={
                'Authorization': 'Bearer ' + tokens[2]})).status_code == 401
            assert validate.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('token', ['droa_short', 'droa_' + 'a' * 44, 'droa_' + '!' * 43])
async def test_malformed_oauth_bearer_never_dispatches_verification(mcp_app, token):
    with patch.object(transport.review_oauth, 'validate_access_token') as validate, \
            patch.object(transport, 'get_client') as db:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url='http://testserver') as http:
            response = await http.post('/api/admin/mcp/', json={}, headers={'Authorization': 'Bearer ' + token})
            assert response.status_code == 401
        validate.assert_not_called()
        db.assert_not_called()
