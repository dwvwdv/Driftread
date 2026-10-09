from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from supabase import Client

from auth import AuthUser, get_current_user
from database import get_client
from services.consumption import daily_digest, rss_remix

router = APIRouter(prefix="/me", tags=["consumption"])


@router.get("/digest")
async def digest(response: Response, day: date | None = Query(None, alias="date"),
                 timezone: str = Query("UTC", max_length=100),
                 limit: int = Query(100, ge=1, le=100),
                 user: AuthUser = Depends(get_current_user),
                 db: Client = Depends(get_client)) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"
    return daily_digest(db, user.id, day, timezone, limit)


@router.get("/rss")
async def rss(limit: int = Query(50, ge=1, le=100),
              user: AuthUser = Depends(get_current_user),
              db: Client = Depends(get_client)) -> Response:
    return Response(rss_remix(db, user.id, limit), media_type="application/rss+xml",
                    headers={"Cache-Control": "private, no-store", "Vary": "Authorization"})
