"""Password + fixed-recipient Telegram OTP for the browser management console.

All state changes are committed under the owner's row lock. Wrong attempts must
commit too; raising an HTTP error inside the transaction would undo the limit.
The direct DATABASE_URL connection already used for migrations keeps Vault and
OTP state inaccessible to every Data API role, including service_role.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import secrets
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import HTTPException

from auth import AuthUser, get_current_user

logger = logging.getLogger(__name__)
OTP_SECONDS = 300
SESSION_SECONDS = 8 * 60 * 60


def owner(user: AuthUser) -> tuple[str, str]:
    configured = os.getenv("MANAGE_OWNER_USER_ID", "").strip()
    if not configured:
        raise HTTPException(503, "後台管理員尚未設定")
    try:
        if UUID(configured) != UUID(user.id):
            raise HTTPException(403, "此帳號無後台權限")
        return str(UUID(user.id)), str(UUID(user.session_id or ""))
    except (ValueError, TypeError):
        raise HTTPException(403, "此帳號無後台權限") from None


@contextmanager
def connection():
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise HTTPException(503, "後台驗證服務尚未設定")
    conn = None
    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute("SET LOCAL lock_timeout = '3s'")
                yield cur
    except psycopg2.Error:
        # DB diagnostics may contain query parameters (OTP / token / Vault).
        logger.error("Management authentication database operation failed")
        raise HTTPException(503, "後台驗證服務暫時無法使用") from None
    finally:
        if conn is not None:
            conn.close()


def active_auth_session(cur, uid, sid):
    cur.execute("""SELECT 1 FROM auth.sessions s JOIN auth.users u ON u.id=s.user_id
        WHERE s.id=%s AND s.user_id=%s AND u.is_anonymous IS FALSE
          AND (u.banned_until IS NULL OR u.banned_until < now())
          AND (s.not_after IS NULL OR s.not_after > now())""", (sid, uid))
    if not cur.fetchone():
        raise HTTPException(401, "登入已失效，請重新登入")


def issue(user: AuthUser):
    uid, sid = owner(user)
    now = datetime.now(timezone.utc)
    # Require an actual recent password login, not OAuth, magic link, or refresh
    # of an old password session. amr comes from the verified JWT, not metadata.
    if not any(isinstance(a, dict) and a.get("method") == "password"
               and isinstance(a.get("timestamp"), (int, float))
               and 0 <= now.timestamp() - a["timestamp"] <= OTP_SECONDS
               for a in user.amr):
        raise HTTPException(401, "請先重新使用帳號密碼登入")
    challenge = str(uuid4())
    code = f"{secrets.randbelow(1_000_000):06d}"
    with connection() as cur:
        active_auth_session(cur, uid, sid)
        cur.execute("INSERT INTO driftread.manage_login_state(user_id) VALUES(%s) ON CONFLICT DO NOTHING", (uid,))
        cur.execute("SELECT * FROM driftread.manage_login_state WHERE user_id=%s FOR UPDATE", (uid,))
        state = cur.fetchone()
        until = state["locked_until"]
        if until and until > now:
            raise HTTPException(429, "驗證錯誤過多，請稍後再試", headers={"Retry-After": str(max(1, int((until-now).total_seconds())))})
        last = state["last_sent_at"]
        if last and (now-last).total_seconds() < 60:
            raise HTTPException(429, "請稍候再重送驗證碼", headers={"Retry-After": str(max(1, int(60-(now-last).total_seconds())))})
        # Use fixed Vault names from seamless_track. Secrets never enter Python.
        cur.execute("""SELECT net.http_post(
            url := 'https://api.telegram.org/bot' ||
                (SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name='telegram_bot_token') || '/sendMessage',
            body := jsonb_build_object(
                'chat_id', (SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name='telegram_chat_id'),
                'text', %s),
            headers := '{"Content-Type":"application/json"}'::jsonb,
            timeout_milliseconds := 5000
        ) AS request_id WHERE
            EXISTS(SELECT 1 FROM vault.decrypted_secrets WHERE name='telegram_bot_token' AND length(decrypted_secret)>0)
            AND EXISTS(SELECT 1 FROM vault.decrypted_secrets WHERE name='telegram_chat_id' AND decrypted_secret ~ '^[1-9][0-9]*$')""",
            (f"Driftread 後台登入驗證碼：{code}\n5 分鐘內有效。若非本人操作，請勿使用此驗證碼。",))
        request = cur.fetchone()
        if not request or request["request_id"] is None:
            raise HTTPException(503, "Telegram 驗證服務尚未設定")
        # Wrong attempts carry over to the new code; only an expired lock (the
        # check above already rejected an active one) or a success resets them.
        # Resetting here would let 4 guesses per resend bypass the lockout.
        cur.execute("""UPDATE driftread.manage_login_state SET challenge_id=%s,auth_session_id=%s,
            code_hash=extensions.crypt(%s,extensions.gen_salt('bf',8)),
            expires_at=%s,last_sent_at=%s,
            attempts=CASE WHEN locked_until IS NULL THEN attempts ELSE 0 END,
            locked_until=NULL,telegram_request_id=%s
            WHERE user_id=%s""", (challenge, sid, code, now+timedelta(seconds=OTP_SECONDS), now, request["request_id"], uid))
        cur.execute("DELETE FROM driftread.manage_sessions WHERE expires_at <= now()")
    return {"challenge_id": challenge, "expires_in": OTP_SECONDS, "retry_after": 60}


def verify(user: AuthUser, challenge: UUID, code: str):
    uid, sid = owner(user)
    now = datetime.now(timezone.utc)
    failure = None
    token = secrets.token_urlsafe(32)
    expires = now+timedelta(seconds=SESSION_SECONDS)
    with connection() as cur:
        active_auth_session(cur, uid, sid)
        cur.execute("SELECT * FROM driftread.manage_login_state WHERE user_id=%s FOR UPDATE", (uid,))
        state = cur.fetchone()
        if (not state or str(state["challenge_id"]) != str(challenge)
                or str(state["auth_session_id"]) != sid or not state["code_hash"]
                or state["expires_at"] <= now or state["attempts"] >= 5):
            raise HTTPException(403, "驗證碼無效或已過期，請重新登入")
        cur.execute("SELECT %s = extensions.crypt(%s,%s) AS matches", (state["code_hash"], code, state["code_hash"]))
        if not cur.fetchone()["matches"]:
            cur.execute("""UPDATE driftread.manage_login_state SET attempts=attempts+1,
                locked_until=CASE WHEN attempts+1>=5 THEN now()+interval '15 minutes' ELSE locked_until END
                WHERE user_id=%s""", (uid,))
            failure = HTTPException(403, "驗證碼錯誤")
        else:
            # A queued request is not proof of delivery. Fail closed until the
            # fixed Telegram API responds with HTTP 200 and {ok:true}.
            cur.execute("SELECT status_code,content FROM net._http_response WHERE id=%s", (state["telegram_request_id"],))
            response = cur.fetchone()
            try:
                delivered = response and response["status_code"] == 200 and json.loads(response["content"]).get("ok") is True
            except (TypeError, ValueError, AttributeError):
                delivered = False
            if not delivered:
                raise HTTPException(503, "Telegram 驗證碼尚未送達，請稍後再試或重送")
            # Consume under the same row lock as issuing the elevated session.
            cur.execute("UPDATE driftread.manage_login_state SET code_hash=NULL,attempts=0 WHERE user_id=%s", (uid,))
            cur.execute("""INSERT INTO driftread.manage_sessions(token_hash,user_id,auth_session_id,expires_at)
                VALUES(%s,%s,%s,%s)""", (hashlib.sha256(token.encode()).hexdigest(), uid, sid, expires))
    if failure:
        raise failure
    return {"token": token, "expires_at": expires.isoformat()}


def require_session(authorization: str | None, token: str | None):
    user = get_current_user(authorization)
    uid, sid = owner(user)
    if not token or len(token) > 128:
        raise HTTPException(401, "請先完成 Telegram 驗證")
    with connection() as cur:
        active_auth_session(cur, uid, sid)
        cur.execute("""SELECT 1 FROM driftread.manage_sessions
            WHERE token_hash=%s AND user_id=%s AND auth_session_id=%s AND expires_at>now()""",
            (hashlib.sha256(token.encode()).hexdigest(), uid, sid))
        if not cur.fetchone():
            raise HTTPException(401, "後台驗證已失效，請重新登入")
    return user


def revoke(user: AuthUser, token: str | None):
    uid, sid = owner(user)
    if token and len(token) <= 128:
        with connection() as cur:
            cur.execute("DELETE FROM driftread.manage_sessions WHERE token_hash=%s AND user_id=%s AND auth_session_id=%s",
                        (hashlib.sha256(token.encode()).hexdigest(), uid, sid))
