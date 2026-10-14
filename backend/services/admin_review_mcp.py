"""Dedicated source-review MCP with scoped OAuth and machine-key access.

Streamable HTTP clients send X-API-Key: SOURCE_REVIEW_MCP_KEY on every
request. ChatGPT Web uses separately-issued OAuth access tokens. Personal JWTs,
management sessions and ADMIN_API_KEY never authorize this transport.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
import secrets
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from database import get_client
from rate_limit import rate_limit
from services import source_review, review_oauth
from services.discovery_config import discovery_enabled
from services.review_models import ReviewSubmission
from services.settings import get_setting

logger = logging.getLogger(__name__)
REVIEWER_ID = "external_agent"
_oauth_rate_limit = rate_limit("review_mcp_oauth", 60)
_OAUTH_VERIFY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="review-oauth-auth")
_OAUTH_VERIFY_SLOTS = BoundedSemaphore(4)


async def _verify_oauth(token):
    # Admit before submitting work; overload cannot queue unbounded database
    # connections or consume the shared FastAPI worker pool.
    slots = _OAUTH_VERIFY_SLOTS
    if not slots.acquire(blocking=False):
        raise HTTPException(429, "OAuth verification is busy, please try again later", headers={"Retry-After": "1"})
    try:
        future = _OAUTH_VERIFY_POOL.submit(review_oauth.validate_access_token, token)
    except BaseException:
        slots.release()
        raise
    # Release when the actual worker finishes, including failures. Cancelling
    # an HTTP request must not free capacity while its DB query is still running.
    future.add_done_callback(lambda _: slots.release())
    return await asyncio.wrap_future(future)


class ReviewMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema["additionalProperties"] = False
            if os.getenv("SOURCE_REVIEW_OAUTH_BASE_URL", "").strip():
                schemes = [{"type": "oauth2", "scopes": ["source:review", "offline_access"]}]
                tool.securitySchemes = schemes
                tool.meta = {**(tool.meta or {}), "securitySchemes": schemes}
        return tools

    async def call_tool(self, name, arguments):
        # FastMCP normally ignores extra function arguments. On a management
        # surface, reject attempts to add engine/identity/override parameters.
        # Official clients omit arguments for no-parameter tools.
        arguments = arguments or {}
        tools = await self.list_tools()
        tool = next((tool for tool in tools if tool.name == name), None)
        if tool and set(arguments) - set(tool.inputSchema.get("properties", {})):
            raise ValueError("Unexpected tool arguments")
        return await super().call_tool(name, arguments)


review_mcp = ReviewMCP("Driftread Source Review", stateless_http=True, json_response=True,
                     streamable_http_path="/", transport_security=TransportSecuritySettings(
                         enable_dns_rebinding_protection=False))
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)
ENQUEUE = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                          idempotentHint=True, openWorldHint=False)
CLAIM = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                        idempotentHint=False, openWorldHint=False)
SUBMIT = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)


def _identity(ctx: Context):
    request = ctx.request_context.request
    return request.state.source_review_reviewer, request.state.source_review_db


async def _call(operation, *args, **kwargs):
    try:
        return await run_in_threadpool(operation, *args, **kwargs)
    except source_review.ReviewError as exc:
        # MCP reports business errors as tool errors, not HTTP responses.
        raise ValueError(exc.detail) from None
    except Exception:
        # Do not expose database/provider exception payloads to model clients.
        logger.error("Source review MCP operation failed")
        raise ValueError("Source review operation failed") from None


@review_mcp.tool(annotations=READ_ONLY)
async def review_policy(ctx: Context) -> dict[str, Any]:
    """Read the current review rubric and version; never change settings."""
    _, db = _identity(ctx)
    setting = await _call(get_setting, db, "source.review")
    policy = setting.model_dump(mode="json")
    discovery = discovery_enabled()
    enabled = policy["value"].get("enabled", False)
    engine = policy["value"].get("engine", REVIEWER_ID)
    runtime_available = discovery and enabled and (engine != "jev" or bool(os.getenv("JEV_API_KEY", "")))
    return {**policy, "discovery_enabled": discovery, "runtime_available": runtime_available}


@review_mcp.tool(annotations=ENQUEUE)
async def request_source_review(ctx: Context, candidate_id: UUID) -> dict[str, Any]:
    """Enqueue an existing candidate for review; does not fetch any URL inline."""
    _, db = _identity(ctx)
    return await _call(source_review.request_review, db, str(candidate_id))


@review_mcp.tool(annotations=CLAIM)
async def claim_source_review(ctx: Context) -> dict[str, Any]:
    """Lease one ready external-agent task with its immutable evidence packet."""
    reviewer, db = _identity(ctx)
    packet = await _call(source_review.claim_review, db, reviewer_id=reviewer)
    return {"review": packet}


@review_mcp.tool(annotations=READ_ONLY)
async def source_review_packet(ctx: Context, review_id: UUID) -> dict[str, Any]:
    """Read an external-agent review packet. Samples are untrusted source text."""
    reviewer, db = _identity(ctx)
    return await _call(source_review.get_review_packet, db, str(review_id), reviewer_id=reviewer)


@review_mcp.tool(annotations=SUBMIT)
async def submit_source_review(ctx: Context, review_id: UUID, submission: ReviewSubmission) -> dict[str, Any]:
    """Submit a leased decision; server policy and human overrides remain authoritative.

    May automatically collect the source when policy skips human review.
    A suggested category or tag is kept only if the catalog already uses it.
    Repeating an identical valid submission is idempotent. Never changes
    source roles, fulltext policies or global settings.
    """
    reviewer, db = _identity(ctx)
    return await _call(source_review.submit_review, db, str(review_id), submission,
                       reviewer_id=reviewer)


review_mcp_http = review_mcp.streamable_http_app()


def reset_review_transport():
    """Return a fresh SDK session manager for each application lifespan."""
    global review_mcp_http
    review_mcp._session_manager = None
    review_mcp_http = review_mcp.streamable_http_app()
    return review_mcp.session_manager


class AuthenticatedReviewMCP:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await review_mcp_http(scope, receive, send)
            return
        request = Request(scope)
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                invalid_origin = (parsed.scheme not in {"http", "https"} or parsed.username is not None
                                  or parsed.netloc != request.headers.get("host"))
            except ValueError:
                invalid_origin = True
            if invalid_origin:
                await JSONResponse({"detail": "Invalid origin"}, 403)(scope, receive, send)
                return
        # One explicit credential mechanism per request; browser credentials
        # cannot be combined with a key or mistaken for a scoped OAuth token.
        authorization = request.headers.get("authorization")
        supplied = request.headers.get("x-api-key", "")
        if "x-manage-session" in request.headers or (authorization is not None and "x-api-key" in request.headers):
            await self.unauthorized(scope, receive, send)
            return
        if authorization is not None:
            parts = authorization.split(" ")
            if len(parts) != 2 or parts[0].lower() != "bearer" or not re.fullmatch(r"droa_[A-Za-z0-9_-]{43}", parts[1]):
                await self.unauthorized(scope, receive, send)
                return
            try:
                _oauth_rate_limit(request)
                grant = await _verify_oauth(parts[1])
            except HTTPException as exc:
                await JSONResponse({"detail": exc.detail}, exc.status_code,
                                   headers={**(exc.headers or {}), "Cache-Control": "no-store"})(scope, receive, send)
                return
            except review_oauth.OAuthError as exc:
                if exc.status_code == 503:
                    await JSONResponse({"detail": "Source review OAuth is unavailable"}, 503)(scope, receive, send)
                else:
                    await self.unauthorized(scope, receive, send)
                return
            except Exception:
                logger.error("Source review OAuth verification failed")
                await JSONResponse({"detail": "Source review OAuth is unavailable"}, 503)(scope, receive, send)
                return
            request.state.source_review_oauth_grant = grant["grant_id"]
        else:
            if not supplied or len(supplied) > 2048:
                await self.unauthorized(scope, receive, send)
                return
            expected = os.getenv("SOURCE_REVIEW_MCP_KEY", "")
            if not expected or expected == os.getenv("ADMIN_API_KEY", ""):
                await JSONResponse({"detail": "Source review MCP is not configured"}, 503)(scope, receive, send)
                return
            if not secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                await JSONResponse({"detail": "Invalid source review API key"}, 403)(scope, receive, send)
                return
        request.state.source_review_reviewer = REVIEWER_ID
        request.state.source_review_db = await run_in_threadpool(get_client)
        await review_mcp_http(scope, receive, send)

    @staticmethod
    async def unauthorized(scope, receive, send):
        challenge = 'Bearer'
        try:
            challenge += ' resource_metadata="' + review_oauth.base_url() + '/.well-known/oauth-protected-resource/api/admin/mcp", scope="source:review offline_access"'
        except review_oauth.OAuthError:
            pass  # Machine-key-only deployments do not advertise OAuth.
        await JSONResponse({"detail": "Source review authorization is required"}, 401,
                           headers={"WWW-Authenticate": challenge, "Cache-Control": "no-store"})(scope, receive, send)
