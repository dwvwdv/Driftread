"""OAuth discovery and authorization for ChatGPT's source-review MCP client."""
from datetime import datetime
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse, Response

from rate_limit import rate_limit
from services import manage_auth, review_oauth

router = APIRouter(tags=["source-review-oauth"])
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}
MAX_OAUTH_BODY = 8192


def oauth_error(exc):
    return JSONResponse({"error": exc.error, "error_description": exc.description},
                        exc.status_code, headers=NO_STORE)


async def invoke(operation, *args, status=200):
    try:
        result = await run_in_threadpool(operation, *args)
        if status == 204:
            return Response(status_code=204, headers=NO_STORE)
        return JSONResponse(result, status, headers=NO_STORE)
    except review_oauth.OAuthError as exc:
        return oauth_error(exc)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, exc.status_code,
                            headers={**(exc.headers or {}), **NO_STORE})


def invalid_request():
    return review_oauth.OAuthError("invalid_request", status_code=400,
                                  description="Invalid OAuth request")


async def bounded_body(request):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_OAUTH_BODY:
            raise invalid_request()
    return bytes(content)


async def form_params(request):
    if request.headers.get("authorization"):
        raise review_oauth.OAuthError("invalid_client", status_code=401,
                                      description="Only public OAuth clients are supported")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise invalid_request()
    try:
        pairs = parse_qsl((await bounded_body(request)).decode("utf-8"),
                          keep_blank_values=True, strict_parsing=True, max_num_fields=20)
    except (ValueError, UnicodeError):
        raise invalid_request() from None
    if len({key for key, _ in pairs}) != len(pairs):
        raise invalid_request()
    return dict(pairs)


@router.get("/.well-known/oauth-authorization-server")
async def authorization_metadata():
    return await invoke(review_oauth.oauth_metadata)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/api/admin/mcp")
@router.get("/.well-known/oauth-protected-resource/api/admin/mcp/")
async def protected_resource_metadata():
    return await invoke(review_oauth.resource_metadata)


@router.post("/api/review-oauth/register", dependencies=[Depends(rate_limit("review_oauth_register", 10))])
async def register(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return oauth_error(invalid_request())
    try:
        import json
        body = json.loads(await bounded_body(request))
        if not isinstance(body, dict):
            raise invalid_request()
    except (ValueError, UnicodeError):
        return oauth_error(invalid_request())
    except review_oauth.OAuthError as exc:
        return oauth_error(exc)
    return await invoke(review_oauth.register_client, body, status=201)


@router.get("/api/review-oauth/authorize", dependencies=[Depends(rate_limit("review_oauth_authorize", 20))])
async def authorize(request: Request):
    if len(request.scope.get("query_string", b"")) > MAX_OAUTH_BODY:
        return oauth_error(invalid_request())
    pairs = request.query_params.multi_items()
    if len(pairs) > 20 or len({key for key, _ in pairs}) != len(pairs):
        return oauth_error(invalid_request())
    try:
        result = await run_in_threadpool(review_oauth.begin_authorization, dict(pairs))
        return RedirectResponse(result["authorize_url"], status_code=302, headers=NO_STORE)
    except review_oauth.OAuthError as exc:
        return oauth_error(exc)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, exc.status_code, headers=NO_STORE)


@router.post("/api/review-oauth/token", dependencies=[Depends(rate_limit("review_oauth_token", 30))])
async def token(request: Request):
    try:
        params = await form_params(request)
    except review_oauth.OAuthError as exc:
        return oauth_error(exc)
    return await invoke(review_oauth.exchange_token, params)


@router.post("/api/review-oauth/revoke", dependencies=[Depends(rate_limit("review_oauth_revoke", 20))])
async def revoke_token(request: Request):
    try:
        params = await form_params(request)
    except review_oauth.OAuthError as exc:
        return oauth_error(exc)
    return await invoke(review_oauth.revoke_token, params)


def browser_call(operation, authorization, manage_token, *args):
    # The broad ADMIN_API_KEY must never issue or revoke a browser OAuth grant.
    user = manage_auth.require_session(authorization, manage_token)
    return operation(*args, user, manage_token)


@router.get("/api/review-oauth/consent/{request_id}")
async def consent(request_id: str, authorization: str | None = Header(default=None),
                  x_manage_session: str | None = Header(default=None)):
    return await invoke(browser_call, review_oauth.get_consent, authorization, x_manage_session, request_id)


class ConsentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approved: bool


@router.post("/api/review-oauth/consent/{request_id}", dependencies=[Depends(rate_limit("review_oauth_consent", 20))])
async def decide(request_id: str, body: ConsentDecision, authorization: str | None = Header(default=None),
                 x_manage_session: str | None = Header(default=None)):
    return await invoke(browser_call, review_oauth.decide_authorization, authorization, x_manage_session,
                        request_id, body.approved)


@router.get("/api/review-oauth/grants")
async def grants(authorization: str | None = Header(default=None),
                 x_manage_session: str | None = Header(default=None)):
    return await invoke(browser_call, review_oauth.list_grants, authorization, x_manage_session)


@router.delete("/api/review-oauth/grants/{grant_id}")
async def delete_grant(grant_id: UUID, authorization: str | None = Header(default=None),
                       x_manage_session: str | None = Header(default=None)):
    return await invoke(browser_call, review_oauth.revoke_grant, authorization, x_manage_session,
                        str(grant_id), status=204)


class GrantExpiry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Required explicit null means permanent; an omitted field must not silently
    # turn a connection into an indefinite authorization.
    expires_at: AwareDatetime | None = Field(...)

    @field_validator('expires_at', mode='before')
    @classmethod
    def explicit_expiry(cls, value):
        if value is not None and (not isinstance(value, str) or len(value) > 64):
            raise ValueError('Expiry must be an ISO date-time or null')
        if value is not None:
            # AwareDatetime also accepts epoch strings; this management contract
            # deliberately requires a date-time with an explicit timezone.
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value


@router.patch("/api/review-oauth/grants/{grant_id}")
async def update_grant_expiry(grant_id: UUID, body: GrantExpiry,
                              authorization: str | None = Header(default=None),
                              x_manage_session: str | None = Header(default=None)):
    return await invoke(browser_call, review_oauth.update_grant_expiry, authorization, x_manage_session,
                        str(grant_id), body.expires_at)
