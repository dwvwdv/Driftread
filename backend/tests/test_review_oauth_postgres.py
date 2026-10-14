"""Real PostgreSQL row locking, credentials, replay fencing and private grants."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4
from fastapi import HTTPException
import psycopg2
import pytest
from auth import AuthUser
from services import manage_auth,review_oauth as oauth
from tests.test_postgres_lifecycle import pg_database

CALLBACK='https://chatgpt.com/connector_platform_oauth_redirect'
VERIFIER='v'*64
CHALLENGE=base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')


@pytest.fixture
def setup(pg_database,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',pg_database)
    monkeypatch.setenv('SOURCE_REVIEW_OAUTH_BASE_URL','https://reader.example')
    uid,sid,token=str(uuid4()),str(uuid4()),'management-'+uuid4().hex
    monkeypatch.setenv('MANAGE_OWNER_USER_ID',uid)
    with psycopg2.connect(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent/'sql/test_review_oauth.sql').read_text())
            cur.execute('INSERT INTO auth.users(id) VALUES(%s)',(uid,))
            cur.execute('INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)',(sid,uid))
            cur.execute("INSERT INTO driftread.manage_sessions(token_hash,user_id,auth_session_id,expires_at) VALUES(%s,%s,%s,now()+interval '1 hour')",(oauth._digest(token),uid,sid))
    conn.close()
    client=oauth.register_client({'client_name':'ChatGPT test','redirect_uris':[CALLBACK]})
    return AuthUser(uid,session_id=sid),token,client['client_id']


def query(sql,params=()):
    with manage_auth.connection() as cur:
        cur.execute(sql,params)
        return cur.fetchall() if cur.description else None


def request(setup,scope='source:review offline_access',**changes):
    return oauth.begin_authorization({'client_id':setup[2],'redirect_uri':CALLBACK,'response_type':'code',
     'scope':scope,'state':'opaque/client+state','resource':oauth.resource_url(),
     'code_challenge':CHALLENGE,'code_challenge_method':'S256',**changes})['request_id']


def authorization(setup,scope='source:review offline_access'):
    response=oauth.decide_authorization(request(setup,scope),True,setup[0],setup[1])
    parts=parse_qs(urlsplit(response['redirect_url']).query)
    assert parts['state']==['opaque/client+state']
    return parts['code'][0]


def exchange(setup,code=None,**changes):
    return oauth.exchange_token({'grant_type':'authorization_code','code':code or authorization(setup),
     'client_id':setup[2],'redirect_uri':CALLBACK,'code_verifier':VERIFIER,'resource':oauth.resource_url(),**changes})


def refresh(setup,token,**changes):
    return oauth.exchange_token({'grant_type':'refresh_token','refresh_token':token,'client_id':setup[2],
     'resource':oauth.resource_url(),**changes})


def test_owner_telegram_and_explicit_consent(setup):
    rid=request(setup)
    assert not query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s',(setup[2],))
    consent=oauth.get_consent(rid,setup[0],setup[1])
    assert consent['scope']=='source:review offline_access' and consent['redirect_host']=='chatgpt.com'
    assert consent['client_name']=='ChatGPT test'
    with pytest.raises(HTTPException):
        oauth.get_consent(rid,AuthUser(str(uuid4()),session_id=setup[0].session_id),setup[1])
    with pytest.raises(HTTPException):
        oauth.decide_authorization(rid,True,setup[0],'wrong')
    denied=oauth.decide_authorization(rid,False,setup[0],setup[1])
    assert parse_qs(urlsplit(denied['redirect_url']).query)['error']==['access_denied']
    with pytest.raises(oauth.OAuthError):
        oauth.decide_authorization(rid,True,setup[0],setup[1])
    assert not query('SELECT id FROM driftread.review_oauth_grants WHERE owner_user_id=%s',(setup[0].id,))
    assert not query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s',(setup[2],))


def test_one_use_code_and_hashed_secrets(setup):
    rid=request(setup)
    code=parse_qs(urlsplit(oauth.decide_authorization(rid,True,setup[0],setup[1])['redirect_url']).query)['code'][0]
    tokens=exchange(setup,code)
    identity=oauth.validate_access_token(tokens['access_token'])
    assert identity['owner_user_id']==setup[0].id and identity['scope']=='source:review'
    assert tokens['expires_in']==900
    with pytest.raises(oauth.OAuthError):
        exchange(setup,code)
    for table,column,secret in [('review_oauth_requests','request_hash',rid),('review_oauth_codes','code_hash',code),
      ('review_oauth_tokens','token_hash',tokens['access_token']),('review_oauth_tokens','token_hash',tokens['refresh_token'])]:
        rows=query(f'SELECT {column} FROM driftread.{table} WHERE {column}=%s',(oauth._digest(secret),))
        assert rows and rows[0][column]!=secret and len(rows[0][column])==64


def test_pkce_client_redirect_and_resource_binding(setup):
    code=authorization(setup)
    other=oauth.register_client({'redirect_uris':[CALLBACK]})['client_id']
    for changes in [{'code_verifier':'w'*64},{'client_id':other},
      {'redirect_uri':'https://chatgpt.com/connector/oauth/another'},{'resource':'https://reader.example/api/mcp/'}]:
        with pytest.raises(oauth.OAuthError):
            exchange(setup,code,**changes)
    assert exchange(setup,code)['access_token']
    with pytest.raises(oauth.OAuthError):
        request(setup,redirect_uri='https://chatgpt.com/connector/oauth/unregistered')


def test_no_offline_permission_no_refresh(setup):
    tokens=exchange(setup,authorization(setup,scope='source:review'))
    assert 'refresh_token' not in tokens and tokens['scope']=='source:review'


def test_refresh_rotation_replay_commits_revocation(setup):
    original=exchange(setup)
    rotated=refresh(setup,original['refresh_token'])
    assert rotated['refresh_token']!=original['refresh_token']
    assert oauth.validate_access_token(rotated['access_token'])
    with pytest.raises(oauth.OAuthError):
        refresh(setup,original['refresh_token'])
    for tokens in (original,rotated):
        with pytest.raises(oauth.OAuthError):
            oauth.validate_access_token(tokens['access_token'])
    assert query('SELECT revoked_at FROM driftread.review_oauth_grants WHERE owner_user_id=%s',(setup[0].id,))[0]['revoked_at']


def test_wrong_refresh_client_does_not_consume(setup):
    tokens=exchange(setup)
    other=oauth.register_client({'redirect_uris':[CALLBACK]})['client_id']
    with pytest.raises(oauth.OAuthError):
        refresh(setup,tokens['refresh_token'],client_id=other)
    assert oauth.validate_access_token(tokens['access_token'])
    assert refresh(setup,tokens['refresh_token'])


def test_request_code_access_grant_expiry(setup):
    rid=request(setup)
    query("UPDATE driftread.review_oauth_requests SET expires_at=now()-interval '1 second' WHERE request_hash=%s",(oauth._digest(rid),))
    with pytest.raises(oauth.OAuthError):
        oauth.get_consent(rid,setup[0],setup[1])
    code=authorization(setup)
    query("UPDATE driftread.review_oauth_codes SET expires_at=now()-interval '1 second' WHERE code_hash=%s",(oauth._digest(code),))
    with pytest.raises(oauth.OAuthError):
        exchange(setup,code)
    tokens=exchange(setup)
    query("UPDATE driftread.review_oauth_tokens SET expires_at=now()-interval '1 second' WHERE token_hash=%s",(oauth._digest(tokens['access_token']),))
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(tokens['access_token'])
    tokens=refresh(setup,tokens['refresh_token'])
    query("UPDATE driftread.review_oauth_grants SET expires_at=now()-interval '1 second' WHERE owner_user_id=%s",(setup[0].id,))
    with pytest.raises(oauth.OAuthError):
        refresh(setup,tokens['refresh_token'])


@pytest.mark.parametrize('action',['delete_session','ban','session_expiry','owner_change','anonymous'])
def test_live_auth_and_current_owner_each_request(setup,monkeypatch,action):
    tokens=exchange(setup)
    if action=='delete_session':
        query('DELETE FROM auth.sessions WHERE id=%s',(setup[0].session_id,))
    elif action=='ban':
        query("UPDATE auth.users SET banned_until=now()+interval '1 day' WHERE id=%s",(setup[0].id,))
    elif action=='session_expiry':
        query("UPDATE auth.sessions SET not_after=now()-interval '1 second' WHERE id=%s",(setup[0].session_id,))
    elif action=='anonymous':
        query('UPDATE auth.users SET is_anonymous=true WHERE id=%s',(setup[0].id,))
    else:
        monkeypatch.setenv('MANAGE_OWNER_USER_ID',str(uuid4()))
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(tokens['access_token'])
    with pytest.raises(oauth.OAuthError):
        refresh(setup,tokens['refresh_token'])


def test_telegram_session_expiry_and_logout_revoke(setup):
    tokens=exchange(setup)
    query("UPDATE driftread.manage_sessions SET expires_at=now()-interval '1 second' WHERE user_id=%s",(setup[0].id,))
    assert oauth.validate_access_token(tokens['access_token'])
    manage_auth.revoke(setup[0],setup[1])
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(tokens['access_token'])


def test_list_revoke_grant_and_rfc7009(setup):
    tokens=exchange(setup)
    rows=oauth.list_grants(setup[0],setup[1])
    assert len(rows)==1 and rows[0]['client_name']=='ChatGPT test'
    oauth.revoke_grant(rows[0]['id'],setup[0],setup[1])
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(tokens['access_token'])
    tokens=exchange(setup)
    assert oauth.revoke_token({'token':tokens['refresh_token'],'client_id':'wrong'})=={}
    assert oauth.validate_access_token(tokens['access_token'])
    oauth.revoke_token({'token':tokens['refresh_token'],'client_id':setup[2]})
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(tokens['access_token'])
    assert oauth.revoke_token({'token':'unknown','client_id':setup[2]})=={}


def test_concurrent_code_and_refresh(setup):
    code=authorization(setup)
    def redeem():
        try:
            return exchange(setup,code)
        except oauth.OAuthError:
            return None
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:redeem(),range(2)))
    assert sum(isinstance(x,dict) for x in results)==1
    tokens=next(x for x in results if x)
    def rotate():
        try:
            return refresh(setup,tokens['refresh_token'])
        except oauth.OAuthError:
            return None
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:rotate(),range(2)))
    assert sum(isinstance(x,dict) for x in results)==1
    with pytest.raises(oauth.OAuthError):
        oauth.validate_access_token(next(x for x in results if x)['access_token'])


def test_request_cap(setup,monkeypatch):
    monkeypatch.setattr(oauth,'REQUEST_LIMIT',0)
    with pytest.raises(oauth.OAuthError) as exc:
        request(setup)
    assert exc.value.status_code==429


def test_private_rls_migration_rerun(setup):
    for table in ['review_oauth_clients','review_oauth_requests','review_oauth_grants','review_oauth_codes','review_oauth_tokens','review_oauth_registration_key']:
        for role in ['anon','authenticated','service_role']:
            assert not query("SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE') AS allowed",(role,'driftread.'+table))[0]['allowed']
        assert query('SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass',('driftread.'+table,))[0]['relrowsecurity']
    query((Path(__file__).parents[1]/'migrations/20261014010000_source_review_oauth.sql').read_text())


def test_cleanup_keeps_replay_fence(setup):
    tokens=exchange(setup)
    refresh(setup,tokens['refresh_token'])
    rid=request(setup)
    query("UPDATE driftread.review_oauth_requests SET expires_at=now()-interval '1 second' WHERE request_hash=%s",(oauth._digest(rid),))
    with manage_auth.connection() as cur:
        oauth._cleanup(cur)
    assert not query('SELECT request_hash FROM driftread.review_oauth_requests WHERE request_hash=%s',(oauth._digest(rid),))
    assert query('SELECT consumed_at FROM driftread.review_oauth_tokens WHERE token_hash=%s',(oauth._digest(tokens['refresh_token']),))[0]['consumed_at']


def test_anonymous_dcr_cannot_exhaust_persistent_client_capacity(setup):
    before = query('SELECT count(*) AS n FROM driftread.review_oauth_clients')[0]['n']
    ids = {oauth.register_client({'redirect_uris':[CALLBACK]})['client_id'] for _ in range(120)}
    assert len(ids)==120
    assert query('SELECT count(*) AS n FROM driftread.review_oauth_clients')[0]['n']==before
    assert query('SELECT count(*) AS n FROM driftread.review_oauth_registration_key')[0]['n']==1
    assert exchange(setup)['access_token']
    assert query('SELECT count(*) AS n FROM driftread.review_oauth_clients')[0]['n']==before+1


def test_approved_cached_client_survives_sixty_day_grant_cleanup(setup):
    exchange(setup)
    query("""UPDATE driftread.review_oauth_clients SET last_used_at=now()-interval '61 days'
      WHERE client_id=%s""",(setup[2],))
    query("""UPDATE driftread.review_oauth_grants SET created_at=now()-interval '61 days',
      expires_at=now()-interval '31 days' WHERE client_id=%s""",(setup[2],))
    query("UPDATE driftread.review_oauth_requests SET expires_at=now()-interval '61 days' WHERE client_id=%s",(setup[2],))
    with manage_auth.connection() as cur:
        oauth._cleanup(cur)
    assert not query('SELECT id FROM driftread.review_oauth_grants WHERE client_id=%s',(setup[2],))
    assert query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s',(setup[2],))
    # The same ID cached by ChatGPT can obtain a new grant; neither the public
    # registration nor the approved metadata expires with its old tokens.
    assert exchange(setup)['access_token']
    assert len(query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s',(setup[2],)))==1


def test_registration_signing_key_persists_between_connections(setup):
    before = query('SELECT signing_key FROM driftread.review_oauth_registration_key')[0]['signing_key']
    registered = oauth.register_client({'redirect_uris':[CALLBACK]})['client_id']
    assert oauth.get_consent(request((setup[0],setup[1],registered)),setup[0],setup[1])
    assert oauth.get_consent(request(setup),setup[0],setup[1])
    assert query('SELECT signing_key FROM driftread.review_oauth_registration_key')[0]['signing_key']==before


def test_tampered_registration_cannot_begin_authorization(setup):
    signed,signature=setup[2].split('.')
    tampered=signed+'.'+('A' if signature[0]!='A' else 'B')+signature[1:]
    with pytest.raises(oauth.OAuthError) as exc:
        request((setup[0],setup[1],tampered))
    assert exc.value.error=='invalid_client'
    assert not query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s',(tampered,))


def test_consent_materializes_once_and_replay_does_not_duplicate_client(setup):
    rid=request(setup)
    oauth.decide_authorization(rid,True,setup[0],setup[1])
    with pytest.raises(oauth.OAuthError):
        oauth.decide_authorization(rid,True,setup[0],setup[1])
    oauth.decide_authorization(request(setup),True,setup[0],setup[1])
    assert len(query('SELECT client_id FROM driftread.review_oauth_clients WHERE client_id=%s',(setup[2],)))==1
    assert len(query('SELECT id FROM driftread.review_oauth_grants WHERE client_id=%s',(setup[2],)))==2


def test_maximum_registration_metadata_approves_and_exchanges(setup):
    uris=['https://chatgpt.com/connector/oauth/'+str(i)+'a'*127 for i in range(5)]
    registered=oauth.register_client({'client_name':'😀'*100,'redirect_uris':uris})['client_id']
    assert len(registered)<=2048
    other=(setup[0],setup[1],registered)
    rid=request(other,redirect_uri=uris[-1])
    assert oauth.get_consent(rid,setup[0],setup[1])['client_name']=='😀'*100
    response=oauth.decide_authorization(rid,True,setup[0],setup[1])
    code=parse_qs(urlsplit(response['redirect_url']).query)['code'][0]
    tokens=exchange(other,code,redirect_uri=uris[-1])
    assert oauth.validate_access_token(tokens['access_token'])
    assert refresh(other,tokens['refresh_token'])['access_token']


def test_existing_registered_client_remains_compatible(setup):
    legacy='drc_'+uuid4().hex
    query("INSERT INTO driftread.review_oauth_clients(client_id,client_name,redirect_uris) VALUES(%s,'Legacy ChatGPT',%s::jsonb)",
          (legacy,'["'+CALLBACK+'"]'))
    old=(setup[0],setup[1],legacy)
    assert oauth.get_consent(request(old),setup[0],setup[1])['client_name']=='Legacy ChatGPT'
    assert exchange(old)['access_token']


def test_standard_registration_metadata_is_not_fetched_or_trusted(setup):
    callback='https://chatgpt.com/connector/oauth/safe-callback_1'
    registered=oauth.register_client({'client_name':'ChatGPT','redirect_uris':[callback],
     'token_endpoint_auth_method':'none','grant_types':['authorization_code','refresh_token'],
     'response_types':['code'],'scope':'source:review offline_access',
     'client_uri':'http://127.0.0.1/never-fetched','logo_uri':'http://169.254.169.254/no-fetch',
     'contacts':['test@example.invalid'],'software_id':'chatgpt','software_version':'1'})
    assert 'client_uri' not in registered and 'logo_uri' not in registered
    rid=request((setup[0],setup[1],registered['client_id']),redirect_uri=callback)
    assert oauth.get_consent(rid,setup[0],setup[1])['redirect_host']=='chatgpt.com'


def test_consent_racing_logout_never_leaves_live_grant(setup):
    from threading import Barrier
    ready=Barrier(2)
    rid=request(setup)
    def consent():
        ready.wait(timeout=3)
        try:
            return oauth.decide_authorization(rid,True,setup[0],setup[1])
        except HTTPException:
            return None
    def logout():
        ready.wait(timeout=3)
        manage_auth.revoke(setup[0],setup[1])
    with ThreadPoolExecutor(2) as pool:
        one=pool.submit(consent)
        two=pool.submit(logout)
        one.result(timeout=5)
        two.result(timeout=5)
    assert not query('SELECT id FROM driftread.review_oauth_grants WHERE owner_user_id=%s AND revoked_at IS NULL',(setup[0].id,))
