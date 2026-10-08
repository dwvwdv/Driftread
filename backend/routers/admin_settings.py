import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from supabase import Client

from database import get_client
from routers.admin import require_api_key
from services.feed_discovery import DiscoveryError
from services.settings import AppSetting, list_settings, save_setting

router = APIRouter(prefix="/admin/settings", tags=["admin", "settings"],
                   dependencies=[Depends(require_api_key)])


class SettingsList(BaseModel):
    settings: list[AppSetting]


class SaveSettingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: dict[str, Any]
    expected_version: int = Field(ge=0)


@router.get("", response_model=SettingsList)
async def read_settings(db: Client = Depends(get_client)) -> SettingsList:
    return SettingsList(settings=await asyncio.to_thread(list_settings, db))


@router.put("/{key}", response_model=AppSetting)
async def update_setting(key: str, body: SaveSettingRequest,
                         db: Client = Depends(get_client)) -> AppSetting:
    try:
        saved = await save_setting(db, key, body.value, body.expected_version)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown setting key") from None
    except (ValidationError, ValueError, DiscoveryError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if saved is None:
        raise HTTPException(status_code=409, detail="Setting changed; reload before saving")
    return saved
