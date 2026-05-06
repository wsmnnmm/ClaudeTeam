"""Read tunable parameters from claudeteam.toml with env-var override.

Cascade priority for every field:

  1. environment variable (e.g. `CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S`)
  2. corresponding entry in `claudeteam.toml`  (resolved via `paths.config_file()`)
  3. caller-supplied `default` value

Tests inject `_load_toml_for_test()` to bypass disk; the production path
caches per (file, mtime) so consecutive `tunable()` calls in a hot loop
don't re-read disk.

Why a new module instead of folding into `runtime/config.py`:
  - config.py is the team.json / runtime_config.json reader (legacy json).
    tunables.py is the new parameterization layer that reads claudeteam.toml.
  - Once everything migrates, tunables can absorb config.py. Until then,
    keeping them separate avoids cross-coupling old paths into the new lookup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Iterable

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover — project pins >=3.10 but stdlib tomllib is 3.11+
    import tomli as tomllib  # type: ignore[no-redef]

from claudeteam.runtime import paths


# Env override naming: dotted path "router.stale_event_threshold_s"
# → env "CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S"
def _env_var_for(dotted_path: str) -> str:
    return "CLAUDETEAM_" + dotted_path.upper().replace(".", "_")


# ── toml load with mtime cache ───────────────────────────────────


_CACHE: dict[Path, tuple[float, dict]] = {}


def _load_toml() -> dict:
    """Read `claudeteam.toml` from `paths.config_file()`. Returns `{}` if
    the file is missing. Cached per file mtime so hot-loop callers don't
    pound the disk.
    """
    path = paths.config_file()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {}
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    _CACHE[path] = (mtime, data)
    return data


def _navigate(data: dict, dotted_path: str) -> Any:
    """Walk `data` along the dotted path. Return None if any segment is
    missing or non-dict."""
    cur: Any = data
    for seg in dotted_path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
        if cur is None:
            return None
    return cur


# ── type coercion for env values ──────────────────────────────────


def _coerce(value: str, target_type: type) -> Any:
    """Coerce env-var string into the type of `default`. On failure
    (e.g. CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S=potato but field is
    float), return None so caller falls back to default."""
    if target_type is bool:
        if value.lower() in {"1", "true", "yes", "on"}:
            return True
        if value.lower() in {"0", "false", "no", "off"}:
            return False
        return None
    if target_type is int:
        try:
            return int(value)
        except ValueError:
            return None
    if target_type is float:
        try:
            return float(value)
        except ValueError:
            return None
    if target_type is list:
        # comma-separated env var → list[str]; trim each item
        return [s.strip() for s in value.split(",") if s.strip()]
    if target_type is str:
        return value
    return None  # unsupported type — fall back


# ── public API ────────────────────────────────────────────────────


def tunable(dotted_path: str, default: Any) -> Any:
    """Resolve `dotted_path` as a tunable.

    Priority: env var > claudeteam.toml > default.

    Args:
        dotted_path: e.g. "router.stale_event_threshold_s" — segments
            map to nested toml tables.
        default: fallback value AND the type oracle. The env-var coercer
            uses `type(default)` to know what to parse the string into;
            unrecognised types pass the string through unchanged.

    Returns the resolved value. Never raises — bad input falls back to
    `default` so a bad config can't take down the daemon.
    """
    env_val = os.environ.get(_env_var_for(dotted_path))
    if env_val is not None and env_val != "":
        coerced = _coerce(env_val, type(default))
        if coerced is not None:
            return coerced
        # bad env value — fall through to toml/default

    toml_val = _navigate(_load_toml(), dotted_path)
    if toml_val is not None:
        return toml_val

    return default


def reset_cache() -> None:
    """Clear the toml load cache. Tests call this between cases that
    munge the config file."""
    _CACHE.clear()


def load() -> dict:
    """Public accessor for the parsed claudeteam.toml. Returns `{}` if
    the file is missing or malformed. Cached per mtime."""
    return _load_toml()
