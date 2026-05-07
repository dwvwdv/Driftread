from __future__ import annotations
import random

from fastapi import APIRouter, Depends, Query
from supabase import Client

from database import get_client
from models import Feed

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=list[Feed])
async def get_recommendations(
    liked: list[str] = Query(default=[]),
    disliked: list[str] = Query(default=[]),
    limit: int = Query(10, ge=1, le=50),
    db: Client = Depends(get_client),
) -> list[Feed]:
    excluded = set(liked) | set(disliked)

    if liked:
        liked_result = (
            db.table("feeds")
            .select("category,tags")
            .in_("id", liked)
            .execute()
        )
        categories: set[str] = set()
        tags: set[str] = set()
        for row in liked_result.data:
            if row.get("category"):
                categories.add(row["category"])
            for t in row.get("tags") or []:
                tags.add(t)

        query = db.table("feeds").select("*").is_("archived_at", "null")
        if excluded:
            query = query.not_.in_("id", list(excluded))

        if categories:
            category_filter = ",".join(f"category.eq.{c}" for c in categories)
            query = query.or_(category_filter)

        result = query.limit(limit * 3).execute()
        candidates: list[dict] = result.data

        if tags:
            scored = []
            for row in candidates:
                row_tags = set(row.get("tags") or [])
                score = len(row_tags & tags)
                scored.append((score, row))
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates = [row for _, row in scored]

        top = candidates[:limit]
        return [Feed(**row) for row in top]

    result = (
        db.table("feeds")
        .select("*")
        .is_("archived_at", "null")
        .not_.in_("id", list(excluded) if excluded else ["00000000-0000-0000-0000-000000000000"])
        .limit(limit * 3)
        .execute()
    )
    pool = result.data
    random.shuffle(pool)
    return [Feed(**row) for row in pool[:limit]]
