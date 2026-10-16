"""Real PostgreSQL coverage for owner-managed finite and permanent grants."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
import pytest

from auth import AuthUser
from services import manage_auth, review_oauth as oauth
from tests.test_postgres_lifecycle import pg_database
from tests.test_review_oauth_postgres import exchange, query, refresh, setup


def grant_id(tokens):
    return oauth.validate_access_token(tokens['access_token'])['grant_id']


def update(setup, gid, expires_at):
    return oauth.update_grant_expiry(gid, expires_at, setup[0], setup[1])


def token_row(token):
    return query('SELECT * FROM driftread.review_oauth_tokens WHERE token_hash=%s',
                 (oauth._digest(token),))[0]


def test_permanent_grant_keeps_short_access_and_rotates_refresh_after_original_expiry(setup, monkeypatch):
    tokens = exchange(setup)
    gid = grant_id(tokens)
    result = update(setup, gid, None)
    assert result['expires_at'] is None
    assert token_row(tokens['refresh_token'])['expires_at'] is None
    assert token_row(tokens['access_token'])['expires_at'] is not None
    future = datetime.now(timezone.utc) + timedelta(days=365)
    monkeypatch.setattr(oauth, '_now', lambda: future)
    # A permanent connection still expires every access credential after 900s.
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(tokens['access_token'])
    rotated = refresh(setup, tokens['refresh_token'])
    assert rotated['expires_in'] == 900
    assert rotated['refresh_token'] != tokens['refresh_token']
    assert token_row(rotated['access_token'])['expires_at'] == future + timedelta(seconds=900)
    assert token_row(rotated['refresh_token'])['expires_at'] is None
    assert oauth.validate_access_token(rotated['access_token'])['grant_id'] == gid


def test_extension_updates_live_refresh_and_works_past_original_thirty_days(setup, monkeypatch):
    original = exchange(setup)
    gid = grant_id(original)
    current = refresh(setup, original['refresh_token'])
    old_consumed_expiry = token_row(original['refresh_token'])['expires_at']
    expiry = datetime.now(timezone.utc) + timedelta(days=90)
    result = update(setup, gid, expiry)
    assert datetime.fromisoformat(result['expires_at']) == expiry
    assert token_row(current['refresh_token'])['expires_at'] == expiry
    assert token_row(original['refresh_token'])['expires_at'] == old_consumed_expiry
    future = datetime.now(timezone.utc) + timedelta(days=60)
    monkeypatch.setattr(oauth, '_now', lambda: future)
    rotated = refresh(setup, current['refresh_token'])
    assert rotated['expires_in'] == 900
    assert token_row(rotated['refresh_token'])['expires_at'] == expiry
    assert oauth.validate_access_token(rotated['access_token'])['grant_id'] == gid
    assert set(result) == {'id', 'client_name', 'scope', 'created_at', 'expires_at', 'revoked_at', 'last_used_at'}
    serialized = json.dumps(result)
    for value in (original['access_token'], original['refresh_token'], current['access_token'],
                  current['refresh_token'], oauth._digest(current['refresh_token'])):
        assert value not in serialized


@pytest.mark.parametrize('permanent_first', [False, True])
def test_shortening_grant_invalidates_unexpired_access_and_updates_refresh(setup, monkeypatch, permanent_first):
    tokens = exchange(setup)
    gid = grant_id(tokens)
    if permanent_first:
        update(setup, gid, None)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    update(setup, gid, expiry)
    assert token_row(tokens['refresh_token'])['expires_at'] == expiry
    assert token_row(tokens['access_token'])['expires_at'] > expiry + timedelta(seconds=1)
    assert oauth.validate_access_token(tokens['access_token'])
    monkeypatch.setattr(oauth, '_now', lambda: expiry + timedelta(seconds=1))
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(tokens['access_token'])
    with pytest.raises(oauth.OAuthError):
        refresh(setup, tokens['refresh_token'])


@pytest.mark.parametrize('action', ['browser_revoke', 'token_revoke', 'logout', 'replay'])
def test_permanent_grants_still_obey_revocation_logout_and_replay(setup, action):
    tokens = exchange(setup)
    gid = grant_id(tokens)
    update(setup, gid, None)
    if action == 'browser_revoke':
        oauth.revoke_grant(gid, setup[0], setup[1])
    elif action == 'token_revoke':
        oauth.revoke_token({'token': tokens['refresh_token'], 'client_id': setup[2]})
    elif action == 'logout':
        manage_auth.revoke(setup[0], None)
    else:
        original = tokens
        tokens = refresh(setup, original['refresh_token'])
        with pytest.raises(oauth.OAuthError):
            refresh(setup, original['refresh_token'])
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(tokens['access_token'])
    with pytest.raises(oauth.OAuthError):
        refresh(setup, tokens['refresh_token'])
    assert query('SELECT revoked_at FROM driftread.review_oauth_grants WHERE id=%s', (gid,))[0]['revoked_at']


@pytest.mark.parametrize('expiry', [datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2099, 1, 1)])
def test_expiry_update_rejects_past_and_naive_values_without_changes(setup, expiry):
    tokens = exchange(setup)
    gid = grant_id(tokens)
    previous = token_row(tokens['refresh_token'])['expires_at']
    with pytest.raises(oauth.OAuthError) as caught:
        update(setup, gid, expiry)
    assert caught.value.status_code == 422
    assert token_row(tokens['refresh_token'])['expires_at'] == previous


@pytest.mark.parametrize('state,status', [('unknown', 404), ('revoked', 409), ('expired', 409), ('other_owner', 404)])
def test_expiry_update_cannot_restore_inactive_or_foreign_connections(setup, state, status):
    tokens = exchange(setup)
    gid = grant_id(tokens)
    if state == 'unknown':
        gid = str(uuid4())
    elif state == 'revoked':
        oauth.revoke_grant(gid, setup[0], setup[1])
    elif state == 'expired':
        query("UPDATE driftread.review_oauth_grants SET expires_at=now()-interval '1 second' WHERE id=%s", (gid,))
    else:
        uid = str(uuid4())
        query('INSERT INTO auth.users(id) VALUES(%s)', (uid,))
        query('UPDATE driftread.review_oauth_grants SET owner_user_id=%s WHERE id=%s', (uid, gid))
    with pytest.raises(oauth.OAuthError) as caught:
        update(setup, gid, None)
    assert caught.value.status_code == status


def test_expiry_update_requires_current_owner_and_telegram_session(setup):
    tokens = exchange(setup)
    gid = grant_id(tokens)
    with pytest.raises(HTTPException):
        oauth.update_grant_expiry(gid, None, setup[0], 'wrong-management-token')
    with pytest.raises(HTTPException):
        oauth.update_grant_expiry(gid, None, AuthUser(str(uuid4()), session_id=setup[0].session_id), setup[1])
    assert token_row(tokens['refresh_token'])['expires_at'] is not None


def test_refresh_racing_expiry_update_keeps_the_live_rotated_token_in_sync(setup):
    tokens = exchange(setup)
    gid = grant_id(tokens)
    expiry = datetime.now(timezone.utc) + timedelta(days=90)
    ready = Barrier(2)

    def rotate():
        ready.wait(timeout=3)
        return refresh(setup, tokens['refresh_token'])

    def extend():
        ready.wait(timeout=3)
        return update(setup, gid, expiry)

    with ThreadPoolExecutor(2) as pool:
        rotation = pool.submit(rotate)
        extension = pool.submit(extend)
        rotated = rotation.result(timeout=5)
        assert datetime.fromisoformat(extension.result(timeout=5)['expires_at']) == expiry
    live = query("""SELECT t.token_hash,t.expires_at,g.expires_at AS grant_expiry
      FROM driftread.review_oauth_tokens t JOIN driftread.review_oauth_grants g ON g.id=t.grant_id
      WHERE t.grant_id=%s AND t.kind='refresh' AND t.consumed_at IS NULL""", (gid,))
    assert len(live) == 1
    assert live[0]['token_hash'] == oauth._digest(rotated['refresh_token'])
    assert live[0]['expires_at'] == live[0]['grant_expiry'] == expiry
    assert refresh(setup, rotated['refresh_token'])['access_token']


def test_permanent_cleanup_preserves_active_and_cascades_old_revoked_grant(setup):
    live = exchange(setup)
    live_gid = grant_id(live)
    update(setup, live_gid, None)
    revoked = exchange(setup)
    revoked_gid = grant_id(revoked)
    update(setup, revoked_gid, None)
    query("UPDATE driftread.review_oauth_grants SET created_at=now()-interval '400 days' WHERE id IN (%s,%s)",
          (live_gid, revoked_gid))
    query("UPDATE driftread.review_oauth_grants SET revoked_at=now()-interval '31 days' WHERE id=%s", (revoked_gid,))
    assert query('SELECT code_hash FROM driftread.review_oauth_codes WHERE grant_id=%s', (revoked_gid,))
    assert query('SELECT token_hash FROM driftread.review_oauth_tokens WHERE grant_id=%s', (revoked_gid,))
    with manage_auth.connection() as cur:
        oauth._cleanup(cur)
    assert oauth.validate_access_token(live['access_token'])['grant_id'] == live_gid
    assert refresh(setup, live['refresh_token'])['access_token']
    for table in ('review_oauth_grants', 'review_oauth_codes', 'review_oauth_tokens'):
        column = 'id' if table == 'review_oauth_grants' else 'grant_id'
        assert not query(f'SELECT {column} FROM driftread.{table} WHERE {column}=%s', (revoked_gid,))


def test_expiry_migration_replays_and_preserves_access_expiry_constraint(setup):
    migration = Path(__file__).parents[1] / 'migrations/20261016050000_review_oauth_expiry.sql'
    query(migration.read_text())
    query(migration.read_text())
    query((Path(__file__).parent / 'sql/test_review_oauth_expiry.sql').read_text())
