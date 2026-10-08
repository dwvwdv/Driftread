import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from services.feed_discovery import DiscoveryError
from services.settings import (
    DISCOVERY_PROFILES_KEY, DiscoveryProfilesValue,
    get_setting, load_discovery_profiles, save_setting,
)
from tests.discovery_fakes import FakeDB


def value():
    return {"profiles": [{"id": "tech", "name": "Technology", "language": "zh-TW",
                          "category": "technology", "enabled": True, "quota": 2,
                          "seed_urls": ["https://pansci.asia/science"]}]}


def test_defaults_balanced_and_only_missing_row_gets_version_zero():
    db = FakeDB(app_settings=[])
    setting = get_setting(db, DISCOVERY_PROFILES_KEY)
    profiles = load_discovery_profiles(db)
    assert setting.version == 0 and setting.updated_at is None
    assert len(profiles) == 7
    assert len({profile.category for profile in profiles}) == 7
    assert sum(len(profile.seed_urls) for profile in profiles) == 9
    assert all(profile.enabled and profile.quota == 1 for profile in profiles)


def test_empty_profiles_is_persisted_disable_not_default():
    db = FakeDB(app_settings=[{"key": DISCOVERY_PROFILES_KEY, "value": {"profiles": []},
                               "version": 3, "updated_at": "2026-01-01"}])
    assert load_discovery_profiles(db) == []


def test_database_errors_and_invalid_stored_rows_do_not_restore_defaults():
    db = MagicMock()
    db.table.side_effect = RuntimeError("settings table unavailable")
    with pytest.raises(RuntimeError):
        load_discovery_profiles(db)
    with pytest.raises(ValidationError):
        load_discovery_profiles(FakeDB(app_settings=[{
            "key": DISCOVERY_PROFILES_KEY, "value": {"profiles": "bad"}, "version": 1,
        }]))


@pytest.mark.parametrize("field,bad", [
    ("id", "../bad"), ("id", "x" * 65), ("name", " "),
    ("language", "zh/evil"), ("language", "x"), ("category", "x" * 101),
    ("language", "abcdef"), ("language", "zh-a"),
    ("enabled", "false"), ("quota", True), ("quota", -1), ("quota", 101),
    ("seed_urls", ["javascript:alert(1)"]),
    ("seed_urls", ["https://user:pass@example.org/"]),
    ("seed_urls", ["https://exa\tmple.org/"]),
    ("seed_urls", ["https://-bad.org/"]),
    ("seed_urls", ["https://bad..org/"]),
    ("seed_urls", ["https://example.org:99999/"]),
    ("seed_urls", ["https://中文.tw/"]),
    ("seed_urls", ["https://example.org/"] * 31),
])
def test_profiles_validate_before_network(field, bad):
    data = value()
    data["profiles"][0][field] = bad
    with pytest.raises(ValidationError):
        DiscoveryProfilesValue.model_validate(data)


def test_aggregate_bounds_and_duplicate_ids():
    profile = value()["profiles"][0]
    with pytest.raises(ValidationError):
        DiscoveryProfilesValue.model_validate({"profiles": [profile, deepcopy(profile)]})
    with pytest.raises(ValidationError):
        DiscoveryProfilesValue.model_validate({"profiles": [dict(profile, id=f"p{i}")
                                                            for i in range(31)]})
    with pytest.raises(ValidationError):
        DiscoveryProfilesValue.model_validate({"profiles": [
            dict(profile, id=f"p{i}", seed_urls=["https://example.org/"] * 30)
            for i in range(7)
        ]})


@pytest.mark.asyncio
async def test_save_validates_enabled_urls_then_atomic_rpc(monkeypatch):
    data = value()
    data["profiles"].append(dict(data["profiles"][0], id="disabled", enabled=False,
                                 seed_urls=["https://openbook.org.tw/"]))
    validator = AsyncMock(side_effect=lambda url: url)
    monkeypatch.setattr("services.settings.validate_fetch_url", validator)
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "key": DISCOVERY_PROFILES_KEY,
        "value": DiscoveryProfilesValue.model_validate(data).model_dump(), "version": 2,
        "updated_at": "2026-01-01",
    }
    result = await save_setting(db, DISCOVERY_PROFILES_KEY, data, 1)
    assert result.version == 2
    assert validator.await_count == 1
    name, params = db.rpc.call_args.args
    assert name == "save_app_setting"
    assert params["p_expected_version"] == 1
    assert params["p_seeds"] == [{"url": "https://pansci.asia/", "host": "pansci.asia"}]
    assert params["p_value"]["profiles"][0]["language"] == "zh-tw"
    assert params["p_value"]["profiles"][0]["seed_urls"] == ["https://pansci.asia/science"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["http://127.0.0.1/", "https://twitter.com/"])
async def test_unsafe_seeds_prevent_all_mutation(monkeypatch, unsafe):
    validator = AsyncMock(side_effect=(DiscoveryError("private host")
                                      if "127.0.0.1" in unsafe else lambda url: url))
    monkeypatch.setattr("services.settings.validate_fetch_url", validator)
    data = value()
    data["profiles"][0]["seed_urls"] = [unsafe]
    db = MagicMock()
    with pytest.raises(DiscoveryError):
        await save_setting(db, DISCOVERY_PROFILES_KEY, data, 0)
    db.rpc.assert_not_called()
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_optimistic_conflict_is_not_success_and_unknown_keys_rejected(monkeypatch):
    monkeypatch.setattr("services.settings.validate_fetch_url", AsyncMock(side_effect=lambda url: url))
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = None
    assert await save_setting(db, DISCOVERY_PROFILES_KEY, value(), 4) is None
    db.reset_mock()
    with pytest.raises(KeyError):
        await save_setting(db, "unregistered", {}, 0)
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_offline_profile_can_be_disabled_without_dns(monkeypatch):
    data = value()
    data["profiles"][0]["enabled"] = False
    validator = AsyncMock(side_effect=DiscoveryError("DNS resolution failed"))
    monkeypatch.setattr("services.settings.validate_fetch_url", validator)
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "key": DISCOVERY_PROFILES_KEY,
        "value": DiscoveryProfilesValue.model_validate(data).model_dump(), "version": 2,
    }
    saved = await save_setting(db, DISCOVERY_PROFILES_KEY, data, 1)
    assert saved.value["profiles"][0]["enabled"] is False
    validator.assert_not_awaited()
    assert db.rpc.call_args.args[1]["p_seeds"] == []


@pytest.mark.asyncio
async def test_enabled_seed_validation_deduplicates_urls_and_bounds_concurrency(monkeypatch):
    data = value()
    urls = [f"https://site{i}.org/" for i in range(8)]
    data["profiles"][0]["seed_urls"] = urls + urls[:2]
    data["profiles"].append(dict(data["profiles"][0], id="other"))
    active = peak = 0
    visited = []
    ready = asyncio.Event()

    async def validator(url):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        visited.append(url)
        if active == 4:
            ready.set()
        try:
            await ready.wait()
            await asyncio.sleep(0)
            return url
        finally:
            active -= 1

    monkeypatch.setattr("services.settings.validate_fetch_url", validator)
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "key": DISCOVERY_PROFILES_KEY,
        "value": DiscoveryProfilesValue.model_validate(data).model_dump(), "version": 1,
    }
    await save_setting(db, DISCOVERY_PROFILES_KEY, data, 0)
    assert peak == 4 and active == 0
    assert sorted(visited) == sorted(urls)
    assert len(db.rpc.call_args.args[1]["p_seeds"]) == 8


@pytest.mark.asyncio
async def test_batch_timeout_cancels_validation_and_does_not_save(monkeypatch):
    monkeypatch.setattr("services.settings.SEED_VALIDATION_TIMEOUT_SECONDS", 0.01)
    waiting = asyncio.Event()
    active = 0

    async def validator(url):
        nonlocal active
        active += 1
        try:
            await waiting.wait()
        finally:
            active -= 1

    monkeypatch.setattr("services.settings.validate_fetch_url", validator)
    db = MagicMock()
    with pytest.raises(DiscoveryError, match="timed out"):
        await save_setting(db, DISCOVERY_PROFILES_KEY, value(), 0)
    assert active == 0
    db.rpc.assert_not_called()
