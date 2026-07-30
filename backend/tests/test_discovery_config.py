from __future__ import annotations

import pytest

from env_utils import env_flag, env_float, env_int
from services import discovery_config as cfg


# ── env_utils ────────────────────────────────────────────────────────────────

def test_env_int_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("DRIFTREAD_TEST_INT", raising=False)
    assert env_int("DRIFTREAD_TEST_INT", 7) == 7


@pytest.mark.parametrize("raw", ["", "   ", "abc", "1.5", "12x"])
def test_env_int_falls_back_on_garbage(monkeypatch, raw):
    monkeypatch.setenv("DRIFTREAD_TEST_INT", raw)
    assert env_int("DRIFTREAD_TEST_INT", 7) == 7


def test_env_int_reads_a_valid_value(monkeypatch):
    monkeypatch.setenv("DRIFTREAD_TEST_INT", " 42 ")
    assert env_int("DRIFTREAD_TEST_INT", 7) == 42


def test_env_int_default_minimum_rejects_zero_and_negatives(monkeypatch):
    monkeypatch.setenv("DRIFTREAD_TEST_INT", "0")
    assert env_int("DRIFTREAD_TEST_INT", 7) == 7
    monkeypatch.setenv("DRIFTREAD_TEST_INT", "-3")
    assert env_int("DRIFTREAD_TEST_INT", 7) == 7


def test_env_int_minimum_zero_accepts_zero(monkeypatch):
    """The whole reason env_int takes `minimum`. See the next test for why it
    matters in practice."""
    monkeypatch.setenv("DRIFTREAD_TEST_INT", "0")
    assert env_int("DRIFTREAD_TEST_INT", 5, minimum=0) == 0
    monkeypatch.setenv("DRIFTREAD_TEST_INT", "-1")
    assert env_int("DRIFTREAD_TEST_INT", 5, minimum=0) == 5


@pytest.mark.parametrize(
    "raw,expected",
    [("0", False), ("false", False), ("FALSE", False), ("No", False),
     ("off", False), (" off ", False), ("true", True), ("1", True),
     ("yes", True), ("anything", True)],
)
def test_env_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("DRIFTREAD_TEST_FLAG", raw)
    assert env_flag("DRIFTREAD_TEST_FLAG", True) is expected
    assert env_flag("DRIFTREAD_TEST_FLAG", False) is expected


@pytest.mark.parametrize("raw", ["", "   "])
def test_env_flag_blank_is_treated_as_unset(monkeypatch, raw):
    """`FOO=` in a .env file, or an unset compose interpolation, means "use the
    default" — not "parse the empty string"."""
    monkeypatch.setenv("DRIFTREAD_TEST_FLAG", raw)
    assert env_flag("DRIFTREAD_TEST_FLAG", True) is True
    assert env_flag("DRIFTREAD_TEST_FLAG", False) is False


def test_env_float_parses_and_clamps(monkeypatch):
    monkeypatch.setenv("DRIFTREAD_TEST_FLOAT", "2.5")
    assert env_float("DRIFTREAD_TEST_FLOAT", 1.0) == 2.5
    monkeypatch.setenv("DRIFTREAD_TEST_FLOAT", "-1")
    assert env_float("DRIFTREAD_TEST_FLOAT", 1.0) == 1.0
    monkeypatch.setenv("DRIFTREAD_TEST_FLOAT", "nonsense")
    assert env_float("DRIFTREAD_TEST_FLOAT", 1.0) == 1.0


def test_env_float_accepts_zero_by_default(monkeypatch):
    """minimum defaults to 0.0 for floats — a zero politeness delay is a
    legitimate setting, unlike a zero batch size."""
    monkeypatch.setenv("DRIFTREAD_TEST_FLOAT", "0")
    assert env_float("DRIFTREAD_TEST_FLOAT", 1.0) == 0.0


# ── discovery_config defaults ────────────────────────────────────────────────

def _clear(monkeypatch) -> None:
    for name in (
        "FEED_DISCOVERY_ENABLED", "FEED_DISCOVERY_TICK_SECONDS",
        "FEED_DISCOVERY_HARVEST_BATCH_SIZE", "FEED_DISCOVERY_HARVEST_ARTICLES",
        "FEED_DISCOVERY_HARVEST_INTERVAL_HOURS",
        "FEED_DISCOVERY_HARVEST_MAX_LINKS_PER_FEED",
        "FEED_DISCOVERY_BLOGROLL_ENABLED", "FEED_DISCOVERY_DIRECTORY_ENABLED",
        "FEED_DISCOVERY_DIRECTORY_BATCH_SIZE", "FEED_DISCOVERY_PROBE_BATCH_SIZE",
        "FEED_DISCOVERY_PROBE_CONCURRENCY", "FEED_DISCOVERY_PROBE_MAX_ATTEMPTS",
        "FEED_DISCOVERY_PROBE_RETRY_HOURS", "FEED_DISCOVERY_HOST_DELAY_SECONDS",
        "FEED_DISCOVERY_RESPECT_ROBOTS", "FEED_DISCOVERY_MAX_FRONTIER_SIZE",
        "FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_discovery_ships_disabled(monkeypatch):
    """The one default that must never drift: pulling a new image cannot silently
    turn a deployment into a crawler."""
    _clear(monkeypatch)
    assert cfg.discovery_enabled() is False
    assert cfg.blogroll_enabled() is False
    assert cfg.directory_enabled() is False


def test_robots_defaults_on(monkeypatch):
    _clear(monkeypatch)
    assert cfg.respect_robots() is True


def test_numeric_defaults(monkeypatch):
    _clear(monkeypatch)
    assert cfg.tick_seconds() == 900
    assert cfg.harvest_batch_size() == 10
    assert cfg.harvest_articles_per_feed() == 20
    assert cfg.harvest_interval_hours() == 168
    assert cfg.harvest_max_links_per_feed() == 200
    assert cfg.directory_batch_size() == 3
    assert cfg.probe_batch_size() == 20
    assert cfg.probe_concurrency() == 3
    assert cfg.probe_max_attempts() == 3
    assert cfg.probe_retry_hours() == 24
    assert cfg.host_delay_seconds() == 2.0
    assert cfg.max_frontier_size() == 50_000


def test_auto_promote_defaults_to_off(monkeypatch):
    _clear(monkeypatch)
    assert cfg.auto_promote_min_referrers() == 0


def test_auto_promote_explicit_zero_stays_zero(monkeypatch):
    """The single most dangerous copy-paste in this feature: reusing the
    FEED_REFRESH_* style `value < 1 -> default` guard here would turn an explicit
    opt-out into the default and silently enable auto-promotion. 0 is the
    default too, so assert against a non-zero default as well."""
    monkeypatch.setenv("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", "0")
    assert cfg.auto_promote_min_referrers() == 0
    assert env_int("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", 3, minimum=0) == 0


def test_auto_promote_reads_a_threshold(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS", "3")
    assert cfg.auto_promote_min_referrers() == 3


def test_env_overrides_are_read_per_call(monkeypatch):
    """Accessors must not cache at import time — a compose restart retunes the
    crawler without a code change."""
    _clear(monkeypatch)
    assert cfg.probe_concurrency() == 3
    monkeypatch.setenv("FEED_DISCOVERY_PROBE_CONCURRENCY", "8")
    assert cfg.probe_concurrency() == 8


def test_host_delay_accepts_fractional_seconds(monkeypatch):
    monkeypatch.setenv("FEED_DISCOVERY_HOST_DELAY_SECONDS", "0.5")
    assert cfg.host_delay_seconds() == 0.5
