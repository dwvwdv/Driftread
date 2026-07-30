"""Every FEED_DISCOVERY_* knob, one accessor per variable.

Read from the environment on each call rather than captured at import time, so a
compose restart can retune the crawler without a code change — the same contract
services/feed_refresh.py's env accessors follow.

Defaults are deliberately conservative. Unlike FEED_REFRESH_ENABLED (true), the
discovery loop ships **off**: refresh only touches feeds an operator explicitly
imported, while discovery probes third parties nobody asked about, and an
existing deployment pulling :latest must not silently become a crawler. The two
network-hop stages (blogroll, directory) are separately off for the same reason —
article-link mining makes zero outbound requests, so it's the only stage that is
free.
"""
from __future__ import annotations

from env_utils import env_flag, env_float, env_int


def discovery_enabled() -> bool:
    """Master switch. Also a hard kill switch for POST /admin/discovery/run,
    which 503s while this is false — see routers/admin_discovery.py for why that
    deliberately differs from /admin/feeds/refresh-due."""
    return env_flag("FEED_DISCOVERY_ENABLED", False)


def tick_seconds() -> int:
    """How often the discovery loop runs a cycle. Not a per-host rate — that's
    host_delay_seconds() plus each target's own next_probe_at."""
    return env_int("FEED_DISCOVERY_TICK_SECONDS", 900)


# ── harvest (mining our own corpus for outbound links) ────────────────────────

def harvest_batch_size() -> int:
    return env_int("FEED_DISCOVERY_HARVEST_BATCH_SIZE", 10)


def harvest_articles_per_feed() -> int:
    return env_int("FEED_DISCOVERY_HARVEST_ARTICLES", 20)


def harvest_interval_hours() -> int:
    """How long before a harvested feed is mined again. A week by default: its
    outbound-link set changes far more slowly than its article list."""
    return env_int("FEED_DISCOVERY_HARVEST_INTERVAL_HOURS", 168)


def harvest_max_links_per_feed() -> int:
    """Distinct new hosts one feed may contribute per cycle. The first line of
    defence against a spammy feed flooding the frontier."""
    return env_int("FEED_DISCOVERY_HARVEST_MAX_LINKS_PER_FEED", 200)


def blogroll_enabled() -> bool:
    """Fetch each feed's website_url homepage and mine its links too. Off by
    default: this is a second class of outbound traffic on top of the free
    article-content path."""
    return env_flag("FEED_DISCOVERY_BLOGROLL_ENABLED", False)


def directory_enabled() -> bool:
    """Mine the discovery_sources list (awesome-lists, OPML directories). Off by
    default, same reasoning as blogroll_enabled()."""
    return env_flag("FEED_DISCOVERY_DIRECTORY_ENABLED", False)


def directory_batch_size() -> int:
    return env_int("FEED_DISCOVERY_DIRECTORY_BATCH_SIZE", 3)


# ── probe (actually reaching out to candidate sites) ─────────────────────────

def probe_batch_size() -> int:
    return env_int("FEED_DISCOVERY_PROBE_BATCH_SIZE", 20)


def probe_concurrency() -> int:
    return env_int("FEED_DISCOVERY_PROBE_CONCURRENCY", 3)


def probe_max_attempts() -> int:
    """Consecutive unreachable probes before a target is abandoned as
    'exhausted' — the discovery analogue of
    feed_refresh.AUTO_ARCHIVE_FAILURE_THRESHOLD."""
    return env_int("FEED_DISCOVERY_PROBE_MAX_ATTEMPTS", 3)


def probe_retry_hours() -> int:
    """Base delay for the doubling retry backoff in
    discovery_probe.next_probe_delay_hours()."""
    return env_int("FEED_DISCOVERY_PROBE_RETRY_HOURS", 24)


def host_delay_seconds() -> float:
    """Politeness gap between the requests one probe makes against a single host
    (robots.txt, the homepage, then up to seven well-known feed paths). A
    robots.txt Crawl-delay larger than this wins, up to
    services/robots.py::MAX_CRAWL_DELAY_SECONDS."""
    return env_float("FEED_DISCOVERY_HOST_DELAY_SECONDS", 2.0)


def respect_robots() -> bool:
    """Honor robots.txt. Turning this off also costs retry precision: the
    robots.txt fetch doubles as the reachability probe that lets
    discovery_probe.probe_one() tell "this site has no feed" (terminal) apart
    from "this site is down" (retry). See that module's docstring."""
    return env_flag("FEED_DISCOVERY_RESPECT_ROBOTS", True)


# ── frontier / promotion bounds ──────────────────────────────────────────────

def max_frontier_size() -> int:
    """Pending-target ceiling. Above it, harvesting still records referrers
    (so the evidence count for known hosts keeps improving) but creates no new
    targets."""
    return env_int("FEED_DISCOVERY_MAX_FRONTIER_SIZE", 50_000)


def auto_promote_min_referrers() -> int:
    """Distinct existing feeds that must link to a candidate before it is
    imported without human review. 0 means never — every candidate waits for the
    admin queue.

    NOTE the minimum=0: 0 is a meaningful value here, so it must not be treated
    as garbage and replaced by the default the way the FEED_REFRESH_* knobs treat
    values below 1. Getting this wrong silently enables auto-promotion.
    """
    return env_int("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", 0, minimum=0)
