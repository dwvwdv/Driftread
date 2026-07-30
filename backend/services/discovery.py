"""The discovery cycle: the single entry point worker.py and the admin endpoint share.

Exactly the relationship services/feed_refresh.py::refresh_due() has with the
worker and POST /admin/feeds/refresh-due today — one implementation, two ways to
trigger it, so a hand-kicked cycle behaves identically to a scheduled one.

Each stage is wrapped individually. The worker's per-cycle try/except is the outer
net; this is the inner one, so a directory source that explodes doesn't cost us
the harvest, probe and promotion that would otherwise have run after it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from services.crawl_policy import make_gate
from services.directory_sources import harvest_sources_due, summarize_sources
from services.discovery_candidates import auto_promote_due, promote_approved
from services.discovery_config import directory_enabled
from services.discovery_probe import probe_due, summarize_probes
from services.feed_discovery import user_agent
from services.link_harvest import build_host_index, harvest_due, summarize_harvest

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

_EMPTY_DIRECTORY = {
    "processed": 0, "targets_created": 0, "feed_targets_created": 0, "failed": 0,
}
_EMPTY_HARVEST = {
    "processed": 0, "articles_scanned": 0, "anchors_seen": 0, "hosts_kept": 0,
    "targets_created": 0, "referrers_recorded": 0, "failed": 0,
}
_EMPTY_PROBE = {
    "processed": 0, "found": 0, "none_found": 0, "blocked": 0, "failed": 0,
    "exhausted": 0, "candidates_new": 0,
}


@dataclass(frozen=True)
class CycleSummary:
    directory: dict = field(default_factory=lambda: dict(_EMPTY_DIRECTORY))
    harvest: dict = field(default_factory=lambda: dict(_EMPTY_HARVEST))
    probe: dict = field(default_factory=lambda: dict(_EMPTY_PROBE))
    auto_promoted: int = 0
    imported: int = 0


async def run_cycle(
    db: "Client",
    harvest_limit: int | None = None,
    probe_limit: int | None = None,
    max_concurrency: int | None = None,
    directory_limit: int | None = None,
) -> CycleSummary:
    """One full pass: directories → article harvest → probe → promote.

    The order matters. Directories and harvesting both feed the frontier, so they
    run before the probe that drains it; a host discovered this cycle is probed in
    the same cycle rather than waiting for the next tick. Promotion runs last so a
    candidate found moments ago can still be auto-imported if it clears the
    threshold.
    """
    directory = dict(_EMPTY_DIRECTORY)
    harvest = dict(_EMPTY_HARVEST)
    probe = dict(_EMPTY_PROBE)
    auto_promoted = imported = 0

    # Both harvest stages make outbound requests — the blogroll hop and the
    # directory-page fetch — so both are gated; without this,
    # FEED_DISCOVERY_RESPECT_ROBOTS would only apply to the probe and quietly
    # mean nothing for the other two.
    #
    # They get the gate *without* the target denylist, though. These stages read
    # from a place we chose (an admin-configured directory, or the homepage of a
    # feed already in the catalog), and DENY_HOST_SUFFIXES answers a different
    # question: "is this host ever worth cataloguing as a blog?" Applying it here
    # would permanently block the shipped default directory list, which is hosted
    # on github.com. The links extracted from those pages are still filtered
    # normally, and the probe — which really is chasing a candidate — keeps the
    # full gate.
    # pace=True because these stages fetch a single page each and have no spacing
    # of their own — without it the robots.txt request and the page request go
    # out back to back. discover_feeds() paces itself, so the probe's gate doesn't.
    source_gate = make_gate(user_agent(), apply_denylist=False, pace=True)

    # One index for the whole cycle, shared by both harvest stages: a host a
    # directory contributes is then visible to article harvesting instead of
    # being inserted twice.
    try:
        index = build_host_index(db)
    except Exception:
        logger.exception("Could not build host index — skipping harvest stages")
        index = None

    if index is not None and directory_enabled():
        try:
            directory = summarize_sources(
                await harvest_sources_due(
                    db, index, limit=directory_limit, allow_url=source_gate
                )
            )
        except Exception:
            logger.exception("Directory harvest stage failed")

    if index is not None:
        try:
            harvest = summarize_harvest(
                await harvest_due(
                    db, limit=harvest_limit, allow_url=source_gate, index=index
                )
            )
        except Exception:
            logger.exception("Article harvest stage failed")

    try:
        probe = summarize_probes(
            await probe_due(db, limit=probe_limit, max_concurrency=max_concurrency)
        )
    except Exception:
        logger.exception("Probe stage failed")

    try:
        auto_promoted = len(auto_promote_due(db))
    except Exception:
        logger.exception("Auto-promote stage failed")

    try:
        imported = len(promote_approved(db))
    except Exception:
        logger.exception("Approved-candidate promotion stage failed")

    return CycleSummary(
        directory=directory,
        harvest=harvest,
        probe=probe,
        auto_promoted=auto_promoted,
        imported=imported,
    )
