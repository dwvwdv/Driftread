import os
from typing import Optional

import jwt
from fastapi import Header, HTTPException, status
from jwt import PyJWKClient, PyJWKClientError

# Supabase is migrating projects from a single HS256 shared secret to
# asymmetric JWT signing keys (ES256, with RS256 also supported by the
# platform). Older projects that haven't rotated still mint HS256 tokens, so
# both paths stay live: which one runs is decided by the token's own `alg`
# header, never by which env vars happen to be set.
_ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")

# Process-wide singleton, rebuilt only if the configured URL changes (tests
# flip SUPABASE_URL/SUPABASE_JWKS_URL between cases). PyJWKClient owns its own
# JWKS document cache and already handles rotation: cache_keys=True caches the
# fetched document for `lifespan` seconds, and a `kid` it doesn't recognise
# triggers one unconditional refetch before giving up — so a newly rotated
# Supabase signing key is picked up on the first token that uses it, not just
# after the TTL expires.
_jwks_client: Optional[PyJWKClient] = None
_jwks_client_url: Optional[str] = None


class AuthUser:
    def __init__(self, user_id: str, email: Optional[str] = None):
        self.id = user_id
        self.email = email


def _jwks_url() -> Optional[str]:
    explicit = os.getenv("SUPABASE_JWKS_URL", "").strip()
    if explicit:
        return explicit
    base = os.getenv("SUPABASE_URL", "").strip()
    if not base:
        return None
    return f"{base.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client, _jwks_client_url
    url = _jwks_url()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_URL/SUPABASE_JWKS_URL not configured",
        )
    if _jwks_client is None or _jwks_client_url != url:
        _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=300)
        _jwks_client_url = url
    return _jwks_client


def reset_jwks_client() -> None:
    """Drop the cached PyJWKClient. For tests — the cache is process-global."""
    global _jwks_client, _jwks_client_url
    _jwks_client = None
    _jwks_client_url = None


def _decode_asymmetric(token: str) -> dict:
    client = _get_jwks_client()
    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except PyJWKClientError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unable to resolve signing key: {e}",
        )
    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=list(_ASYMMETRIC_ALGORITHMS),
            audience="authenticated",
            options={"verify_aud": True},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )


def _decode_hs256(token: str) -> dict:
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET not configured",
        )
    try:
        return jwt.decode(
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


def _verify_token(token: str) -> AuthUser:
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )

    # The header's `alg` only ever selects *which* fixed verification method
    # runs — HS256 always checks against SUPABASE_JWT_SECRET, ES256/RS256
    # always check against a JWKS-derived public key — so a forged header
    # can't redirect one token onto the other's key material (the classic
    # algorithm-confusion attack).
    if alg in _ASYMMETRIC_ALGORITHMS:
        payload = _decode_asymmetric(token)
    elif alg == "HS256":
        payload = _decode_hs256(token)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unsupported token algorithm: {alg}",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
        )
    # The shared Supabase project has Anonymous Sign-Ins enabled. Anonymous
    # users receive the authenticated Postgres role, and this backend uses the
    # service-role client, so RLS cannot enforce the permanent-user-only rule
    # for API calls. Mirror the owner policies here before any privileged query
    # is allowed to run. Requiring an explicit False also keeps application
    # authorization aligned with SQL's `... IS FALSE` behavior.
    if payload.get("is_anonymous") is not False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A permanent account is required",
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
