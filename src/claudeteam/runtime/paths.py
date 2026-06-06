"""Single source of truth for runtime filesystem paths.

All paths derive from `$CLAUDETEAM_STATE_DIR` (re-read on every call so
tests get isolation by setting the env, not by monkey-patching).  When
not set, falls back to `~/.claudeteam`.

Layout:
    $CLAUDETEAM_STATE_DIR/
        facts/             ← inbox.json, status.json, logs.jsonl, heartbeats.json
        agents/<name>/     ← per-agent identity.md
        router.pid         ← daemon pid files
        watchdog.pid
        router.cursor      ← catchup replay state
"""
from __future__ import annotations

import os
from pathlib import Path

from claudeteam.util import env_path


def state_dir() -> Path:
    """Top-level directory for all runtime state.

    Resolution order:
    1. $CLAUDETEAM_STATE_DIR env var
    2. ``state_dir`` key from ``./claudeteam.toml`` (so each team repo can
       isolate its own router, watchdog, and runtime state)
    3. ``~/.claudeteam`` (legacy single-team fallback)
    """
    env = env_path("CLAUDETEAM_STATE_DIR")
    if env:
        return env
    try:
        import tomllib
        candidates: list[Path] = []
        explicit = env_path("CLAUDETEAM_CONFIG_FILE")
        if explicit and explicit.exists():
            candidates.append(explicit)
        cwd_toml = Path("claudeteam.toml")
        if cwd_toml.exists():
            candidates.append(cwd_toml)
        seen: set[Path] = set()
        for cfg in candidates:
            if cfg in seen:
                continue
            seen.add(cfg)
            resolved_cfg = cfg.resolve()
            data = tomllib.loads(resolved_cfg.read_text(encoding="utf-8"))
            configured = data.get("state_dir")
            if configured:
                path = Path(os.path.expandvars(str(configured))).expanduser()
                if not path.is_absolute():
                    path = resolved_cfg.parent / path
                return path
    except Exception:
        pass
    return Path.home() / ".claudeteam"


def facts_dir() -> Path:
    """Where local_facts stores inbox / status / log / heartbeats."""
    return state_dir() / "facts"


def state_file(name: str) -> Path:
    """A file under state_dir. Caller is responsible for mkdir before writing
    — pure path resolution, no I/O side effects."""
    return state_dir() / name


def router_pid_file() -> Path:
    return state_file("router.pid")


def router_cursor_file() -> Path:
    return state_file("router.cursor")


def router_log_file() -> Path:
    return state_file("router.log")


def router_seen_file() -> Path:
    return state_file("router.seen")


def config_file() -> Path:
    """Path to the unified TOML config file (replaces team.json +
    runtime_config.json). Override via CLAUDETEAM_CONFIG_FILE env, else
    uses the state-scoped config pointer / inferred team root before
    falling back to `./claudeteam.toml` relative to cwd.

    If an agent pane inherited only CLAUDETEAM_STATE_DIR, prefer the
    config path recorded in that state dir, then infer the team root from
    the common `/team/state` layout. This keeps shell-outs such as
    `claudeteam say` pointed at the right Feishu chat even when the pane's
    cwd drifted to another repo that also has a `claudeteam.toml`.
    """
    from claudeteam.util import env_path
    explicit = env_path("CLAUDETEAM_CONFIG_FILE")
    if explicit:
        return explicit
    state = env_path("CLAUDETEAM_STATE_DIR")
    if state:
        pointer = state / "config-file.path"
        try:
            recorded = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded:
            return Path(recorded).expanduser()
    if state and state.name == "state":
        inferred = state.parent / "claudeteam.toml"
        if inferred.exists():
            return inferred
    cwd_config = Path.cwd() / "claudeteam.toml"
    if cwd_config.exists():
        return cwd_config
    return cwd_config


def watchdog_pid_file() -> Path:
    return state_file("watchdog.pid")


def watchdog_log_file() -> Path:
    return state_file("watchdog.log")


def codex_home_dir(agent: str | None = None) -> Path:
    """Project-scoped Codex home used by codex-cli workers.

    When multiple codex agents run in the same team, each gets its own
    isolated Codex home so provider config, auth and reasoning effort do
    not overwrite each other.
    """
    if agent:
        return state_dir() / "codex-home" / agent
    return state_dir() / "codex-home"


def codex_config_file(agent: str | None = None) -> Path:
    """Project-scoped Codex config file."""
    return codex_home_dir(agent) / "config.toml"


def codex_auth_file(agent: str | None = None) -> Path:
    """Project-scoped Codex auth file."""
    return codex_home_dir(agent) / "auth.json"


def ensure_state_dir() -> Path:
    """Create state_dir if missing and return it. Use when about to write."""
    sd = state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    return sd
