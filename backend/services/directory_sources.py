"""Mine curated directory pages for candidate sites and feeds.

Article-link harvesting only works once the catalog has something in it. This is
the bootstrap: an admin-maintained list of index pages, in two flavours.

- ``links_page`` — an HTML page (an awesome-list, a blogroll directory). Every
  external <a href> becomes a frontier *host*, exactly like blogroll mining.
- ``opml`` — an OPML/XML directory. Every ``outline/@xmlUrl`` is already a feed
  URL, so it becomes a frontier target in its own right; discover_feeds()'s first
  stage already handles "the URL is itself a feed", so the probe needs no branch
  for it. These dedupe by URL rather than by host, since one host can legitimately
  publish many feeds.

Deliberately *not* here: scrapers for Hacker News, Reddit and friends. Their
markup changes under you and each one is its own maintenance burden. The
supported way to mine an aggregator is to import its RSS feed as an ordinary feed
— then article-link harvesting picks up every submitted domain for free, with no
code at all. That's documented in docs/FEATURES.md.
"""
from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from services.discovery_config import directory_batch_size, probe_retry_hours
from services.feed_discovery import (
    MAX_FEED_BYTES,
    AllowUrl,
    DiscoveryError,
    fetch_with_cap,
    user_agent,
)
from services.link_harvest import (
    HostIndex,
    extract_anchor_hosts,
    is_denied_host,
    normalize_host,
    record_targets,
)

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

SEEDS_PATH = Path(__file__).resolve().parent.parent / "seeds" / "discovery_sources.json"

# Feed URLs taken from one OPML directory. A public OPML can list thousands;
# there is no reason to swallow a whole directory in one cycle when the source is
# re-harvested weekly anyway.
MAX_OPML_FEEDS_PER_SOURCE = 500

# How far a failing source backs off. Directories change slowly, so unlike the
# probe there's no doubling — one retry interval is plenty.
_FAILURE_RETRY_HOURS_MULTIPLIER = 2


@dataclass(frozen=True)
class SourceHarvestResult:
    source_id: str
    url: str
    kind: str
    targets_created: int = 0
    feed_targets_created: int = 0
    error: str | None = None


def load_default_sources(db: "Client") -> int:
    """Seed `discovery_sources` from backend/seeds/discovery_sources.json.

    Idempotent via on_conflict="url", so re-running only fills gaps and never
    resets an operator's `enabled` flag or interval on a row they've already
    tuned — the payload deliberately carries no such columns.

    The list lives in JSON rather than in the migration because it is data an
    operator should be able to edit, not schema.
    """
    try:
        entries = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read default discovery sources: %s", e)
        return 0

    payload = []
    for entry in entries:
        url = (entry.get("url") or "").strip()
        kind = entry.get("kind") or "links_page"
        if not url or kind not in ("links_page", "opml"):
            continue
        payload.append({"url": url, "kind": kind, "label": entry.get("label")})

    if not payload:
        return 0
    result = db.table("discovery_sources").upsert(payload, on_conflict="url").execute()
    return len(result.data or [])


def select_due_sources(db: "Client", limit: int) -> list[dict]:
    """Enabled sources whose next_harvest_at has passed. Matches
    discovery_sources_due_idx from migration 006."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        db.table("discovery_sources")
        .select("*")
        .is_("enabled", True)
        .lte("next_harvest_at", now)
        .order("next_harvest_at")
        .limit(limit)
        .execute()
    )
    return list(result.data or [])


def extract_opml_feed_urls(xml_text: str) -> list[str]:
    """Every outline/@xmlUrl in an OPML document, in document order.

    Parsed with defusedxml, which rejects DTD entity declarations and external
    references — the same billion-laughs / XXE hardening routers/opml.py applies
    to user uploads, and needed here for the same reason: the bytes come from
    somewhere we don't control.
    """
    try:
        root = safe_fromstring(xml_text)
    except (ET.ParseError, DefusedXmlException) as e:
        raise DiscoveryError(f"Invalid OPML: {e}") from e

    urls: list[str] = []
    seen: set[str] = set()

    def walk(node: ET.Element) -> None:
        for child in node:
            if child.tag == "outline":
                url = (child.get("xmlUrl") or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
            walk(child)

    walk(root)
    return urls


def record_feed_targets(
    db: "Client", feed_urls: list[str], index: HostIndex
) -> int:
    """Insert OPML feed URLs as frontier targets. Returns how many were created.

    Dedupes by URL, not by host — but still refuses any host an admin rejected or
    a probe found blocked, so a directory listing can't walk a banned site back
    in under a different URL.
    """
    if index.frontier_full:
        return 0

    payload: list[dict] = []
    seen: set[str] = set()
    for raw in feed_urls[:MAX_OPML_FEEDS_PER_SOURCE]:
        host = normalize_host(raw)
        if not host or is_denied_host(host) or host in index.blocked_hosts:
            continue
        if host in index.feed_hosts:
            # We already carry a feed on this host; the probe would only
            # rediscover what we have.
            continue
        if raw in index.target_urls or raw in seen:
            continue
        seen.add(raw)
        payload.append({"url": raw, "host": host, "source": "opml"})

    if not payload:
        return 0
    result = db.table("discovery_targets").insert(payload).execute()
    created = list(result.data or [])
    for row in created:
        index.target_hosts.setdefault(row["host"], str(row["id"]))
    return len(created)


async def _fetch(url: str, allow_url: AllowUrl | None) -> str:
    headers = {
        "User-Agent": user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml,*/*",
    }
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=15.0, headers=headers
    ) as client:
        text, _ctype = await fetch_with_cap(
            client, url, MAX_FEED_BYTES, allow_url=allow_url
        )
    return text


def _reschedule(db: "Client", source: dict, *, failed: bool, created: int) -> None:
    now = datetime.now(timezone.utc)
    hours = source.get("interval_hours") or 168
    update: dict = {"last_harvested_at": now.isoformat()}
    if failed:
        update["attempts"] = (source.get("attempts") or 0) + 1
        hours = min(hours, probe_retry_hours() * _FAILURE_RETRY_HOURS_MULTIPLIER)
    else:
        update["attempts"] = 0
        update["last_failure_reason"] = None
        update["targets_created"] = (source.get("targets_created") or 0) + created
    update["next_harvest_at"] = (now + timedelta(hours=hours)).isoformat()
    db.table("discovery_sources").update(update).eq("id", str(source["id"])).execute()


async def harvest_source_one(
    db: "Client", source: dict, index: HostIndex, allow_url: AllowUrl | None = None
) -> SourceHarvestResult:
    """Harvest one directory source. Never raises — a bad source is recorded and
    backed off, the same contract harvest_one() and refresh_one() follow."""
    source_id = str(source["id"])
    url = source["url"]
    kind = source.get("kind") or "links_page"
    created = feed_created = 0
    error: str | None = None

    try:
        text = await _fetch(url, allow_url)
        if kind == "opml":
            feed_created = record_feed_targets(db, extract_opml_feed_urls(text), index)
        else:
            host_urls: dict[str, str] = {}
            for host, _absolute in extract_anchor_hosts(text, url):
                if host in index.feed_hosts or is_denied_host(host):
                    continue
                host_urls.setdefault(host, f"https://{host}/")
            # feed_id=None: a directory has no owning feed, so there is no
            # distinct-feed edge to record — the target row is the whole signal.
            created, _ = record_targets(db, None, host_urls, index, source="directory")
    except (httpx.HTTPError, DiscoveryError, ValueError) as e:
        error = str(e)[:500]
        logger.info("Directory source %s failed: %s", url, error)
    except Exception as e:  # noqa: BLE001 - see docstring
        error = str(e)[:500]
        logger.exception("Directory source %s failed unexpectedly", url)

    if error:
        try:
            db.table("discovery_sources").update(
                {"last_failure_reason": error}
            ).eq("id", source_id).execute()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Could not record failure for source %s", url)

    try:
        _reschedule(db, source, failed=bool(error), created=created + feed_created)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not advance schedule for source %s", url)

    return SourceHarvestResult(
        source_id=source_id,
        url=url,
        kind=kind,
        targets_created=created,
        feed_targets_created=feed_created,
        error=error,
    )


async def harvest_sources_due(
    db: "Client",
    index: HostIndex,
    limit: int | None = None,
    allow_url: AllowUrl | None = None,
) -> list[SourceHarvestResult]:
    """Harvest every due directory source, sequentially.

    Takes the HostIndex rather than building its own so the whole cycle shares
    one snapshot — a host a directory contributes is then visible to the article
    harvest that runs next, instead of being inserted twice.
    """
    sources = select_due_sources(db, limit or directory_batch_size())
    if not sources:
        return []
    return [await harvest_source_one(db, s, index, allow_url) for s in sources]


def summarize_sources(results: list[SourceHarvestResult]) -> dict[str, int]:
    return {
        "processed": len(results),
        "targets_created": sum(r.targets_created for r in results),
        "feed_targets_created": sum(r.feed_targets_created for r in results),
        "failed": sum(1 for r in results if r.error),
    }
