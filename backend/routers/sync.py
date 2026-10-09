"""User-bound invalidation cursors; changed pages replace the whole bounded cache."""
import base64
import json
from typing import Literal

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from supabase import Client
from pydantic import BaseModel, ConfigDict, StrictBool

from auth import AuthUser, get_current_user
from database import get_client

router = APIRouter(prefix="/me", tags=["sync"])


class ReplayOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    article_id: UUID
    kind: Literal["read", "favorite", "read_later"]
    enabled: StrictBool


@router.post("/sync/operations", status_code=204)
async def replay_operation(
    body: ReplayOperation,
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> None:
    applied = db.rpc("replay_personal_article_state", {
        "p_user_id": user.id, "p_article_id": str(body.article_id),
        "p_kind": body.kind, "p_enabled": body.enabled,
    }).execute().data
    if applied == "retry":
        raise HTTPException(503, "Reading state is busy; retry later", headers={"Retry-After": "1"})
    if applied != "applied":
        raise HTTPException(404, "Article is no longer available")


def sync_cursor(user_id: str, sequence: int) -> str:
    return base64.urlsafe_b64encode(json.dumps(
        {"v":1,"user":user_id,"sequence":sequence}, separators=(",",":")
    ).encode()).decode().rstrip("=")


def decode_sync_cursor(cursor: str, user_id: str) -> int:
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        seq = value["sequence"]
        if value["v"] != 1 or value["user"] != user_id or type(seq) is not int or not 0 <= seq <= 9223372036854775807:
            raise ValueError
        return seq
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Invalid sync cursor") from exc


@router.get("/sync")
async def sync(response: Response, cursor: str | None = Query(None, max_length=500),
               pending_ids: list[UUID] = Query(default=[], max_length=100),
               user: AuthUser = Depends(get_current_user),
               db: Client = Depends(get_client)) -> dict:
    since = decode_sync_cursor(cursor, user.id) if cursor else None
    data = db.rpc("personal_sync_snapshot", {"p_user_id":user.id,"p_since":since,"p_pending_ids":[str(x) for x in pending_ids]}).execute().data
    # PostgREST jsonb scalar responses are dicts, not row arrays.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"
    return {**data,"account_id":user.id,"cursor":sync_cursor(user.id,data["sequence"])}
