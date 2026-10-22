"""Operational state is accessible only through the existing admin API key."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from database import get_client
from routers.admin import require_api_key
from services.ingest_activity import IngestActivity, MAX_LIMIT, WINDOW_HOURS, ingest_activity, window_error
from services.operations import operations_status

router = APIRouter(prefix="/admin/operations", tags=["admin"])


@router.get("", dependencies=[Depends(require_api_key)])
async def get_operations(
    limit: int = Query(20, ge=1, le=100),
    db: Client = Depends(get_client),
) -> dict:
    return await asyncio.to_thread(operations_status, db, limit)


@router.get("/ingest", response_model=IngestActivity, dependencies=[Depends(require_api_key)])
async def get_ingest_activity(
    limit: int = Query(10, ge=1, le=MAX_LIMIT),
    hours: int = Query(WINDOW_HOURS),
    db: Client = Depends(get_client),
) -> IngestActivity:
    """Last 24 hours or 7 days of per-source ingest and revision rates; display only."""
    if error := window_error(hours):
        raise HTTPException(status_code=422, detail=error)
    return await ingest_activity(db, limit, hours)
