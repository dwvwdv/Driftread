from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from database import get_client
from models import (
    AddSourcesRequest,
    ApproveCandidateRequest,
    DiscoveryCycleSummary,
    DiscoverySource,
    DiscoveryStats,
    DiscoveryTarget,
    Feed,
    FeedCandidate,
    PaginatedDiscoveryTargets,
    PaginatedFeedCandidates,
    RejectCandidateRequest,
    SeedTargetsRequest,
    SeedTargetsResult,
    UpdateSourceRequest,
)
from routers.admin import require_api_key
from services.directory_sources import load_default_sources
from services.discovery import run_cycle
from services.discovery_candidates import (
    approve_candidate,
    block_host,
    list_candidates,
    reject_candidate,
    stats,
)
from services.discovery_config import discovery_enabled
from services.feed_discovery import DiscoveryError, validate_fetch_url
from services.link_harvest import is_denied_host, normalize_host, origin_of

router = APIRouter(prefix="/admin/discovery", tags=["admin", "discovery"])

# Terminal statuses a seed request may reset back to 'pending'. 'rejected' is
# deliberately absent: an admin blocked that host, and re-seeding must not be a
# back door around the block.
_REQUEUEABLE = ("done", "exhausted", "blocked")


def _row_or_none(result) -> dict | None:
    if not result or not getattr(result, "data", None):
        return None
    return result.data


def _require_enabled() -> None:
    """FEED_DISCOVERY_ENABLED gates this endpoint, not just the worker.

    Deliberately unlike /admin/feeds/refresh-due, where the flag is worker-only:
    refreshing feeds we already own on demand is fine while the scheduler is off.
    But this loop reaches out to third parties, so "disabled" has to mean
    disabled — otherwise it's a claim an operator can't stand behind when a site
    owner writes in.
    """
    if not discovery_enabled():
        raise HTTPException(
            status_code=503,
            detail="Autonomous discovery is disabled (FEED_DISCOVERY_ENABLED=false)",
        )


# ── frontier ─────────────────────────────────────────────────────────────────

@router.post(
    "/targets",
    response_model=SeedTargetsResult,
    dependencies=[Depends(require_api_key)],
)
async def seed_targets(
    body: SeedTargetsRequest,
    db: Client = Depends(get_client),
) -> SeedTargetsResult:
    """Push site URLs straight into the frontier, to bootstrap an empty catalog.

    A URL that fails validation is reported in `rejected` rather than failing the
    whole batch — the same shape routers/opml.py::import_opml uses for `failed`.
    """
    accepted = requeued = skipped = 0
    rejected: list[str] = []

    for raw in body.urls:
        try:
            safe_url = await validate_fetch_url(raw)
        except DiscoveryError as e:
            rejected.append(f"{raw}: {e}")
            continue

        host = normalize_host(safe_url)
        if not host:
            rejected.append(f"{raw}: unusable host")
            continue
        if is_denied_host(host):
            rejected.append(f"{raw}: host is on the discovery denylist")
            continue

        existing = _row_or_none(
            db.table("discovery_targets")
            .select("id,status")
            .eq("host", host)
            .maybe_single()
            .execute()
        )
        if existing is None:
            # The admin's own scheme and authority, not a rebuilt
            # https://<normalized host>/ — they may have deliberately given a
            # www. or http:// address because that's the one that works.
            db.table("discovery_targets").insert({
                "url": origin_of(safe_url) or f"https://{host}/",
                "host": host,
                "source": "seed",
            }).execute()
            accepted += 1
        elif existing["status"] in _REQUEUEABLE:
            db.table("discovery_targets").update({
                "status": "pending",
                "attempts": 0,
                "last_failure_reason": None,
            }).eq("id", str(existing["id"])).execute()
            requeued += 1
        else:
            # Already pending, or rejected by an admin — either way, nothing to do.
            skipped += 1

    return SeedTargetsResult(
        accepted=accepted, requeued=requeued, skipped=skipped, rejected=rejected
    )


@router.get(
    "/targets",
    response_model=PaginatedDiscoveryTargets,
    dependencies=[Depends(require_api_key)],
)
async def list_targets(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Client = Depends(get_client),
) -> PaginatedDiscoveryTargets:
    query = db.table("discovery_targets").select("*", count="exact")
    if status:
        query = query.eq("status", status)
    offset = (page - 1) * page_size
    result = (
        query.order("referring_feed_count", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    return PaginatedDiscoveryTargets(
        items=[DiscoveryTarget(**row) for row in result.data or []],
        total=getattr(result, "count", None) or 0,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/targets/{target_id}/block",
    response_model=DiscoveryTarget,
    dependencies=[Depends(require_api_key)],
)
async def block_target(
    target_id: UUID,
    db: Client = Depends(get_client),
) -> DiscoveryTarget:
    """Permanently keep a host out of the frontier.

    Blocks the whole host, not just this row: discovery_targets is unique on url,
    so one host can hold several rows and leaving the siblings pending would mean
    still contacting a host the admin explicitly blocked. link_harvest skips any
    host already present, so the rows staying put is what makes the block stick.
    """
    existing = _row_or_none(
        db.table("discovery_targets")
        .select("id,host")
        .eq("id", str(target_id))
        .maybe_single()
        .execute()
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Target not found")

    block_host(db, existing["host"])

    refreshed = _row_or_none(
        db.table("discovery_targets")
        .select("*")
        .eq("id", str(target_id))
        .maybe_single()
        .execute()
    )
    return DiscoveryTarget(**refreshed)


# ── candidates ───────────────────────────────────────────────────────────────

@router.get(
    "/candidates",
    response_model=PaginatedFeedCandidates,
    dependencies=[Depends(require_api_key)],
)
async def list_pending_candidates(
    status: str | None = "pending",
    min_referrers: int = Query(0, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Client = Depends(get_client),
) -> PaginatedFeedCandidates:
    items, total = list_candidates(db, status, min_referrers, page, page_size)
    return PaginatedFeedCandidates(
        items=[FeedCandidate(**row) for row in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/candidates/{candidate_id}/approve",
    response_model=Feed,
    dependencies=[Depends(require_api_key)],
)
async def approve(
    candidate_id: UUID,
    body: ApproveCandidateRequest,
    db: Client = Depends(get_client),
) -> Feed:
    """Import a candidate into the catalog.

    Writes metadata only, with next_fetch_at = now(); the existing refresh worker
    does the first article fetch.
    """
    feed, outcome = approve_candidate(db, str(candidate_id), body.category, body.tags)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Candidate not found")
    if outcome == "already_rejected":
        raise HTTPException(
            status_code=409,
            detail="Candidate was rejected; re-approving must be done deliberately",
        )
    if not feed:
        raise HTTPException(status_code=500, detail="Failed to create feed")
    return Feed(**feed)


@router.post(
    "/candidates/{candidate_id}/reject",
    response_model=FeedCandidate,
    dependencies=[Depends(require_api_key)],
)
async def reject(
    candidate_id: UUID,
    body: RejectCandidateRequest,
    db: Client = Depends(get_client),
) -> FeedCandidate:
    row = reject_candidate(db, str(candidate_id), body.note, body.block_host)
    if not row:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return FeedCandidate(**row)


# ── directory sources ────────────────────────────────────────────────────────

@router.get(
    "/sources",
    response_model=list[DiscoverySource],
    dependencies=[Depends(require_api_key)],
)
async def list_sources(
    db: Client = Depends(get_client),
) -> list[DiscoverySource]:
    result = (
        db.table("discovery_sources").select("*").order("created_at").execute()
    )
    return [DiscoverySource(**row) for row in result.data or []]


@router.post(
    "/sources",
    response_model=list[DiscoverySource],
    dependencies=[Depends(require_api_key)],
)
async def add_sources(
    body: AddSourcesRequest,
    db: Client = Depends(get_client),
) -> list[DiscoverySource]:
    payload = []
    for item in body.items:
        try:
            safe_url = await validate_fetch_url(item.url)
        except DiscoveryError as e:
            raise HTTPException(status_code=400, detail=f"{item.url}: {e}")
        payload.append({"url": safe_url, "kind": item.kind, "label": item.label})

    result = db.table("discovery_sources").upsert(payload, on_conflict="url").execute()
    return [DiscoverySource(**row) for row in result.data or []]


@router.patch(
    "/sources/{source_id}",
    response_model=DiscoverySource,
    dependencies=[Depends(require_api_key)],
)
async def update_source(
    source_id: UUID,
    body: UpdateSourceRequest,
    db: Client = Depends(get_client),
) -> DiscoverySource:
    update = body.model_dump(exclude_none=True)
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")

    existing = _row_or_none(
        db.table("discovery_sources")
        .select("id")
        .eq("id", str(source_id))
        .maybe_single()
        .execute()
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Source not found")

    updated = (
        db.table("discovery_sources")
        .update(update)
        .eq("id", str(source_id))
        .execute()
    )
    return DiscoverySource(**updated.data[0])


@router.post(
    "/sources/reload-defaults",
    response_model=dict,
    dependencies=[Depends(require_api_key)],
)
async def reload_default_sources(db: Client = Depends(get_client)) -> dict:
    """Re-seed from backend/seeds/discovery_sources.json. Idempotent, and never
    resets an operator's enabled flag or interval."""
    return {"loaded": load_default_sources(db)}


# ── running a cycle by hand ──────────────────────────────────────────────────

@router.post(
    "/run",
    response_model=DiscoveryCycleSummary,
    dependencies=[Depends(require_api_key)],
)
async def run_discovery_cycle(
    harvest_limit: int | None = Query(None, ge=1, le=100),
    probe_limit: int | None = Query(None, ge=1, le=200),
    max_concurrency: int | None = Query(None, ge=1, le=10),
    directory_limit: int | None = Query(None, ge=1, le=20),
    db: Client = Depends(get_client),
) -> DiscoveryCycleSummary:
    """Run one discovery cycle now.

    The worker does this on a timer; this exists to kick a cycle by hand and to
    let a deployment without the worker drive discovery from an external
    scheduler — the same relationship /admin/feeds/refresh-due has with the
    refresh loop. Omitted bounds fall back to the FEED_DISCOVERY_* env values, so
    endpoint and worker behave identically unless deliberately overridden.
    """
    _require_enabled()
    summary = await run_cycle(
        db,
        harvest_limit=harvest_limit,
        probe_limit=probe_limit,
        max_concurrency=max_concurrency,
        directory_limit=directory_limit,
    )
    return DiscoveryCycleSummary(
        directory=summary.directory,
        harvest=summary.harvest,
        probe=summary.probe,
        auto_promoted=summary.auto_promoted,
        imported=summary.imported,
    )


@router.get(
    "/stats", response_model=DiscoveryStats, dependencies=[Depends(require_api_key)]
)
async def discovery_stats(db: Client = Depends(get_client)) -> DiscoveryStats:
    return DiscoveryStats(**stats(db))
