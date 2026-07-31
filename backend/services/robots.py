"""robots.txt fetching and evaluation for the autonomous discovery crawler.

Only the discovery loop uses this. User-triggered discovery (POST /api/discover)
deliberately does not: a person asking us to look at one specific URL they typed
is not a crawler, and making them wait on a robots.txt round trip would be
gratuitous.

Two things make this more than a wrapper around urllib.robotparser:

1. **The fetch goes through the same choke point as everything else** —
   services/feed_discovery.py::fetch_with_cap, which applies the SSRF gate,
   manual redirect handling and a streaming byte cap. RobotFileParser.read()
   must never be called: it does its own unguarded urlopen(), which would bypass
   all of that.
2. **Reachability is part of the answer.** discovery_probe reuses it to tell
   "this site has no feed" (terminal, don't retry) apart from "this site is
   down" (retry with backoff) — a distinction discover_feeds() can't express,
   since it absorbs fetch errors into an empty list.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from services.feed_discovery import DiscoveryError, fetch_with_cap, ssrf_safe_client

logger = logging.getLogger(__name__)

# robots.txt is a small text file; anything approaching this is either broken or
# hostile. Far below MAX_FEED_BYTES on purpose.
MAX_ROBOTS_BYTES = 512 * 1024

CACHE_TTL_SECONDS = 3600.0

# Bounded for the same reason as rate_limit.py::MAX_TRACKED_CLIENTS: entries are
# created per distinct origin, and without a ceiling a long-running worker that
# probes enough hosts grows this dict without bound. Least-recently-used is
# evicted; the only cost is an early re-fetch.
MAX_CACHED_ORIGINS = 2000

# A hostile (or merely thoughtless) `Crawl-delay: 86400` would otherwise pin one
# of the few probe slots for a day. Honour the intent, cap the damage.
MAX_CRAWL_DELAY_SECONDS = 30.0

_FETCH_TIMEOUT = 10.0


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    # False only for transport/DNS failures and policy rejections — i.e. we never
    # got an HTTP answer. A 4xx or 5xx still means the host is up and talking.
    reachable: bool
    crawl_delay: float | None = None
    # Qualifies a disallow (meaningless when allowed). True means "not right now"
    # rather than "not ever": a 5xx on robots.txt disallows this request per RFC
    # 9309, but the site never actually told us to stay away, so the caller must
    # retry later instead of treating the target as permanently excluded.
    transient: bool = False


@dataclass
class _Cached:
    # None means no robots.txt was ever parsed (the host didn't answer), in which
    # case `reachable` is False and everything is disallowed.
    parser: RobotFileParser | None
    reachable: bool
    fetched_at: float
    transient: bool = False


# Keyed by origin only, not (origin, user_agent): the UA is fixed per process
# (feed_discovery.user_agent()), and can_fetch()/crawl_delay() take the UA at
# query time anyway, so a cached parse stays correct if it ever changes.
_cache: "OrderedDict[str, _Cached]" = OrderedDict()


def clear_cache() -> None:
    """Drop every cached robots.txt. For tests — the cache is process-global."""
    _cache.clear()


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _remember(origin: str, entry: _Cached) -> _Cached:
    _cache[origin] = entry
    _cache.move_to_end(origin)
    while len(_cache) > MAX_CACHED_ORIGINS:
        _cache.popitem(last=False)
    return entry


async def _fetch(origin: str, user_agent: str) -> _Cached:
    """Fetch and parse `origin`/robots.txt. Never raises."""
    now = time.monotonic()
    parser = RobotFileParser()
    # A fresh client per origin per TTL — cheap, given the cache, and it keeps the
    # UA header local rather than threading a shared client through every caller.
    client_kwargs = {
        "follow_redirects": False,
        "timeout": _FETCH_TIMEOUT,
        "headers": {"User-Agent": user_agent, "Accept": "text/plain,*/*"},
    }
    try:
        async with ssrf_safe_client(**client_kwargs) as client:
            text, _ctype = await fetch_with_cap(
                client, f"{origin}/robots.txt", MAX_ROBOTS_BYTES
            )
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if 400 <= status < 500:
            # RFC 9309 §2.3.1.3: "unavailable" (404/410 and 4xx generally) means
            # no restrictions. This is the common case — most sites have no
            # robots.txt at all.
            parser.allow_all = True
            return _remember(origin, _Cached(parser, reachable=True, fetched_at=now))
        # RFC 9309 §2.3.1.4: a server error means "unreachable" and the crawler
        # must assume complete disallow. The host answered, though, so it counts
        # as reachable for the probe's purposes.
        #
        # transient=True is the important part: a 503 is the site being briefly
        # broken, not the site telling us to stay away. Without the distinction
        # the caller would file the target under "robots says no" permanently and
        # never look at it again once the server recovered.
        parser.disallow_all = True
        return _remember(
            origin, _Cached(parser, reachable=True, fetched_at=now, transient=True)
        )
    except DiscoveryError as e:
        # The SSRF gate, the crawl policy, or the byte cap said no. Not a
        # statement about the host being up, so reachable stays False.
        logger.debug("robots.txt for %s rejected: %s", origin, e)
        return _remember(origin, _Cached(None, reachable=False, fetched_at=now))
    except (httpx.HTTPError, ValueError) as e:
        logger.debug("robots.txt for %s unreachable: %s", origin, e)
        return _remember(origin, _Cached(None, reachable=False, fetched_at=now))

    # parse() calls modified() internally, which sets last_checked — so can_fetch
    # and crawl_delay work without ever touching read().
    parser.parse(text.splitlines())
    return _remember(origin, _Cached(parser, reachable=True, fetched_at=now))


async def _load(origin: str, user_agent: str) -> _Cached:
    cached = _cache.get(origin)
    if cached is not None and (time.monotonic() - cached.fetched_at) < CACHE_TTL_SECONDS:
        _cache.move_to_end(origin)
        return cached
    # No lock: two probes racing on one origin just fetch robots.txt twice and
    # write the same answer. Targets are URL-unique and probe concurrency is 3,
    # so the race is rare and harmless — a lock map would be more machinery than
    # the duplicate request costs.
    return await _fetch(origin, user_agent)


async def check(url: str, user_agent: str) -> RobotsDecision:
    """May we fetch `url`, and is its host answering at all?"""
    cached = await _load(_origin(url), user_agent)
    if cached.parser is None:
        # No parse at all: the host didn't answer. Always retryable.
        return RobotsDecision(allowed=False, reachable=cached.reachable, transient=True)

    allowed = cached.parser.can_fetch(user_agent, url)

    # crawl_delay() returns None when a UA-specific group matched but declares no
    # Crawl-delay — the wildcard group's value does NOT fall through, so ask for
    # it explicitly. (Verified against CPython 3.11's robotparser.)
    delay = cached.parser.crawl_delay(user_agent)
    if delay is None:
        delay = cached.parser.crawl_delay("*")
    try:
        delay = min(float(delay), MAX_CRAWL_DELAY_SECONDS) if delay is not None else None
    except (TypeError, ValueError):
        delay = None

    return RobotsDecision(
        allowed=allowed,
        reachable=cached.reachable,
        crawl_delay=delay,
        transient=cached.transient,
    )


async def is_allowed(url: str, user_agent: str) -> bool:
    """check() reduced to a bool, for use as a feed_discovery.AllowUrl hook."""
    return (await check(url, user_agent)).allowed
