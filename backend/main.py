from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from migrate import run_migrations
from routers import (
    admin,
    admin_discovery,
    articles,
    discover,
    feeds,
    me,
    opml,
    recommendations,
)

logging.basicConfig(level=logging.INFO)

# Comfortably above the largest legitimate body (the 5 MiB OPML upload cap in
# routers/opml.py) plus multipart overhead. FastAPI/Starlette buffer a
# request's body fully in memory before any route or pydantic validation
# runs, so without this, any JSON POST endpoint — including the public,
# unauthenticated /api/discover routes — has no limit on payload size and is
# a memory-exhaustion vector.
MAX_REQUEST_BODY_BYTES = 6 * 1024 * 1024


class _RequestBodyTooLarge(Exception):
    """Raised from the wrapped `receive` once streamed body bytes exceed the
    cap, so it's caught around the app call rather than needing every ASGI
    message site downstream to check a size themselves."""


class MaxBodySizeMiddleware:
    """Rejects a request whose body exceeds the cap. A declared
    Content-Length over the cap is rejected up front, before the body is
    read. Requests with no (or an understated) Content-Length — e.g. chunked
    transfer-encoding — are instead capped by counting bytes as they stream
    through `receive`, so they can't bypass the check FastAPI/Starlette
    would otherwise only apply after buffering the full body in memory.
    """

    def __init__(self, app, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    too_large = int(value) > self.max_body_size
                except ValueError:
                    too_large = False
                if too_large:
                    response = PlainTextResponse(
                        "Request body too large", status_code=413
                    )
                    await response(scope, receive, send)
                    return
                break

        total_size = 0

        async def limited_receive():
            nonlocal total_size
            message = await receive()
            if message["type"] == "http.request":
                total_size += len(message.get("body", b""))
                if total_size > self.max_body_size:
                    raise _RequestBodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            # Only reachable before the app has sent a response: every route
            # here fully consumes the body (JSON/multipart parsing) before
            # producing a response, and none stream a response body, so no
            # ASGI messages have gone out on `send` yet at this point.
            response = PlainTextResponse("Request body too large", status_code=413)
            await response(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    yield


app = FastAPI(
    title="Driftread API",
    description="RSS 推薦平台 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS_ORIGINS defaults to "*" (open) — restrict to specific origins in production.
# Note: credentials (cookies) cannot be used with wildcard origins per the CORS spec.
_cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added after CORSMiddleware so it runs outermost (Starlette executes the
# most-recently-added middleware first), rejecting oversized requests before
# CORS or routing does any work.
app.add_middleware(MaxBodySizeMiddleware, max_body_size=MAX_REQUEST_BODY_BYTES)

# docker-compose.yml never publishes a port for this service — the only way
# to reach it at all is through the frontend nginx container, which sets
# X-Forwarded-For/X-Real-IP (frontend/nginx.conf). Without this, every
# request's scope["client"] is the nginx container's own address (uvicorn
# doesn't trust proxy headers by default), so anything keyed on request.client
# — e.g. rate_limit.py's per-IP limiter — sees one shared "client" for every
# real user behind the proxy instead of distinguishing them. trusted_hosts="*"
# is safe specifically because nginx is the only possible peer here; it would
# NOT be safe if this API were ever also reachable directly from the internet.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

app.include_router(feeds.router, prefix="/api")
app.include_router(articles.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(admin_discovery.router, prefix="/api")
app.include_router(me.router, prefix="/api")
app.include_router(opml.router, prefix="/api")
app.include_router(discover.router, prefix="/api")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
