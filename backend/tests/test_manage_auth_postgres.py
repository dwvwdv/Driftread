"""Exercise real transactions, PostgreSQL crypt, row locks and restricted grants."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import time
from uuid import UUID, uuid4

from fastapi import HTTPException
import psycopg2
import pytest

from auth import AuthUser
from services import manage_auth as manage
from tests.test_postgres_lifecycle import pg_database


@pytest.fixture
def setup(pg_database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_database)
    uid, sid = str(uuid4()), str(uuid4())
    monkeypatch.setenv("MANAGE_OWNER_USER_ID", uid)
    with psycopg2.connect(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / "sql/test_manage_auth.sql").read_text())
            cur.execute("INSERT INTO auth.users(id) VALUES(%s)", (uid,))
            cur.execute("INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)", (sid, uid))
    conn.close()
    return AuthUser(uid, session_id=sid, amr=[{"method":"password","timestamp":int(time.time())}])


def query(statement, params=()):
    with manage.connection() as cur:
        cur.execute(statement, params)
        return cur.fetchall() if cur.description else None


def challenge(user):
    result = manage.issue(user)
    row = query("SELECT body,url FROM net.test_requests ORDER BY id DESC LIMIT 1")[0]
    assert row["url"] == 'https://api.telegram.org/bottest-bot/sendMessage'
    assert row["body"]["chat_id"] == '123456'
    code = row["body"]["text"].split('：')[1].split('\n')[0]
    return UUID(result["challenge_id"]), code


def test_otp_is_one_use_and_elevated_session_bound_to_auth_session(setup, monkeypatch):
    user = setup
    cid, code = challenge(user)
    result = manage.verify(user, cid, code)
    monkeypatch.setattr(manage, "get_current_user", lambda _: user)
    assert manage.require_session("Bearer signed", result["token"]).id == user.id
    assert query("SELECT token_hash FROM driftread.manage_sessions WHERE user_id=%s", (user.id,))[0]["token_hash"] != result["token"]
    with pytest.raises(HTTPException) as exc:
        manage.verify(user, cid, code)
    assert exc.value.status_code == 403
    user.session_id = str(uuid4())
    with pytest.raises(HTTPException):
        manage.require_session("Bearer signed", result["token"])


def test_wrong_attempts_commit_and_lock_survives_resend(setup):
    cid, code = challenge(setup)
    wrong = '000001' if code == '000000' else '000000'
    for _ in range(5):
        with pytest.raises(HTTPException) as exc:
            manage.verify(setup, cid, wrong)
        assert exc.value.status_code == 403
    assert query("SELECT attempts FROM driftread.manage_login_state WHERE user_id=%s", (setup.id,))[0]["attempts"] == 5
    query("UPDATE driftread.manage_login_state SET last_sent_at=now()-interval '2 minutes' WHERE user_id=%s", (setup.id,))
    with pytest.raises(HTTPException) as exc:
        manage.issue(setup)
    assert exc.value.status_code == 429
    with pytest.raises(HTTPException):
        manage.verify(setup, cid, code)


def test_resend_cooldown_and_invalidates_previous_code(setup):
    cid, code = challenge(setup)
    with pytest.raises(HTTPException) as exc:
        manage.issue(setup)
    assert exc.value.status_code == 429
    query("UPDATE driftread.manage_login_state SET last_sent_at=now()-interval '2 minutes' WHERE user_id=%s", (setup.id,))
    cid2, code2 = challenge(setup)
    assert cid2 != cid
    with pytest.raises(HTTPException):
        manage.verify(setup, cid, code)
    assert manage.verify(setup, cid2, code2)["token"]


def test_expiry_delivery_failure_and_revocation_fail_closed(setup, monkeypatch):
    cid, code = challenge(setup)
    query("UPDATE net._http_response SET content='{\"ok\":false}' WHERE id=(SELECT telegram_request_id FROM driftread.manage_login_state WHERE user_id=%s)", (setup.id,))
    with pytest.raises(HTTPException) as exc:
        manage.verify(setup, cid, code)
    assert exc.value.status_code == 503
    query("UPDATE net._http_response SET content='{\"ok\":true}' WHERE id=(SELECT telegram_request_id FROM driftread.manage_login_state WHERE user_id=%s)", (setup.id,))
    result = manage.verify(setup, cid, code)
    monkeypatch.setattr(manage, "get_current_user", lambda _: setup)
    query("UPDATE driftread.manage_sessions SET expires_at=now()-interval '1 second' WHERE user_id=%s", (setup.id,))
    with pytest.raises(HTTPException):
        manage.require_session("Bearer signed", result["token"])
    query("UPDATE driftread.manage_sessions SET expires_at=now()+interval '1 hour' WHERE user_id=%s", (setup.id,))
    manage.revoke(setup, result["token"])
    with pytest.raises(HTTPException):
        manage.require_session("Bearer signed", result["token"])


def test_otp_expiry_and_auth_session_revocation(setup, monkeypatch):
    cid, code = challenge(setup)
    query("UPDATE driftread.manage_login_state SET expires_at=now()-interval '1 second' WHERE user_id=%s", (setup.id,))
    with pytest.raises(HTTPException):
        manage.verify(setup, cid, code)
    query("UPDATE driftread.manage_login_state SET expires_at=now()+interval '1 minute' WHERE user_id=%s", (setup.id,))
    token = manage.verify(setup, cid, code)["token"]
    query("DELETE FROM auth.sessions WHERE id=%s", (setup.session_id,))
    monkeypatch.setattr(manage, "get_current_user", lambda _: setup)
    with pytest.raises(HTTPException) as exc:
        manage.require_session("Bearer signed", token)
    assert exc.value.status_code == 401


def test_parallel_verify_consumes_only_once(setup):
    cid, code = challenge(setup)
    def verify():
        try:
            return manage.verify(setup, cid, code)
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: verify(), range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert 403 in results


def test_parallel_request_only_sends_one_message(setup):
    def send():
        try:
            return manage.issue(setup)
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert 429 in results


@pytest.mark.parametrize('secret', ['telegram_chat_id', 'telegram_bot_token'])
def test_missing_vault_configuration_does_not_issue_challenge(setup, secret):
    query("DELETE FROM vault.decrypted_secrets WHERE name=%s", (secret,))
    with pytest.raises(HTTPException) as exc:
        manage.issue(setup)
    assert exc.value.status_code == 503
    assert not query("SELECT * FROM driftread.manage_login_state WHERE user_id=%s", (setup.id,))


def test_group_recipient_rejected(setup):
    query("UPDATE vault.decrypted_secrets SET decrypted_secret='-100123456' WHERE name='telegram_chat_id'")
    with pytest.raises(HTTPException) as exc:
        manage.issue(setup)
    assert exc.value.status_code == 503


def test_management_tables_not_accessible_to_data_api_roles(setup):
    for table in ['manage_login_state','manage_sessions']:
        for role in ['anon','authenticated','service_role']:
            assert not query("SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE') AS allowed", (role, f'driftread.{table}'))[0]['allowed']
        assert query("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass", (f'driftread.{table}',))[0]['relrowsecurity']
    migration = Path(__file__).parents[1]/'migrations/20261012020000_manage_telegram_auth.sql'
    query(migration.read_text())  # must also tolerate a ledger-independent rerun
