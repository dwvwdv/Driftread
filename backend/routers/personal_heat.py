from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from supabase import Client

from auth import AuthUser, get_current_user
from database import get_client
from models import PaginatedStream
from rate_limit import rate_limit
from routers.recommendations import Signals, _load_signals
from services.personal_heat import CANDIDATE_LIMIT, rank_personal_heat

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
            "personal_heat_snapshot",
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
