"""Environment-variable parsing shared by the two worker loops.

The bodies here started life as services/feed_refresh.py::_env_int. The discovery
loop needs the same "parse, validate, warn, fall back" behavior for ints, floats
and booleans, and duplicating it is how the two loops would drift in how they
treat a typo'd value. feed_refresh keeps its own `_env_int` / `refresh_enabled`
names as thin delegates so its callers and tests are untouched.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Values that mean "off". Anything else (including a bare word like "yes") reads
# as on — matching how FEED_REFRESH_ENABLED has always behaved.
_FALSEY = ("0", "false", "no", "off")


def _raw(name: str) -> str | None:
    """The variable's value, or None when unset *or* set to whitespace only.

    Treating "" as unset is deliberate: `FOO=` in a .env file (or an unset
    compose interpolation) should mean "use the default", not "parse the empty
    string".
    """
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """An int from the environment, clamped to `>= minimum` by falling back.

    `minimum` exists because not every knob's floor is 1:
    FEED_DISCOVERY_AUTO_PROMOTE_MIN_REFERRERS uses 0 as a *meaningful* value
    ("never auto-promote"), and a hardcoded `< 1 → default` would silently turn
    that opt-out back into the default and enable auto-promotion.
    """
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer — using default %d", name, raw, default)
        return default
    if value < minimum:
        logger.warning(
            "%s=%d is below %d — using default %d", name, value, minimum, default
        )
        return default
    return value


def env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """A float from the environment, clamped to `>= minimum` by falling back."""
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using default %s", name, raw, default)
        return default
    if value < minimum:
        logger.warning(
            "%s=%s is below %s — using default %s", name, value, minimum, default
        )
        return default
    return value


def env_flag(name: str, default: bool) -> bool:
    """A boolean from the environment. Unset or blank yields `default`."""
    raw = _raw(name)
    if raw is None:
        return default
    return raw.lower() not in _FALSEY
