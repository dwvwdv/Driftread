"""Typed registry for global, versioned application settings.

Only absent rows use defaults. Database failures and corrupt values propagate;
they must not silently restore crawling that an operator disabled.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.feed_discovery import DiscoveryError, validate_fetch_url
from services.link_harvest import is_denied_host, normalize_host, origin_of

DISCOVERY_PROFILES_KEY = "discovery.profiles"
SEED_VALIDATION_CONCURRENCY = 4
SEED_VALIDATION_TIMEOUT_SECONDS = 20.0


class DiscoveryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=100)
    language: str = Field(min_length=2, max_length=35)
    category: str | None = Field(default=None, max_length=100)
    enabled: bool = True
    quota: int = Field(default=1, ge=0, le=100)
    seed_urls: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("language")
    @classmethod
    def language_tag(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", value):
            raise ValueError("language must be a BCP47-style language tag")
        return value.lower()

    @field_validator("name", "category")
    @classmethod
    def meaningful_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(ch) < 32 for ch in value)):
            raise ValueError("text must be nonempty and contain no control characters")
        return value

    @field_validator("seed_urls")
    @classmethod
    def bounded_urls(cls, values: list[str]) -> list[str]:
        for value in values:
            if (len(value) > 2048 or value != value.strip()
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
                raise ValueError("seed URL must be at most 2048 characters without surrounding whitespace")
            try:
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError("seed URL must use HTTP or HTTPS with a host")
                if parsed.username or parsed.password:
                    raise ValueError("seed URL must not contain credentials")
                # Validate syntax even when loading persisted values; DNS and
                # public-address checks still run asynchronously before saving.
                parsed.port
                host = parsed.hostname
                if not host.isascii():
                    raise ValueError("seed host must use ASCII or punycode")
                try:
                    ip_address(host)
                except ValueError:
                    dns = host.encode("idna").decode("ascii").rstrip(".")
                    if len(dns) > 253 or not all(
                        re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                        for label in dns.split(".")
                    ):
                        raise ValueError("seed URL contains an invalid DNS host")
            except ValueError as exc:
                raise ValueError(f"invalid seed URL: {exc}") from exc
        return values


class DiscoveryProfilesValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profiles: list[DiscoveryProfile] = Field(max_length=30)

    @model_validator(mode="after")
    def bounded_unique_profiles(self):
        if len({profile.id for profile in self.profiles}) != len(self.profiles):
            raise ValueError("profile ids must be unique")
        if sum(len(profile.seed_urls) for profile in self.profiles) > 200:
            raise ValueError("at most 200 seed URLs may be configured")
        return self


class AppSetting(BaseModel):
    key: str
    value: dict[str, Any]
    version: int = Field(ge=0)
    updated_at: str | None = None


def default_discovery_profiles() -> DiscoveryProfilesValue:
    entries = [
        ("tech", "科技", "technology", ["https://technews.tw/", "https://www.inside.com.tw/"]),
        ("business", "商業", "business", ["https://www.managertoday.com.tw/"]),
        ("science", "科學", "science", ["https://pansci.asia/"]),
        ("society", "社會", "society", ["https://www.twreporter.org/"]),
        ("culture", "文化", "culture", ["https://www.openbook.org.tw/", "https://storystudio.tw/"]),
        ("environment", "環境", "environment", ["https://e-info.org.tw/"]),
        ("life", "生活", "life", ["https://icook.tw/"]),
    ]
    return DiscoveryProfilesValue(profiles=[
        DiscoveryProfile(id=id, name=name, language="zh-tw", category=category,
                         enabled=True, quota=1, seed_urls=urls)
        for id, name, category, urls in entries
    ])


async def _prepare_discovery_seeds(value: BaseModel) -> list[dict[str, str]]:
    if not isinstance(value, DiscoveryProfilesValue):
        raise TypeError("discovery seed preparation requires discovery profiles")
    # Disabled profiles retain validated syntax but never resolve DNS. An
    # offline source must not prevent an operator from turning its crawl off.
    urls = list(dict.fromkeys(raw for profile in value.profiles if profile.enabled
                             for raw in profile.seed_urls))
    semaphore = asyncio.Semaphore(SEED_VALIDATION_CONCURRENCY)

    async def validate_seed(raw: str) -> dict[str, str]:
        async with semaphore:
            safe_url = await validate_fetch_url(raw)
            host = normalize_host(safe_url)
            if not host or is_denied_host(host):
                raise DiscoveryError("seed host is unusable or on the discovery denylist")
            return {"url": origin_of(safe_url), "host": host}

    async def validate_batch() -> list[dict[str, str]]:
        tasks = [asyncio.create_task(validate_seed(raw)) for raw in urls]
        try:
            return await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        validated = await asyncio.wait_for(validate_batch(), SEED_VALIDATION_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise DiscoveryError("Seed validation timed out; retry or reduce enabled seed URLs") from exc
    seeds = {}
    for seed in validated:
        seeds.setdefault(seed["host"], seed)
    return list(seeds.values())


@dataclass(frozen=True)
class SettingDefinition:
    schema: type[BaseModel]
    default_factory: Callable[[], BaseModel]
    prepare_seeds: Callable[[BaseModel], Awaitable[list[dict[str, str]]]] | None = None


SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    DISCOVERY_PROFILES_KEY: SettingDefinition(
        DiscoveryProfilesValue, default_discovery_profiles, _prepare_discovery_seeds,
    ),
}


def validate_setting_value(key: str, value: Any) -> BaseModel:
    if key not in SETTING_DEFINITIONS:
        raise KeyError(key)
    return SETTING_DEFINITIONS[key].schema.model_validate(value)


def get_setting(db, key: str) -> AppSetting:
    if key not in SETTING_DEFINITIONS:
        raise KeyError(key)
    result = db.table("app_settings").select("*").eq("key", key).maybe_single().execute()
    row = getattr(result, "data", None) if result is not None else None
    if not row:
        default_factory = SETTING_DEFINITIONS[key].default_factory
        return AppSetting(key=key, value=default_factory().model_dump(), version=0)
    setting = AppSetting.model_validate(row)
    if setting.key != key or setting.version < 1:
        raise ValueError("stored settings key or version is invalid")
    setting.value = validate_setting_value(key, setting.value).model_dump()
    return setting


def list_settings(db) -> list[AppSetting]:
    return [get_setting(db, key) for key in SETTING_DEFINITIONS]


def load_discovery_profiles(db) -> list[DiscoveryProfile]:
    return DiscoveryProfilesValue.model_validate(
        get_setting(db, DISCOVERY_PROFILES_KEY).value,
    ).profiles


async def save_setting(db, key: str, value: Any, expected_version: int) -> AppSetting | None:
    parsed = validate_setting_value(key, value)
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
        raise ValueError("expected_version must be a nonnegative integer")
    prepare_seeds = SETTING_DEFINITIONS[key].prepare_seeds
    seeds = await prepare_seeds(parsed) if prepare_seeds else []
    params = {
        "p_key": key, "p_value": parsed.model_dump(),
        "p_expected_version": expected_version, "p_seeds": seeds,
    }
    result = await asyncio.to_thread(lambda: db.rpc("save_app_setting", params).execute())
    data = getattr(result, "data", None)
    if not data:
        return None
    row = data[0] if isinstance(data, list) and len(data) == 1 else data
    try:
        saved = AppSetting.model_validate(row)
        if saved.key != key or saved.version != expected_version + 1:
            raise ValueError("setting RPC returned inconsistent key or version")
        validate_setting_value(saved.key, saved.value)
    except ValueError as exc:
        raise RuntimeError("invalid setting response from database") from exc
    return saved
