from __future__ import annotations
import os
import time
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt import PyJWKClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

import auth

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-please-change-and-make-32-bytes-long")


@pytest.fixture(autouse=True)
def _reset_jwks_client():
    """auth._jwks_client is process-global, same reasoning as robots.clear_cache()."""
    auth.reset_jwks_client()
    yield
    auth.reset_jwks_client()


def _token(
    user_id: str = "user-abc",
    secret: str | None = None,
    is_anonymous: bool | None = False,
) -> str:
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "email": "u@example.com",
        "exp": int(time.time()) + 3600,
    }
    if is_anonymous is not None:
        payload["is_anonymous"] = is_anonymous
    return jwt.encode(
        payload,
        secret or os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def _asymmetric_token_and_jwks(
    algorithm: str = "ES256",
    kid: str = "test-kid-1",
    user_id: str = "user-abc",
    is_anonymous: bool | None = False,
    other_keys: list[dict] | None = None,
):
    """Build a real ES256/RS256-signed token plus the JWKS document that would
    let a verifier recover its public key — the same shape Supabase's
    /auth/v1/.well-known/jwks.json endpoint returns."""
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "email": "u@example.com",
        "exp": int(time.time()) + 3600,
    }
    if is_anonymous is not None:
        payload["is_anonymous"] = is_anonymous

    if algorithm == "ES256":
        private_key = ec.generate_private_key(ec.SECP256R1())
        jwk = ECAlgorithm(ECAlgorithm.SHA256).to_jwk(private_key.public_key(), as_dict=True)
    elif algorithm == "RS256":
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(private_key.public_key(), as_dict=True)
    else:
        raise ValueError(algorithm)

    jwk["kid"] = kid
    jwk["use"] = "sig"
    token = jwt.encode(payload, private_key, algorithm=algorithm, headers={"kid": kid})
    jwks = {"keys": [*(other_keys or []), jwk]}
    return token, jwks


def _serve_jwks(monkeypatch, jwks: dict) -> list[int]:
    """Patch PyJWKClient's network fetch to return `jwks` and count calls."""
    calls: list[int] = []

    def fake_fetch_data(self):
        calls.append(1)
        return jwks

    monkeypatch.setattr(PyJWKClient, "fetch_data", fake_fetch_data)
    return calls


def test_me_feeds_requires_auth(client):
    c, _ = client
    resp = c.get("/api/me/feeds")
    assert resp.status_code == 401


def test_me_feeds_invalid_token(client):
    c, _ = client
    resp = c.get("/api/me/feeds", headers={"Authorization": "Bearer bogus"})
    assert resp.status_code == 401


def test_me_feeds_wrong_secret(client):
    c, _ = client
    bad = _token(secret="another-different-secret-of-sufficient-length")
    resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {bad}"})
    assert resp.status_code == 401


def test_me_feeds_valid_token(client):
    c, mock_db = client
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[]
    )
    resp = c.get(
        "/api/me/feeds",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_me_feeds_rejects_anonymous_supabase_user(client):
    c, mock_db = client
    resp = c.get(
        "/api/me/feeds",
        headers={"Authorization": f"Bearer {_token(is_anonymous=True)}"},
    )

    assert resp.status_code == 403
    assert resp.json() == {"detail": "A permanent account is required"}
    mock_db.table.assert_not_called()


def test_me_feeds_rejects_token_without_permanent_user_claim(client):
    c, mock_db = client
    resp = c.get(
        "/api/me/feeds",
        headers={"Authorization": f"Bearer {_token(is_anonymous=None)}"},
    )

    assert resp.status_code == 403
    mock_db.table.assert_not_called()


def test_unsupported_algorithm_rejected(client):
    c, _ = client
    # 'none' is a real, if never wire-legal here, JWT alg — assert it doesn't
    # silently fall through either the HS256 or JWKS branch.
    token = jwt.encode(
        {"sub": "user-abc", "aud": "authenticated", "is_anonymous": False},
        "",
        algorithm="none",
    )
    resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


class TestJwksVerification:
    """ES256/RS256 tokens verified against a Supabase-style JWKS document,
    covering the TODO.md items this PR closes: moving off HS256-only
    verification, and surviving key rotation + relying on PyJWKClient's cache
    refresh rather than hand-rolled caching."""

    def test_es256_token_verified_via_jwks(self, monkeypatch, client):
        c, mock_db = client
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )
        token, jwks = _asymmetric_token_and_jwks("ES256")
        _serve_jwks(monkeypatch, jwks)

        resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_rs256_token_verified_via_jwks(self, monkeypatch, client):
        c, mock_db = client
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )
        token, jwks = _asymmetric_token_and_jwks("RS256")
        _serve_jwks(monkeypatch, jwks)

        resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_jwks_token_rejects_anonymous_user(self, monkeypatch, client):
        c, mock_db = client
        token, jwks = _asymmetric_token_and_jwks("ES256", is_anonymous=True)
        _serve_jwks(monkeypatch, jwks)

        resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        mock_db.table.assert_not_called()

    def test_jwks_token_wrong_key_rejected(self, monkeypatch, client):
        """A token signed by a key that isn't in the served JWKS at all (not
        even under a stale kid) must not verify."""
        c, _ = client
        token, _real_jwks = _asymmetric_token_and_jwks("ES256", kid="attacker-key")
        # Serve a JWKS containing only an unrelated legitimate key.
        _, other_jwks = _asymmetric_token_and_jwks("ES256", kid="real-key")
        _serve_jwks(monkeypatch, other_jwks)

        resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_jwks_missing_kid_rejected(self, monkeypatch, client):
        c, _ = client
        token, jwks = _asymmetric_token_and_jwks("ES256")
        _serve_jwks(monkeypatch, jwks)
        # Re-encode without a kid header.
        payload = jwt.decode(token, options={"verify_signature": False})
        private_key = ec.generate_private_key(ec.SECP256R1())
        no_kid_token = jwt.encode(payload, private_key, algorithm="ES256")

        resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {no_kid_token}"})
        assert resp.status_code == 401

    def test_key_rotation_triggers_one_refetch_for_unknown_kid(self, monkeypatch, client):
        """PyJWKClient's own rotation handling: a kid missing from the cached
        JWKS document forces exactly one refetch before giving up, so a newly
        rotated Supabase signing key verifies without waiting out the TTL."""
        c, mock_db = client
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )
        old_token, old_jwks = _asymmetric_token_and_jwks("ES256", kid="key-1")
        new_token, new_jwks = _asymmetric_token_and_jwks("ES256", kid="key-2")

        state = {"jwks": old_jwks}
        calls: list[int] = []

        def fake_fetch_data(self):
            calls.append(1)
            return state["jwks"]

        monkeypatch.setattr(PyJWKClient, "fetch_data", fake_fetch_data)

        resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {old_token}"})
        assert resp.status_code == 200
        assert len(calls) == 1

        # Server rotates its signing key without this process restarting.
        state["jwks"] = new_jwks

        resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {new_token}"})
        assert resp.status_code == 200
        # Cached document didn't know "key-2" -> exactly one extra refetch.
        assert len(calls) == 2

    def test_jwks_endpoint_derived_from_supabase_url(self):
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ.pop("SUPABASE_JWKS_URL", None)
        try:
            assert (
                auth._jwks_url()
                == "https://example.supabase.co/auth/v1/.well-known/jwks.json"
            )
        finally:
            os.environ.pop("SUPABASE_URL", None)

    def test_explicit_jwks_url_overrides_derived_one(self):
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_JWKS_URL"] = "https://override.example/jwks.json"
        try:
            assert auth._jwks_url() == "https://override.example/jwks.json"
        finally:
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_JWKS_URL", None)

    def test_asymmetric_token_without_supabase_url_is_500(self, client):
        c, _ = client
        token, _jwks = _asymmetric_token_and_jwks("ES256")
        saved = os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_JWKS_URL", None)
        try:
            resp = c.get("/api/me/feeds", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 500
        finally:
            if saved is not None:
                os.environ["SUPABASE_URL"] = saved
