"""The crawl policy every autonomous outbound request is gated on.

Lives in its own module rather than inside discovery_probe because it is not the
probe's policy — it is the *loop's* policy, and three stages make outbound
requests: the blogroll hop, the directory-page fetch, and the probe itself.
Building it here and passing it down means adding a stage can't quietly skip it,
which is exactly what happened when this was a private helper in the probe (see
docs/SECURITY.md rule 9, and #24 §4 for the same lesson one layer down).
"""
from __future__ import annotations

import asyncio

from services import robots
from services.discovery_config import host_delay_seconds, respect_robots
from services.feed_discovery import AllowUrl
from services.link_harvest import is_denied_host, normalize_host


def make_gate(
    user_agent: str, *, apply_denylist: bool = True, pace: bool = False
) -> AllowUrl:
    """A feed_discovery.AllowUrl enforcing (optionally) the denylist ∧ robots.txt.

    Handed to fetch_with_cap_response, which evaluates it on the initial URL and
    again on every redirect hop — so a 3xx can't carry a request to a host the
    denylist or robots.txt would have refused.

    `apply_denylist=False` is for the stages that read from a place we chose
    rather than probe a place we found: an admin-configured directory page, or
    the homepage of a feed already in the catalog. DENY_HOST_SUFFIXES answers
    "is this host ever a blog worth cataloguing?", which is the wrong question
    there — the shipped default directory list is hosted on github.com, and
    applying the denylist to it blocks every one of those sources forever. The
    URL-shape and robots checks still apply, and the *links extracted* from
    those pages go through the denylist as normal.

    `pace=True` makes the gate sleep before returning an allow. That is for
    callers with no pacing of their own: the harvest stages fetch a single page
    each, so without it the robots.txt request and the page request go out back
    to back and the advertised per-host interval means nothing. The gate is the
    right place for it because the choke point calls it immediately before every
    request, redirect hops included. discover_feeds() paces itself, so the probe
    leaves this off rather than sleeping twice.
    """

    async def allow(url: str) -> bool:
        host = normalize_host(url)
        if not host:
            return False
        if apply_denylist and is_denied_host(host):
            return False

        delay = host_delay_seconds() if pace else 0.0
        if respect_robots():
            decision = await robots.check(url, user_agent)
            if not decision.allowed:
                return False
            if pace and decision.crawl_delay:
                delay = max(delay, decision.crawl_delay)

        if pace and delay > 0:
            await asyncio.sleep(min(delay, robots.MAX_CRAWL_DELAY_SECONDS))
        return True

    return allow
