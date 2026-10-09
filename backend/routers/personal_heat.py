from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from auth import AuthUser, get_current_user
from database import get_client
from models import PaginatedStream
from rate_limit import rate_limit
from routers.recommendations import Signals, _load_signals
from services.personal_heat import CANDIDATE_LIMIT, _time, rank_personal_heat

router = APIRouter(prefix="/me", tags=["personal-heat"])


@router.get(
    "/personal-heat",
    response_model=PaginatedStream,
    dependencies=[Depends(rate_limit("personal-heat"))],
)
async def personal_heat(
    feed_id: UUID | None = None,
    unread_only: bool = False,
    limit: int = Query(100, ge=1, le=100),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
):
    at = datetime.now(timezone.utc)
    snapshot = (
        db.rpc(
            "capture_personal_heat",
            {
                "p_user_id": user.id,
                "p_at": at.isoformat(),
                "p_feed_id": str(feed_id) if feed_id else None,
                "p_unread_only": unread_only,
                "p_limit": CANDIDATE_LIMIT,
            },
        )
        .execute()
        .data
    )
    signals = Signals()
    _load_signals(db, user.id, signals)
    return rank_personal_heat(snapshot, signals, at, limit)


@router.get("/personal-heat/history", dependencies=[Depends(rate_limit("heat-history"))])
async def heat_history(
    limit: int = Query(20, ge=1, le=100),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    return {
        "items": (
            db.table("user_heat_snapshots")
            .select("id,snapshot_at,repaired_at,unread_only")
            .eq("user_id", user.id)
            .gte("snapshot_at", cutoff)
            .order("snapshot_at", desc=True)
            .order("id", desc=True)
            .limit(limit)
            .execute().data
        ),
        "history_window_days": 30,
        "snapshot_limit": 100,
    }


@router.post(
    "/personal-heat/history/repair", dependencies=[Depends(rate_limit("heat-repair"))]
)
async def repair_heat_history(
    limit: int = Query(20, ge=1, le=20),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
):
    return db.rpc(
        "repair_personal_heat_history", {"p_user_id": user.id, "p_limit": limit}
    ).execute().data


@router.get(
    "/personal-heat/history/{snapshot_id}",
    response_model=PaginatedStream,
    dependencies=[Depends(rate_limit("heat-history-read"))],
)
async def read_heat_history(
    snapshot_id: UUID,
    limit: int = Query(100, ge=1, le=100),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
):
    snapshot = db.rpc(
        "read_personal_heat_history", {"p_user_id": user.id, "p_id": str(snapshot_id)}
    ).execute().data
    if not snapshot:
        raise HTTPException(404, "Heat snapshot not found")
    signals = Signals()
    _load_signals(db, user.id, signals)
    return rank_personal_heat(snapshot, signals, _time(snapshot["snapshot_at"]), limit)
