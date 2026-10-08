from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from database import get_client
from routers.admin_settings import router
from services.settings import DISCOVERY_PROFILES_KEY, SETTING_DEFINITIONS, SettingDefinition

KEY = {"x-api-key": "test-admin-key"}
BASE = "/admin/settings"


@pytest.fixture
def settings_app(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr("services.settings.validate_fetch_url", AsyncMock(side_effect=lambda url: url))
    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = None

    async def dependency():
        return db
    app.dependency_overrides[get_client] = dependency
    return app, db


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,status", [({}, 422), ({"x-api-key": "wrong"}, 403)])
async def test_settings_requires_admin(settings_app, headers, status):
    app, db = settings_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(BASE, headers=headers)).status_code == status
        assert (await client.put(f"{BASE}/{DISCOVERY_PROFILES_KEY}", headers=headers,
                                 json={"value": {"profiles": []}, "expected_version": 0})).status_code == status
    db.table.assert_not_called()
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_list_settings_defaults(settings_app):
    app, db = settings_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(BASE, headers=KEY)
    assert response.status_code == 200
    setting = response.json()["settings"][0]
    assert setting["key"] == DISCOVERY_PROFILES_KEY and setting["version"] == 0
    assert len(setting["value"]["profiles"]) == 7


@pytest.mark.asyncio
async def test_save_and_conflict(settings_app):
    app, db = settings_app
    row = {"key": DISCOVERY_PROFILES_KEY, "value": {"profiles": []}, "version": 1, "updated_at": "2026-01-01"}
    db.rpc.return_value.execute.return_value.data = row
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"{BASE}/{DISCOVERY_PROFILES_KEY}", headers=KEY,
                                    json={"value": {"profiles": []}, "expected_version": 0})
        assert response.status_code == 200 and response.json() == row
        db.rpc.return_value.execute.return_value.data = None
        conflict = await client.put(f"{BASE}/{DISCOVERY_PROFILES_KEY}", headers=KEY,
                                    json={"value": {"profiles": []}, "expected_version": 0})
    assert conflict.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("key,body,status", [
    ("unknown", {"value": {}, "expected_version": 0}, 404),
    (DISCOVERY_PROFILES_KEY, {"value": {"profiles": "bad"}, "expected_version": 0}, 422),
    (DISCOVERY_PROFILES_KEY, {"value": {"profiles": []}, "expected_version": -1}, 422),
    (DISCOVERY_PROFILES_KEY, {"value": {"profiles": []}, "expected_version": True}, 422),
])
async def test_invalid_request_never_mutates(settings_app, key, body, status):
    app, db = settings_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"{BASE}/{key}", headers=KEY, json=body)
    assert response.status_code == status
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_registered_setting_uses_generic_routes(settings_app, monkeypatch):
    class ReaderPreferences(BaseModel):
        compact: bool = False

    monkeypatch.setitem(SETTING_DEFINITIONS, "reader.preferences",
                        SettingDefinition(ReaderPreferences, ReaderPreferences))
    app, db = settings_app
    row = {"key": "reader.preferences", "value": {"compact": True}, "version": 1,
           "updated_at": "2026-01-01"}
    db.rpc.return_value.execute.return_value.data = row
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get(BASE, headers=KEY)
        defaults = next(setting for setting in listing.json()["settings"]
                        if setting["key"] == "reader.preferences")
        assert defaults["value"] == {"compact": False} and defaults["version"] == 0
        response = await client.put(f"{BASE}/reader.preferences", headers=KEY,
                                    json={"value": {"compact": True}, "expected_version": 0})
    assert response.status_code == 200 and response.json() == row
    assert db.rpc.call_args.args[1]["p_seeds"] == []


@pytest.mark.asyncio
async def test_corrupt_database_response_is_server_error_not_input_error(settings_app):
    app, db = settings_app
    db.rpc.return_value.execute.return_value.data = {
        "key": DISCOVERY_PROFILES_KEY, "value": {"profiles": []}, "version": 0,
    }
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(f"{BASE}/{DISCOVERY_PROFILES_KEY}", headers=KEY,
                                    json={"value": {"profiles": []}, "expected_version": 0})
    assert response.status_code == 500
