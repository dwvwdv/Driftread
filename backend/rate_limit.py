"""In-process rate limiting for endpoints that trigger outbound network fetches.

The public, unauthenticated /api/discover and /api/discover/import endpoints
each make one or more outbound HTTP requests to a client-chosen URL (see
services/feed_discovery.py). Without a limit, anyone can call them in a tight
loop to run the server as a free network scanner/amplifier — the existing
SSRF guard (validate_fetch_url) stops requests from reaching private targets,
but does nothing to stop volume against public ones.

This is a per-process sliding-window counter, not a distributed one (no
Redis) — the project runs as a single backend container (see
docker-compose.yml), so a process-local limiter is sufficient.
"""
from __future__ import annotations
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

DEFAULT_MAX_REQUESTS = 20
DEFAULT_WINDOW_SECONDS = 60.0

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def rate_limit(
    name: str,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
):
    """Build a FastAPI dependency allowing `max_requests` per `window_seconds`
    per client IP, bucketed by `name` so distinct endpoints don't share a quota.
    """

    def dependency(request: Request) -> None:
        key = (name, _client_ip(request))
        now = time.monotonic()
        hits = _hits[key]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= max_requests:
            retry_after = window_seconds - (now - hits[0])
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, please try again later",
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )
        hits.append(now)

    return dependency
