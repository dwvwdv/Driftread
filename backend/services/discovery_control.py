"""Persistent discovery pause, shared by API, worker and management MCP."""
import asyncio
from contextlib import suppress

from pydantic import BaseModel, ConfigDict, Field

from services.discovery_config import discovery_enabled
from services.settings import DISCOVERY_CONTROL_KEY, DiscoveryControlValue, get_setting, save_setting

POLL_SECONDS = 1.0


class ControlError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


class ControlUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool
    expected_version: int = Field(ge=0)


class DiscoveryPaused(Exception):
    pass


def runtime_enabled(db) -> bool:
    # No cache, and no fallback on invalid rows or database failure.
    return DiscoveryControlValue.model_validate(get_setting(db, DISCOVERY_CONTROL_KEY).value).enabled


def control_state(setting) -> dict:
    enabled = DiscoveryControlValue.model_validate(setting.value).enabled
    deployment_enabled = discovery_enabled()
    return {'enabled': enabled, 'deployment_enabled': deployment_enabled,
            'effective_enabled': enabled and deployment_enabled, 'version': setting.version,
            'updated_at': setting.updated_at}


def read_control(db) -> dict:
    return control_state(get_setting(db, DISCOVERY_CONTROL_KEY))


async def update_control(db, enabled: bool, expected_version: int) -> dict:
    setting = await save_setting(db, DISCOVERY_CONTROL_KEY, {'enabled': enabled}, expected_version)
    if setting is None:
        raise ControlError(409, 'Discovery control changed; reload before saving')
    return control_state(setting)


async def can_start(db) -> bool:
    return discovery_enabled() and await asyncio.to_thread(runtime_enabled, db)


async def while_enabled(db, action):
    """Cancel an active discovery coroutine when the persisted switch is off.

    Synchronous database work already submitted to a thread may finish. Read
    failures also cancel activity rather than continuing with a stale decision.
    The caller owns job acknowledgement; pausing is not a failed probe.
    """
    if not await asyncio.to_thread(runtime_enabled, db):
        raise DiscoveryPaused()
    task = asyncio.create_task(action())
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
            if done:
                return await task
            if not await asyncio.to_thread(runtime_enabled, db):
                raise DiscoveryPaused()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
