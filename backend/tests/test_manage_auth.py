import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import jwt
import os
import pytest

from auth import AuthUser
from database import get_client
from routers.admin import router as admin_router
from routers.manage_auth import router
from services import manage_auth as manage
from tests.test_auth import _token


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router, prefix='/api')
    app.include_router(admin_router, prefix='/api')
    # Authentication / validation tests must not initialize a live DB client.
    app.dependency_overrides[get_client] = lambda: None
    return TestClient(app)


def owner_token(uid=None, sid=None, amr=None):
    return jwt.encode({'sub': uid or str(uuid4()), 'session_id': sid or str(uuid4()),
        'is_anonymous':False, 'aud':'authenticated','exp':int(time.time())+600,
        'amr': amr or [{'method':'password','timestamp':int(time.time())}]},
        os.environ['SUPABASE_JWT_SECRET'], algorithm='HS256')


def test_request_requires_login(api, monkeypatch):
    monkeypatch.setattr(manage, 'connection', lambda: pytest.fail('must not access DB'))
    assert api.post('/api/manage/auth/request').status_code == 401
    assert api.post('/api/manage/auth/request', headers={'Authorization':'Bearer bad'}).status_code == 401


def test_non_owner_cannot_send_otp(api, monkeypatch):
    monkeypatch.setenv('MANAGE_OWNER_USER_ID', str(uuid4()))
    monkeypatch.setattr(manage, 'connection', lambda: pytest.fail('must not access DB'))
    assert api.post('/api/manage/auth/request', headers={'Authorization':f'Bearer {owner_token()}'}).status_code == 403


@pytest.mark.parametrize('amr', [[{'method':'oauth','timestamp':int(time.time())}],
                              [{'method':'password','timestamp':int(time.time())-3600}],
                              [{'method':'password','timestamp':'wrong'}]])
def test_requires_recent_password_login(api, monkeypatch, amr):
    uid = str(uuid4())
    monkeypatch.setenv('MANAGE_OWNER_USER_ID', uid)
    monkeypatch.setattr(manage, 'connection', lambda: pytest.fail('must not access DB'))
    assert api.post('/api/manage/auth/request', headers={'Authorization':f'Bearer {owner_token(uid=uid,amr=amr)}'}).status_code == 401


def test_request_rate_limit(api, monkeypatch):
    monkeypatch.setattr(manage, 'issue', lambda _: {'challenge_id':str(uuid4()),'expires_in':300,'retry_after':60})
    headers = {'Authorization':f'Bearer {owner_token()}'}
    for _ in range(5):
        response = api.post('/api/manage/auth/request',headers=headers)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
    assert api.post('/api/manage/auth/request',headers=headers).status_code == 429


@pytest.mark.parametrize('code', ['12345', '1234567', '１２３４５６', 'abcdef'])
def test_verify_requires_exactly_six_ascii_digits(api, code):
    response = api.post('/api/manage/auth/verify', json={'challenge_id':str(uuid4()),'code':code},
                        headers={'Authorization':f'Bearer {owner_token()}'})
    assert response.status_code == 422


def test_browser_admin_calls_cannot_fall_back_to_machine_key(api, monkeypatch):
    monkeypatch.setattr(manage, 'connection', lambda: pytest.fail('must not access DB'))
    headers = {'Authorization':f'Bearer {owner_token()}', 'x-api-key':os.environ['ADMIN_API_KEY']}
    monkeypatch.setenv('MANAGE_OWNER_USER_ID', str(uuid4()))
    assert api.get('/api/admin/feeds',headers=headers).status_code == 403
    assert api.get('/api/admin/feeds',headers={'x-manage-session':'bad','x-api-key':os.environ['ADMIN_API_KEY']}).status_code == 401


def test_verified_management_auth_reaches_admin_router(api, monkeypatch):
    reached=[]
    def accept(auth, token):
        reached.append((auth,token))
    monkeypatch.setattr(manage,'require_session',accept)
    # Invalid query proves auth dependency passed, without touching live data.
    response = api.get('/api/admin/feeds?page_size=0', headers={'Authorization':'Bearer user','x-manage-session':'otp'})
    assert response.status_code == 422
    assert reached == [('Bearer user','otp')]
