"""Dedicated machine-only MCP for bounded source review tasks.

Streamable HTTP clients send X-API-Key: SOURCE_REVIEW_MCP_KEY on every
request. This transport does not accept personal JWTs, management sessions,
or ADMIN_API_KEY. It neither exposes human overrides nor arbitrary URL tools.
"""
import logging
import os
import secrets
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from database import get_client
from services import source_review
from services.discovery_config import discovery_enabled
from services.review_models import ReviewSubmission
from services.settings import get_setting

logger = logging.getLogger(__name__)
REVIEWER_ID = "external_agent"


class ReviewMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema["additionalProperties"] = False
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
        # Reject mixed browser credentials rather than silently changing scope.
        supplied = request.headers.get("x-api-key", "")
        if (not supplied or len(supplied) > 2048 or "authorization" in request.headers
                or "x-manage-session" in request.headers):
            await JSONResponse({"detail": "A dedicated source review API key is required"}, 401)(scope, receive, send)
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
