from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field

from auth import AuthUser, get_current_user
from rate_limit import rate_limit
from services import manage_auth

router = APIRouter(prefix="/manage/auth", tags=["manage"])


class VerifyRequest(BaseModel):
    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


@router.post("/request", dependencies=[Depends(rate_limit("manage_request", 5)), Depends(no_store)])
def request_otp(user: AuthUser = Depends(get_current_user)):
    return manage_auth.issue(user)


@router.post("/verify", dependencies=[Depends(rate_limit("manage_verify", 10)), Depends(no_store)])
def verify_otp(body: VerifyRequest, user: AuthUser = Depends(get_current_user)):
    return manage_auth.verify(user, body.challenge_id, body.code)


@router.post("/logout", status_code=204, dependencies=[Depends(no_store)])
def logout(user: AuthUser = Depends(get_current_user), x_manage_session: str | None = Header(default=None)):
    manage_auth.revoke(user, x_manage_session)
