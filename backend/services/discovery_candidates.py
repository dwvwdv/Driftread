"""The review queue: recording discovered feeds, and promoting the approved ones.

The status column carries the whole point of this module. A candidate an admin
rejected must never come back — and it *would* come back, because the same feed
keeps being linked from the same articles every cycle. So the write path here
never upserts over `status`; it looks a URL up first and only ever inserts rows
it has confirmed absent.

Promotion writes `feeds` with `next_fetch_at = now()` and nothing else. It must
not fetch articles inline: the existing refresh worker already owns that, and a
bulk approval that fetched would take as long as the feeds are slow.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from services.discovery_config import auto_promote_min_referrers

if TYPE_CHECKING:
    from supabase import Client

    from services.feed_discovery import DiscoveryCandidate

logger = logging.getLogger(__name__)

MAX_TITLE_LEN = 200
MAX_URL_LEN = 2048

# Zero-width characters and bidirectional overrides. A bidi override inside a
# feed title can visually reverse a domain in the reviewer's list — "moc.dab" can
# be made to read as "bad.com" — so a human approving from the queue could be
# looking at something other than what gets stored.
_INVISIBLE = re.compile(
    "[​-‏‪-‮⁠-⁤⁦-⁩﻿]"
)
_WHITESPACE = re.compile(r"\s+")


def sanitize_text(value: str | None, max_len: int = MAX_TITLE_LEN) -> str | None:
    """Make third-party text safe to store in a world-readable table.

    `feeds` is public (migration 004's read policy) and rendered by the Angular
    app, so anything approved out of this queue ends up in front of every visitor.
    Angular escapes interpolation, which handles markup; this handles the things
    escaping doesn't — control characters, invisible characters, and text that
    lies about its own direction.
    """
    if not value:
        return None
    text = _INVISIBLE.sub("", value)
    text = "".join(
        ch for ch in text if ch == "\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return None
    return text[:max_len]


def sanitize_http_url(value: str | None) -> str | None:
    """A URL we're willing to store, or None. Enforces an http(s) scheme so a
    `javascript:` URL can never reach the database, let alone an href."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_URL_LEN:
        return None
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return candidate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_or_none(result) -> dict | None:
    # postgrest-py has shipped versions where maybe_single().execute() returns a
    # bare None on zero rows instead of a response object (docs/SECURITY.md #20).
    if not result or not getattr(result, "data", None):
        return None
    return result.data


def record_candidates(
    db: "Client", target: dict, candidates: list["DiscoveryCandidate"]
) -> tuple[int, int]:
    """Store a probe's findings. Returns (newly pending, already seen).

    Looks each URL up individually rather than batching with `.in_(...)`. That is
    a security choice, not laziness: these URLs come from remote HTML, and
    PostgREST's filter mini-language treats `,` `(` `)` as syntax
    (docs/SECURITY.md #14). A probe yields at most a handful of URLs, so the
    extra round trips cost nothing worth optimizing.
    """
    new = seen = 0
    target_id = str(target["id"]) if target.get("id") is not None else None
    source_host = target.get("host")

    for candidate in candidates:
        feed_url = sanitize_http_url(getattr(candidate, "feed_url", None))
        if not feed_url:
            continue

        existing = _row_or_none(
            db.table("discovery_candidates")
            .select("id,status")
            .eq("feed_url", feed_url)
            .maybe_single()
            .execute()
        )
        if existing:
            # Only ever refresh last_seen_at, and only while still pending. An
            # upsert here would flip a 'rejected' row back to 'pending' and
            # re-propose it forever — the exact failure the status column exists
            # to prevent.
            db.table("discovery_candidates").update(
                {"last_seen_at": _now()}
            ).eq("id", str(existing["id"])).eq("status", "pending").execute()
            seen += 1
            continue

        already_a_feed = _row_or_none(
            db.table("feeds").select("id").eq("url", feed_url).maybe_single().execute()
        )
        payload = {
            "target_id": target_id,
            "feed_url": feed_url,
            "title": sanitize_text(getattr(candidate, "title", None)),
            "website_url": sanitize_http_url(getattr(candidate, "website_url", None)),
            "source_host": source_host,
            "referring_feed_count": target.get("referring_feed_count") or 0,
        }
        if already_a_feed:
            # Recorded rather than skipped, so the frontier shows why this host
            # was worth probing even though it added nothing.
            payload["status"] = "imported"
            payload["feed_id"] = str(already_a_feed["id"])
            payload["reviewed_at"] = _now()
        else:
            payload["status"] = "pending"
            new += 1
        db.table("discovery_candidates").insert(payload).execute()

    return (new, seen)


def list_candidates(
    db: "Client",
    status: str | None = "pending",
    min_referrers: int = 0,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    query = db.table("discovery_candidates").select("*", count="exact")
    if status:
        query = query.eq("status", status)
    if min_referrers:
        query = query.gte("referring_feed_count", min_referrers)
    offset = (page - 1) * page_size
    result = (
        query.order("referring_feed_count", desc=True)
        .order("discovered_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    return (list(result.data or []), getattr(result, "count", None) or 0)


def get_candidate(db: "Client", candidate_id: str) -> dict | None:
    return _row_or_none(
        db.table("discovery_candidates")
        .select("*")
        .eq("id", str(candidate_id))
        .maybe_single()
        .execute()
    )


def block_host(db: "Client", host: str) -> int:
    """Reject *every* frontier row for `host`. Returns how many were rejected.

    By host and not by target id: discovery_targets is unique on url, so one host
    can legitimately hold several rows (an OPML directory contributing multiple
    feeds from one publisher). Rejecting only the row an admin happened to be
    looking at would leave its siblings pending, and they would go on being
    contacted and proposing feeds from a host that was explicitly blocked.

    The rows stay put rather than being deleted — link_harvest skips any host
    already in discovery_targets, so their continued presence is what makes the
    block permanent without a separate blocklist table.
    """
    if not host:
        return 0
    result = (
        db.table("discovery_targets")
        .update({"status": "rejected"})
        .eq("host", host)
        .execute()
    )
    return len(result.data or [])


def hold_candidate(
    db: "Client",
    candidate_id: str,
    note: str | None = None,
) -> dict | None:
    """Keep a viable alternate out of the catalog without discarding it.

    Held candidates remain reviewable and can later be approved if the active
    feed for their domain stops working. Unlike rejection, holding never blocks
    the discovery target.
    """
    candidate = get_candidate(db, candidate_id)
    if not candidate:
        return None

    updated = (
        db.table("discovery_candidates")
        .update({
            "status": "held",
            "review_note": sanitize_text(note, 500),
            "reviewed_at": _now(),
        })
        .eq("id", str(candidate_id))
        .execute()
    )
    return updated.data[0] if updated.data else None


def reject_candidate(
    db: "Client",
    candidate_id: str,
    note: str | None = None,
    block_host_too: bool = False,
) -> dict | None:
    candidate = get_candidate(db, candidate_id)
    if not candidate:
        return None

    updated = (
        db.table("discovery_candidates")
        .update({
            "status": "rejected",
            "review_note": sanitize_text(note, 500),
            "reviewed_at": _now(),
        })
        .eq("id", str(candidate_id))
        .execute()
    )

    if block_host_too:
        host = candidate.get("source_host")
        if not host and candidate.get("target_id"):
            target = _row_or_none(
                db.table("discovery_targets")
                .select("host")
                .eq("id", str(candidate["target_id"]))
                .maybe_single()
                .execute()
            )
            host = (target or {}).get("host")
        if host:
            block_host(db, host)

    return updated.data[0] if updated.data else None


def _mark_imported(db: "Client", candidate_id: str, feed_id: str) -> None:
    db.table("discovery_candidates").update({
        "status": "imported",
        "feed_id": str(feed_id),
        "reviewed_at": _now(),
    }).eq("id", str(candidate_id)).execute()


def promote_candidate(
    db: "Client",
    candidate: dict,
    category: str | None = None,
    tags: list[str] | None = None,
) -> dict | None:
    """Create the `feeds` row for an approved candidate. Never fetches."""
    feed_url = sanitize_http_url(candidate.get("feed_url"))
    if not feed_url:
        return None

    # A feed on this URL may already exist — imported by hand, by the extension,
    # or by OPML while this candidate sat in the queue. Link to it and stop.
    # Upserting instead would overwrite a curated title, website, category and
    # tags with scraped values and this call's (probably empty) defaults, so an
    # otherwise harmless duplicate approval would quietly damage the catalog.
    existing = _row_or_none(
        db.table("feeds").select("*").eq("url", feed_url).maybe_single().execute()
    )
    if existing:
        _mark_imported(db, candidate["id"], existing["id"])
        return existing

    # Fall back to the choices stored at approval time, so a retry from
    # promote_approved() reproduces what the reviewer actually picked.
    if category is None:
        category = candidate.get("approved_category")
    if tags is None:
        tags = candidate.get("approved_tags")

    feed_data = {
        # feeds.title is NOT NULL, and a discovered candidate's title can be None
        # (an Atom feed with an empty <title>), so fall back to something we
        # computed ourselves rather than to the remote text.
        "title": candidate.get("title") or candidate.get("source_host") or feed_url,
        "url": feed_url,
        "website_url": sanitize_http_url(candidate.get("website_url")),
        "category": category,
        "tags": tags or [],
        # Metadata only. The existing refresh worker picks this up immediately and
        # does the first article fetch — promotion must never fetch inline.
        "next_fetch_at": _now(),
    }
    # Still an upsert rather than an insert: two cycles could race on the same
    # URL, and losing that race should link rather than raise. The pre-check
    # above is what protects an already-curated row.
    result = db.table("feeds").upsert(feed_data, on_conflict="url").execute()
    if not result.data:
        return None
    feed = result.data[0]

    _mark_imported(db, candidate["id"], feed["id"])
    return feed


def approve_candidate(
    db: "Client",
    candidate_id: str,
    category: str | None = None,
    tags: list[str] | None = None,
) -> tuple[dict | None, str]:
    """Approve and promote in one step. Returns (feed_row, outcome).

    Outcome is one of "imported", "not_found", "already_rejected", "failed" — the
    router maps those to status codes.
    """
    candidate = get_candidate(db, candidate_id)
    if not candidate:
        return (None, "not_found")
    if candidate.get("status") == "rejected":
        # Approving something previously rejected has to be explicit; silently
        # reviving it would undo the one guarantee this queue makes.
        return (None, "already_rejected")

    # Record the approval — including what the reviewer chose — before writing
    # feeds. If the feeds write fails, the whole decision survives and
    # promote_approved() reproduces it next cycle rather than importing the feed
    # uncategorised.
    db.table("discovery_candidates").update({
        "status": "approved",
        "reviewed_at": _now(),
        "approved_category": category,
        "approved_tags": tags or [],
    }).eq("id", str(candidate_id)).execute()

    candidate["approved_category"] = category
    candidate["approved_tags"] = tags or []
    feed = promote_candidate(db, candidate, category, tags)
    return (feed, "imported" if feed else "failed")


def promote_approved(db: "Client", limit: int = 50) -> list[dict]:
    """Sweep candidates left in 'approved' — i.e. approvals whose feeds write
    didn't land — into `feeds`."""
    rows = (
        db.table("discovery_candidates")
        .select("*")
        .eq("status", "approved")
        .limit(limit)
        .execute()
    )
    promoted = []
    for candidate in list(rows.data or []):
        feed = promote_candidate(db, candidate)
        if feed:
            promoted.append(feed)
    return promoted


def auto_promote_due(db: "Client", limit: int = 50) -> list[dict]:
    """Import candidates backed by enough distinct referring feeds, without review.

    Off unless FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS is set: 0 means every
    candidate waits for a human. Migration 006's trigger keeps
    referring_feed_count live on pending rows, so this threshold reacts to
    evidence that accumulated after discovery rather than to a stale snapshot.
    """
    threshold = auto_promote_min_referrers()
    if threshold <= 0:
        return []

    rows = (
        db.table("discovery_candidates")
        .select("*")
        .eq("status", "pending")
        .gte("referring_feed_count", threshold)
        .order("referring_feed_count", desc=True)
        .limit(limit)
        .execute()
    )
    promoted = []
    for candidate in list(rows.data or []):
        feed = promote_candidate(db, candidate)
        if feed:
            promoted.append(feed)
            logger.info(
                "Auto-promoted %s (%s referring feeds)",
                candidate.get("feed_url"),
                candidate.get("referring_feed_count"),
            )
    return promoted


def stats(db: "Client") -> dict[str, int]:
    """Counts per status, for the admin overview."""

    def count(table: str, column: str, value) -> int:
        result = (
            db.table(table)
            .select("id", count="exact", head=True)
            .eq(column, value)
            .execute()
        )
        return getattr(result, "count", None) or 0

    return {
        "targets_pending": count("discovery_targets", "status", "pending"),
        "targets_done": count("discovery_targets", "status", "done"),
        "targets_blocked": count("discovery_targets", "status", "blocked"),
        "targets_exhausted": count("discovery_targets", "status", "exhausted"),
        "targets_rejected": count("discovery_targets", "status", "rejected"),
        "candidates_pending": count("discovery_candidates", "status", "pending"),
        "candidates_held": count("discovery_candidates", "status", "held"),
        "candidates_approved": count("discovery_candidates", "status", "approved"),
        "candidates_rejected": count("discovery_candidates", "status", "rejected"),
        "candidates_imported": count("discovery_candidates", "status", "imported"),
        "sources_enabled": count("discovery_sources", "enabled", True),
    }
