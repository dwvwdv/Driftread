"""In-process rate limiting for endpoints that trigger outbound network fetches.

/api/discover (unauthenticated) and /api/discover/import (requires a signed-in
user — see routers/discover.py) each make one or more outbound HTTP requests
to a client-chosen URL (see services/feed_discovery.py). Without a limit,
anyone who can call them could run the server as a free network
scanner/amplifier in a tight loop — the existing SSRF guard
(validate_fetch_url) stops requests from reaching private targets, but does
nothing to stop volume against public ones.

This is a per-process sliding-window counter, not a distributed one (no
Redis) — the project runs as a single backend container (see
docker-compose.yml), so a process-local limiter is sufficient.
"""
from __future__ import annotations
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request, status

DEFAULT_MAX_REQUESTS = 20
DEFAULT_WINDOW_SECONDS = 60.0

# Caps total memory use. A bucket is created per distinct (endpoint, client
# IP) pair; expiration only drops timestamps *inside* a bucket, so without a
# cap on the number of buckets themselves, a long-running process serving
# enough unique clients — ordinary traffic churn, not just abuse — grows
# this dict without bound. When full, the least-recently-touched bucket is
# evicted to make room; at this project's scale that only means a client's
# history resets a little early, not a new way to bypass the limiter (the
# thing being guarded against is request *volume*, not perfect long-term
# memory of every past caller).
MAX_TRACKED_CLIENTS = 10_000

_hits: "OrderedDict[tuple[str, str], deque[float]]" = OrderedDict()


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
        hits = _hits.get(key)
        if hits is None:
            hits = deque()
            _hits[key] = hits
            if len(_hits) > MAX_TRACKED_CLIENTS:
                _hits.popitem(last=False)
        else:
            _hits.move_to_end(key)
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
