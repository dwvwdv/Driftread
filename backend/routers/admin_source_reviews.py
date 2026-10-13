"""Management-session source review reads and explicit human resolutions."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool
from supabase import Client

from database import get_client
from routers.admin import require_api_key
from services import source_review
from services.ai_runtime import ai_usage_summary

router = APIRouter(prefix="/admin/source-reviews", tags=["admin", "source-reviews"],
                   dependencies=[Depends(require_api_key)])


class ResolveReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "reject", "defer"]


async def _call(operation, *args, **kwargs):
    try:
        return await run_in_threadpool(operation, *args, **kwargs)
    except source_review.ReviewError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.get("")
async def list_source_reviews(candidate_id: UUID | None = None,
                              limit: int = Query(50, ge=1, le=100),
                              db: Client = Depends(get_client)) -> dict:
    items = await _call(source_review.list_reviews, db,
                        candidate_id=str(candidate_id) if candidate_id else None, limit=limit)
    return {"items": items}


@router.post("/candidates/{candidate_id}/request")
async def request_source_review(candidate_id: UUID, db: Client = Depends(get_client)) -> dict:
    # Preparation and any network work belong to the worker, never this request.
    return await _call(source_review.request_review, db, str(candidate_id))


@router.get("/usage")
async def source_review_usage(limit: int = Query(50, ge=1, le=100),
                              db: Client = Depends(get_client)) -> dict:
    """Read bounded provider receipts and budget status without source payloads."""
    return await ai_usage_summary(db, limit=limit)


@router.get("/{review_id}")
async def source_review_packet(review_id: UUID, db: Client = Depends(get_client)) -> dict:
    return await _call(source_review.get_review_packet, db, str(review_id))


@router.post("/{review_id}/resolve")
async def resolve_source_review(review_id: UUID, body: ResolveReviewRequest,
                                db: Client = Depends(get_client)) -> dict:
    return await _call(source_review.resolve_review, db, str(review_id), body.decision)


@router.post("/{review_id}/retry")
async def retry_source_review(review_id: UUID, db: Client = Depends(get_client)) -> dict:
    packet = await _call(source_review.get_review_packet, db, str(review_id))
    return await _call(source_review.request_review, db, packet["candidate_id"], retry=True)
