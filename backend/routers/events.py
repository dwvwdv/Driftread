"""Explicit human Fact/Story organization; no relation inference."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from postgrest.exceptions import APIError
from supabase import Client

from database import get_client
from rate_limit import rate_limit
from routers.admin import require_api_key

router = APIRouter(tags=["manual-events"])
Relation = Literal[
    "SAME_EVENT",
    "SAME_STORY",
    "FOLLOW_UP",
    "REACTION",
    "CONTEXT",
    "SAME_TOPIC",
    "UNRELATED",
]


class EventCreate(BaseModel):
    kind: Literal["fact", "story"]
    title: str = Field(min_length=1, max_length=200)


class MemberChange(BaseModel):
    id: UUID
    excluded: bool = False


class EventChange(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    members: list[MemberChange] | None = Field(default=None, max_length=200)
    relation_target: UUID | None = None
    relation: Relation | None = None

    @model_validator(mode="after")
    def paired_relation(self):
        if (self.relation_target is None) != (self.relation is None):
            raise ValueError("relation and target are required together")
        return self


class StoryMerge(BaseModel):
    target_id: UUID
    source_version: int = Field(ge=1)
    target_version: int = Field(ge=1)


def _rpc(db: Client, name: str, params: dict):
    try:
        return db.rpc(name, params).execute().data
    except APIError as exc:
        if exc.code == "40001":
            raise HTTPException(
                409, "Version conflict; reload before retrying"
            ) from exc
        if exc.code == "P0002":
            raise HTTPException(404, "Event not found or merged") from exc
        if exc.code == "22023":
            raise HTTPException(400, "Invalid event change") from exc
        raise


@router.post("/admin/events", dependencies=[Depends(require_api_key)], status_code=201)
async def create_event(body: EventCreate, db: Client = Depends(get_client)):
    data = db.table("event_objects").insert(body.model_dump()).execute().data
    return data[0]


@router.get("/admin/events", dependencies=[Depends(require_api_key)])
async def list_events(
    kind: Literal["fact", "story"] | None = None,
    limit: int = Query(100, ge=1, le=100),
    db: Client = Depends(get_client),
):
    query = db.table("event_objects").select(
        "id,kind,title,version,merged_into,created_at,updated_at"
    )
    if kind:
        query = query.eq("kind", kind)
    return (
        query.order("updated_at", desc=True)
        .order("id", desc=True)
        .limit(limit)
        .execute()
        .data
    )


@router.get("/admin/events/{event_id}", dependencies=[Depends(require_api_key)])
async def admin_event(event_id: UUID, db: Client = Depends(get_client)):
    data = _rpc(db, "read_manual_event", {"p_id": str(event_id), "p_admin": True})
    if not data:
        raise HTTPException(404, "Event not found")
    return data


@router.patch("/admin/events/{event_id}", dependencies=[Depends(require_api_key)])
async def change_event(
    event_id: UUID, body: EventChange, db: Client = Depends(get_client)
):
    return _rpc(
        db,
        "mutate_manual_event",
        {
            "p_id": str(event_id),
            "p_expected_version": body.expected_version,
            "p_title": body.title,
            "p_members": (
                [m.model_dump(mode="json") for m in body.members]
                if body.members is not None
                else None
            ),
            "p_relation_target": (
                str(body.relation_target) if body.relation_target else None
            ),
            "p_relation": body.relation,
        },
    )


@router.post("/admin/stories/{story_id}/merge", dependencies=[Depends(require_api_key)])
async def merge_story(
    story_id: UUID, body: StoryMerge, db: Client = Depends(get_client)
):
    return _rpc(
        db,
        "merge_manual_stories",
        {
            "p_source": str(story_id),
            "p_target": str(body.target_id),
            "p_source_version": body.source_version,
            "p_target_version": body.target_version,
        },
    )


@router.get("/events/{event_id}", dependencies=[Depends(rate_limit("manual-events"))])
async def read_event(event_id: UUID, db: Client = Depends(get_client)):
    data = _rpc(db, "read_manual_event", {"p_id": str(event_id), "p_admin": False})
    if not data:
        raise HTTPException(404, "Event not found")
    return data
