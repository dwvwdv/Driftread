"""Narrow OAuth authorization server for ChatGPT source review.

Opaque secrets are returned once; only SHA256 digests are durable. Grants are
bound to the owner and live Supabase session that completed Telegram consent.
All exchanges and revocations serialize on the grant row. This is deliberately
not a general OAuth provider or a grant to the browser/admin REST interfaces.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import secrets
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg2.extras import Json
from auth import AuthUser
from services import manage_auth

SCOPE = "source:review"
REQUEST_SECONDS = 600
CODE_SECONDS = 120
ACCESS_SECONDS = 900
GRANT_SECONDS = 30 * 86400
CLIENT_LIMIT = 100
REQUEST_LIMIT = 1000
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_CALLBACK = re.compile(r"^/connector/oauth/[A-Za-z0-9_-]{1,128}$")


class OAuthError(Exception):
    def __init__(self, error: str, status_code: int = 400, description: str = "OAuth request is invalid"):
        self.error = error
        self.status_code = status_code
        self.description = description
        super().__init__(description)


def base_url() -> str:
    value = os.getenv("SOURCE_REVIEW_OAUTH_BASE_URL", "").strip()
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port in (None, 443)
    except ValueError:
        raise OAuthError('temporarily_unavailable', 503, 'Source review OAuth is not configured') from None
    if (not value or parsed.scheme != 'https' or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or not valid_port
            or any(c.isspace() for c in value)):
        raise OAuthError('temporarily_unavailable', 503, 'Source review OAuth is not configured')
    return value.rstrip('/')


def resource_url() -> str:
    return base_url() + '/api/admin/mcp/'


def oauth_metadata() -> dict:
    base = base_url()
    return {'issuer': base, 'authorization_endpoint': base+'/api/review-oauth/authorize',
            'token_endpoint': base+'/api/review-oauth/token',
            'registration_endpoint': base+'/api/review-oauth/register',
            'revocation_endpoint': base+'/api/review-oauth/revoke',
            'response_types_supported': ['code'],
            'grant_types_supported': ['authorization_code', 'refresh_token'],
            'token_endpoint_auth_methods_supported': ['none'],
            'revocation_endpoint_auth_methods_supported': ['none'],
            'code_challenge_methods_supported': ['S256'],
            'scopes_supported': [SCOPE, 'offline_access']}


def resource_metadata() -> dict:
    return {'resource': resource_url(), 'authorization_servers': [base_url()],
            'scopes_supported': [SCOPE, 'offline_access'], 'bearer_methods_supported': ['header']}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _strict(params, allowed):
    if not isinstance(params, dict) or set(params) - set(allowed):
        raise OAuthError('invalid_request', description='Unsupported request parameters')


def _text(params, key, maximum=2048, required=True):
    value = params.get(key)
    if value is None and not required:
        return ''
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise OAuthError('invalid_request', description='Invalid request parameter')
    return value


def _redirect(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise OAuthError('invalid_redirect_uri') from None
    if (parsed.scheme != 'https' or parsed.netloc != 'chatgpt.com'
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not (parsed.path == '/connector_platform_oauth_redirect' or _CALLBACK.fullmatch(parsed.path))):
        raise OAuthError('invalid_redirect_uri', description='Only official ChatGPT callback URLs are supported')
    return value


def _resource(params):
    value = _text(params, 'resource')
    if value != resource_url():
        raise OAuthError('invalid_target', description='Resource does not match source review MCP')
    return value


def _scope(value):
    pieces = value.split(' ')
    if SCOPE not in pieces or set(pieces) - {SCOPE, 'offline_access'} or len(pieces) != len(set(pieces)):
        raise OAuthError('invalid_scope', description='Only source review permission is supported')
    return ' '.join([SCOPE] + (['offline_access'] if 'offline_access' in pieces else []))


def _cleanup(cur):
    # Bound each opportunistic deletion. Consumed token digests remain while
    # the grant can still be used, fencing refresh replays after rotation.
    cur.execute("""DELETE FROM driftread.review_oauth_requests WHERE request_hash IN
      (SELECT request_hash FROM driftread.review_oauth_requests WHERE expires_at<now() LIMIT 200)""")
    cur.execute("""DELETE FROM driftread.review_oauth_grants WHERE id IN
      (SELECT id FROM driftread.review_oauth_grants WHERE expires_at<now()-interval '30 days' LIMIT 100)""")
    cur.execute("""DELETE FROM driftread.review_oauth_clients WHERE client_id IN
      (SELECT c.client_id FROM driftread.review_oauth_clients c WHERE c.last_used_at<now()-interval '7 days'
       AND NOT EXISTS(SELECT 1 FROM driftread.review_oauth_grants g WHERE g.client_id=c.client_id)
       AND NOT EXISTS(SELECT 1 FROM driftread.review_oauth_requests r WHERE r.client_id=c.client_id) LIMIT 100)""")


def register_client(body: dict) -> dict:
    base_url()
    _strict(body, {'client_name','redirect_uris','grant_types','response_types','token_endpoint_auth_method','scope',
                   'client_uri','logo_uri','tos_uri','policy_uri','contacts','software_id','software_version'})
    # Standard optional metadata is bounded and ignored, never fetched or used
    # as trusted branding on the consent page.
    for key in ('client_uri','logo_uri','tos_uri','policy_uri','software_id','software_version'):
        if key in body:
            _text(body,key,2048 if key.endswith('_uri') else 100)
    if 'contacts' in body and (not isinstance(body['contacts'],list) or len(body['contacts'])>5
                              or any(not isinstance(x,str) or len(x)>254 for x in body['contacts'])):
        raise OAuthError('invalid_client_metadata')
    if body.get('token_endpoint_auth_method', 'none') != 'none':
        raise OAuthError('invalid_client_metadata', description='Only public clients are supported')
    for key, allowed in [('grant_types', {'authorization_code', 'refresh_token'}), ('response_types', {'code'})]:
        if key in body and (not isinstance(body[key], list) or not body[key]
                            or any(not isinstance(x, str) or x not in allowed for x in body[key])):
            raise OAuthError('invalid_client_metadata', description='Unsupported client metadata')
    name = _text(body, 'client_name', 100, required=False) or 'ChatGPT'
    uris = body.get('redirect_uris')
    if not isinstance(uris, list) or not 1 <= len(uris) <= 5 or any(not isinstance(x,str) or len(x)>256 for x in uris):
        raise OAuthError('invalid_client_metadata', description='Invalid redirect URLs')
    uris = list(dict.fromkeys(_redirect(x) for x in uris))
    scope = _scope(_text(body, 'scope', 100, required=False) or SCOPE+' offline_access')
    client_id = 'drc_' + secrets.token_urlsafe(24)
    with manage_auth.connection() as cur:
        # DCR is public. Serialize the cap so parallel registration cannot exceed it.
        cur.execute("SELECT pg_advisory_xact_lock(7142026101401)")
        _cleanup(cur)
        cur.execute("SELECT count(*) AS count FROM driftread.review_oauth_clients")
        if cur.fetchone()['count'] >= CLIENT_LIMIT:
            raise OAuthError('temporarily_unavailable', 429, 'Client registration capacity reached')
        cur.execute("INSERT INTO driftread.review_oauth_clients(client_id,client_name,redirect_uris) VALUES(%s,%s,%s)",
                    (client_id, name, Json(uris)))
    return {'client_id':client_id,'client_name':name,'redirect_uris':uris,
            'token_endpoint_auth_method':'none','grant_types':['authorization_code','refresh_token'],
            'response_types':['code'],'scope':scope}


def begin_authorization(params: dict) -> dict:
    base = base_url()
    _strict(params, {'client_id','redirect_uri','response_type','scope','state','resource','code_challenge','code_challenge_method','prompt'})
    if _text(params, 'response_type', 20) != 'code':
        raise OAuthError('unsupported_response_type')
    if params.get('code_challenge_method') != 'S256':
        raise OAuthError('invalid_request', description='S256 PKCE is required')
    challenge = _text(params, 'code_challenge', 128)
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', challenge):
        raise OAuthError('invalid_request', description='Invalid PKCE challenge')
    if 'prompt' in params and params['prompt'] not in ('consent', 'login'):
        raise OAuthError('invalid_request', description='Interactive consent is required')
    client_id = _text(params, 'client_id', 100)
    redirect = _redirect(_text(params, 'redirect_uri', 256))
    scope = _scope(_text(params, 'scope', 100))
    state = _text(params, 'state', 2048)
    resource = _resource(params)
    request_id = secrets.token_urlsafe(32)
    with manage_auth.connection() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(7142026101402)")
        _cleanup(cur)
        cur.execute("SELECT redirect_uris FROM driftread.review_oauth_clients WHERE client_id=%s", (client_id,))
        client = cur.fetchone()
        if not client or redirect not in client['redirect_uris']:
            raise OAuthError('invalid_client', description='Client or callback is not registered')
        cur.execute("SELECT count(*) AS count FROM driftread.review_oauth_requests")
        if cur.fetchone()['count'] >= REQUEST_LIMIT:
            raise OAuthError('temporarily_unavailable', 429, 'Authorization request capacity reached')
        cur.execute("""INSERT INTO driftread.review_oauth_requests
          (request_hash,client_id,redirect_uri,state,scope,resource,code_challenge,expires_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
          (_digest(request_id),client_id,redirect,state,scope,resource,challenge,_now()+timedelta(seconds=REQUEST_SECONDS)))
        cur.execute("UPDATE driftread.review_oauth_clients SET last_used_at=now() WHERE client_id=%s", (client_id,))
    return {'request_id':request_id,'authorize_url':base+'/manage/mcp-authorize?'+urlencode({'request':request_id})}


def _manage(cur, user, token):
    uid, sid = manage_auth.owner(user)
    if not isinstance(token, str) or not 1 <= len(token) <= 128:
        raise HTTPException(401, '請先完成 Telegram 驗證')
    manage_auth.active_auth_session(cur, uid, sid)
    cur.execute("""SELECT 1 FROM driftread.manage_sessions WHERE token_hash=%s AND user_id=%s
     AND auth_session_id=%s AND expires_at>now() FOR SHARE""", (_digest(token),uid,sid))
    if not cur.fetchone():
        raise HTTPException(401, '後台驗證已失效，請重新登入')
    return uid, sid


def _request(cur, request_id, lock=False):
    if not isinstance(request_id, str) or not _TOKEN.fullmatch(request_id):
        raise OAuthError('invalid_request', description='Authorization request is invalid or expired')
    cur.execute("""SELECT r.*,c.client_name FROM driftread.review_oauth_requests r
      JOIN driftread.review_oauth_clients c ON c.client_id=r.client_id
      WHERE request_hash=%s""" + (' FOR UPDATE OF r' if lock else ''), (_digest(request_id),))
    request = cur.fetchone()
    if (not request or request['expires_at'] <= _now() or request['decided_at']
            or request['resource'] != resource_url()):
        raise OAuthError('invalid_request', description='Authorization request is invalid or expired')
    return request


def get_consent(request_id: str, user: AuthUser, manage_token: str) -> dict:
    base_url()
    with manage_auth.connection() as cur:
        _manage(cur, user, manage_token)
        row = _request(cur, request_id)
    return {'request_id':request_id,'client_name':row['client_name'],
            'redirect_host':'chatgpt.com','scope':row['scope'],'expires_at':row['expires_at'].isoformat()}


def decide_authorization(request_id: str, approved: bool, user: AuthUser, manage_token: str) -> dict:
    base_url()
    if type(approved) is not bool:
        raise OAuthError('invalid_request')
    with manage_auth.connection() as cur:
        uid, sid = _manage(cur, user, manage_token)
        row = _request(cur, request_id, lock=True)
        cur.execute("UPDATE driftread.review_oauth_requests SET decided_at=now() WHERE request_hash=%s", (_digest(request_id),))
        response = {'state':row['state']}
        if approved:
            grant_id, code = str(uuid4()), secrets.token_urlsafe(32)
            cur.execute("""INSERT INTO driftread.review_oauth_grants
              (id,client_id,owner_user_id,auth_session_id,scope,resource,expires_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s)""", (grant_id,row['client_id'],uid,sid,row['scope'],row['resource'],_now()+timedelta(seconds=GRANT_SECONDS)))
            cur.execute("""INSERT INTO driftread.review_oauth_codes(code_hash,grant_id,redirect_uri,code_challenge,expires_at)
              VALUES(%s,%s,%s,%s,%s)""", (_digest(code),grant_id,row['redirect_uri'],row['code_challenge'],_now()+timedelta(seconds=CODE_SECONDS)))
            response['code'] = code
        else:
            response['error'] = 'access_denied'
    return {'redirect_url':row['redirect_uri']+'?'+urlencode(response)}


def _valid_grant(cur, grant):
    if (not grant or grant['revoked_at'] or grant['expires_at'] <= _now()
            or grant['resource'] != resource_url() or SCOPE not in grant['scope'].split(' ')):
        raise OAuthError('invalid_grant', description='Authorization is expired or revoked')
    try:
        configured = str(UUID(os.getenv('MANAGE_OWNER_USER_ID', '').strip()))
    except ValueError:
        raise OAuthError('invalid_grant') from None
    if str(grant['owner_user_id']) != configured:
        raise OAuthError('invalid_grant')
    try:
        manage_auth.active_auth_session(cur, str(grant['owner_user_id']), str(grant['auth_session_id']))
    except HTTPException:
        raise OAuthError('invalid_grant', description='Authorizing login session is no longer active') from None


def _lock_grant(cur, grant_id):
    cur.execute("SELECT * FROM driftread.review_oauth_grants WHERE id=%s FOR UPDATE", (grant_id,))
    grant = cur.fetchone()
    _valid_grant(cur, grant)
    return grant


def _issue_tokens(cur, grant, refresh):
    access = 'droa_' + secrets.token_urlsafe(32)
    now = _now()
    lifetime = min(ACCESS_SECONDS, max(1,int((grant['expires_at']-now).total_seconds())))
    cur.execute("INSERT INTO driftread.review_oauth_tokens(token_hash,grant_id,kind,expires_at) VALUES(%s,%s,'access',%s)",
                (_digest(access),str(grant['id']),now+timedelta(seconds=lifetime)))
    result = {'access_token':access,'token_type':'Bearer','expires_in':lifetime,'scope':grant['scope']}
    if refresh:
        token = 'dror_' + secrets.token_urlsafe(32)
        cur.execute("INSERT INTO driftread.review_oauth_tokens(token_hash,grant_id,kind,expires_at) VALUES(%s,%s,'refresh',%s)",
                    (_digest(token),str(grant['id']),grant['expires_at']))
        result['refresh_token'] = token
    return result


def exchange_token(params: dict) -> dict:
    base_url()
    kind = params.get('grant_type') if isinstance(params, dict) else None
    if kind == 'authorization_code':
        _strict(params, {'grant_type','code','client_id','redirect_uri','code_verifier','resource'})
        code = _text(params, 'code', 128)
        if not _TOKEN.fullmatch(code):
            raise OAuthError('invalid_grant')
        verifier = _text(params, 'code_verifier', 128)
        if not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier):
            raise OAuthError('invalid_grant')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
        redirect = _redirect(_text(params, 'redirect_uri', 256))
        client_id = _text(params, 'client_id', 100)
        _resource(params)
        with manage_auth.connection() as cur:
            cur.execute("SELECT grant_id FROM driftread.review_oauth_codes WHERE code_hash=%s", (_digest(code),))
            found = cur.fetchone()
            if not found:
                raise OAuthError('invalid_grant')
            grant = _lock_grant(cur, found['grant_id'])
            cur.execute("SELECT * FROM driftread.review_oauth_codes WHERE code_hash=%s FOR UPDATE", (_digest(code),))
            row = cur.fetchone()
            if (row['consumed_at'] or row['expires_at'] <= _now() or grant['client_id'] != client_id
                    or row['redirect_uri'] != redirect or not secrets.compare_digest(row['code_challenge'],challenge)):
                raise OAuthError('invalid_grant')
            cur.execute("UPDATE driftread.review_oauth_codes SET consumed_at=now() WHERE code_hash=%s", (_digest(code),))
            result = _issue_tokens(cur, grant, 'offline_access' in grant['scope'].split(' '))
        return result
    if kind == 'refresh_token':
        _strict(params, {'grant_type','refresh_token','client_id','resource','scope'})
        token = _text(params, 'refresh_token', 128)
        if not re.fullmatch(r'dror_[A-Za-z0-9_-]{43}', token):
            raise OAuthError('invalid_grant')
        client_id = _text(params, 'client_id', 100)
        _resource(params)
        failure = None
        with manage_auth.connection() as cur:
            cur.execute("SELECT grant_id FROM driftread.review_oauth_tokens WHERE token_hash=%s AND kind='refresh'", (_digest(token),))
            found = cur.fetchone()
            if not found:
                raise OAuthError('invalid_grant')
            grant = _lock_grant(cur, found['grant_id'])
            if grant['client_id'] != client_id or ('scope' in params and _scope(_text(params, 'scope',100)) != grant['scope']):
                raise OAuthError('invalid_grant')
            cur.execute("SELECT * FROM driftread.review_oauth_tokens WHERE token_hash=%s FOR UPDATE", (_digest(token),))
            row = cur.fetchone()
            if row['consumed_at']:
                # Raise only after the transaction commits the replay fence.
                cur.execute("UPDATE driftread.review_oauth_grants SET revoked_at=now() WHERE id=%s", (grant['id'],))
                failure = OAuthError('invalid_grant', description='Refresh token has already been used')
            elif row['expires_at'] <= _now():
                raise OAuthError('invalid_grant')
            else:
                cur.execute("UPDATE driftread.review_oauth_tokens SET consumed_at=now() WHERE token_hash=%s", (_digest(token),))
                result = _issue_tokens(cur, grant, True)
        if failure:
            raise failure
        return result
    raise OAuthError('unsupported_grant_type')


def validate_access_token(token: str) -> dict:
    base_url()
    if not isinstance(token, str) or not re.fullmatch(r'droa_[A-Za-z0-9_-]{43}', token):
        raise OAuthError('invalid_token', 401, 'Source review access token is invalid')
    with manage_auth.connection() as cur:
        cur.execute("""SELECT g.*,t.expires_at AS token_expires_at FROM driftread.review_oauth_tokens t
          JOIN driftread.review_oauth_grants g ON g.id=t.grant_id
          WHERE t.token_hash=%s AND t.kind='access'""", (_digest(token),))
        grant = cur.fetchone()
        try:
            _valid_grant(cur, grant)
        except OAuthError:
            raise OAuthError('invalid_token', 401, 'Source review access token is expired or revoked') from None
        if grant['token_expires_at'] <= _now():
            raise OAuthError('invalid_token', 401)
        cur.execute("UPDATE driftread.review_oauth_grants SET last_used_at=now() WHERE id=%s", (grant['id'],))
    return {'owner_user_id':str(grant['owner_user_id']),'scope':SCOPE,'grant_id':str(grant['id'])}


def list_grants(user: AuthUser, manage_token: str) -> list:
    base_url()
    with manage_auth.connection() as cur:
        uid, _ = _manage(cur, user, manage_token)
        _cleanup(cur)
        cur.execute("""SELECT g.id,g.created_at,g.expires_at,g.revoked_at,g.last_used_at,g.scope,c.client_name
         FROM driftread.review_oauth_grants g JOIN driftread.review_oauth_clients c ON c.client_id=g.client_id
         WHERE g.owner_user_id=%s ORDER BY g.created_at DESC LIMIT 100""", (uid,))
        rows = cur.fetchall()
    return [{**r,'id':str(r['id']),'created_at':r['created_at'].isoformat(),
             'expires_at':r['expires_at'].isoformat(),'last_used_at':r['last_used_at'].isoformat() if r['last_used_at'] else None,'revoked_at':r['revoked_at'].isoformat() if r['revoked_at'] else None} for r in rows]


def revoke_grant(grant_id: str, user: AuthUser, manage_token: str):
    base_url()
    try:
        gid = str(UUID(str(grant_id)))
    except ValueError:
        raise OAuthError('invalid_request') from None
    with manage_auth.connection() as cur:
        uid, _ = _manage(cur, user, manage_token)
        cur.execute("UPDATE driftread.review_oauth_grants SET revoked_at=coalesce(revoked_at,now()) WHERE id=%s AND owner_user_id=%s", (gid,uid))
    return {'revoked':True}


def revoke_token(params: dict) -> dict:
    base_url()
    _strict(params, {'token','client_id','token_type_hint'})
    token = _text(params, 'token', 128)
    client_id = _text(params, 'client_id', 100)
    if params.get('token_type_hint') not in (None,'access_token','refresh_token'):
        raise OAuthError('unsupported_token_type')
    if re.fullmatch(r'dro[ar]_[A-Za-z0-9_-]{43}', token):
        with manage_auth.connection() as cur:
            cur.execute("""UPDATE driftread.review_oauth_grants g SET revoked_at=coalesce(g.revoked_at,now())
             FROM driftread.review_oauth_tokens t WHERE t.grant_id=g.id AND t.token_hash=%s AND g.client_id=%s""", (_digest(token),client_id))
    return {}
