import os
from typing import Optional

import jwt
from fastapi import Header, HTTPException, status


class AuthUser:
    def __init__(self, user_id: str, email: Optional[str] = None):
        self.id = user_id
        self.email = email


def _verify_token(token: str) -> AuthUser:
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET not configured",
        )
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_aud": True},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )
    return AuthUser(user_id=user_id, email=payload.get("email"))


def _extract_bearer(authorization):
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def get_current_user(authorization: Optional[str] = Header(default=None)) -> AuthUser:
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    return _verify_token(token)


def get_optional_user(authorization: Optional[str] = Header(default=None)):
    token = _extract_bearer(authorization)
    if not token:
        return None
    try:
        return _verify_token(token)
    except HTTPException:
        return None
