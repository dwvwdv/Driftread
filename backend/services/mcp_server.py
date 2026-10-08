"""Official MCP SDK transport, mounted inside the existing FastAPI process."""
from datetime import date
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth import get_current_user
from database import get_client
from services.consumption import daily_digest, excerpt, publications

# The deployment's reverse proxy owns Host trust. Origin is checked below;
# stateless requests are authenticated independently and never share sessions.
mcp = FastMCP("Driftread", stateless_http=True, json_response=True,
              streamable_http_path="/", transport_security=TransportSecuritySettings(
                  enable_dns_rebinding_protection=False))
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)


def _identity(ctx: Context):
    request = ctx.request_context.request
    return request.state.driftread_user, request.state.driftread_db


@mcp.tool(annotations=READ_ONLY)
def reading_stream(ctx: Context, limit: Annotated[int, Field(ge=1, le=100)] = 30) -> dict:
    """Read recent articles from your active reading subscriptions; excerpts only."""
    user, db = _identity(ctx)
    return {"items": [excerpt(r) for r in publications(db, user.id, limit=limit)]}


@mcp.tool(annotations=READ_ONLY)
def article(ctx: Context, article_id: UUID) -> dict:
    """Read a published article from your active subscriptions; rights apply."""
    user, db = _identity(ctx)
    from services.publications import get_publication
    row = get_publication(db, str(article_id))
    if row is None:
        raise ValueError("Article not found")
    subscribed = (db.table("user_feeds").select("feed_id").eq("user_id", user.id)
                  .eq("feed_id", str(row["feed_id"])).is_("muted_at", "null")
                  .maybe_single().execute())
    if not subscribed or not subscribed.data:
        raise ValueError("Article not found")
    result = excerpt(row)
    result["content"] = row.get("content") if row.get("fulltext_allowed") else None
    return result


@mcp.tool(annotations=READ_ONLY)
def digest(ctx: Context, day: date | None = None, timezone: str = "UTC",
           limit: Annotated[int, Field(ge=1, le=100)] = 100) -> dict:
    """List a local day's articles using source summaries; no generated summary."""
    user, db = _identity(ctx)
    return daily_digest(db, user.id, day, timezone, limit)


mcp_http = mcp.streamable_http_app()


def reset_transport():
    # SDK managers are single-use. Each FastAPI lifespan (also TestClient's)
    # receives a new manager while keeping the registered tools.
    global mcp_http
    mcp._session_manager = None
    mcp_http = mcp.streamable_http_app()
    return mcp.session_manager


class AuthenticatedMCP:

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await mcp_http(scope, receive, send)
            return
        request = Request(scope)
        # Browser requests must come from this origin. CLI/SDK clients have no Origin.
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            await JSONResponse({"detail": "Invalid origin"}, 403)(scope, receive, send)
            return
        try:
            user = get_current_user(request.headers.get("authorization"))
        except HTTPException as exc:
            await JSONResponse({"detail": exc.detail}, exc.status_code,
                               headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        request.state.driftread_user = user
        request.state.driftread_db = get_client()
        await mcp_http(scope, receive, send)
