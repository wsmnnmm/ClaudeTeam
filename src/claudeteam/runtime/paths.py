"""Single source of truth for runtime filesystem paths.

All paths derive from `$CLAUDETEAM_STATE_DIR` (re-read on every call so
tests get isolation by setting the env, not by monkey-patching).  When
not set, falls back to a `state/` dir beside the config file — the config's
location is the team's identity, so two teams never share one global dir.

Layout:
    $CLAUDETEAM_STATE_DIR/
        facts/             ← inbox.json, status.json, logs.jsonl, heartbeats.json
        share/             ← team-shared experience (experience.jsonl)
        agents/<name>/     ← one dir per agent:
                               identity.md, memory.jsonl
                               workspace/  ← private scratch / long reports
                               home/       ← the CLI's HOME (.claude / .codex / ...)
        router.pid         ← daemon pid files
        watchdog.pid
        router.cursor      ← catchup replay state
"""
from __future__ import annotations

import os
from pathlib import Path

from claudeteam.util import env_path, env_str


def state_dir() -> Path:
    """Top-level directory for all runtime state.

    Resolution order:
    1. $CLAUDETEAM_STATE_DIR env var
    2. ``state_dir`` key from ``./claudeteam.toml`` (so each team repo can
       isolate its own router, watchdog, and runtime state)
    3. ``state/`` beside the config file path
    4. ``~/.claudeteam`` (legacy single-team fallback)
    """
    env = env_path("CLAUDETEAM_STATE_DIR")
    if env:
        return env
    try:
        import tomllib
        candidates: list[Path] = []
        explicit = env_path("CLAUDETEAM_CONFIG_FILE")
        if explicit:
            candidates.append(explicit)
        cwd_toml = Path("claudeteam.toml")
        if cwd_toml.exists():
            candidates.append(cwd_toml)
        seen: set[Path] = set()
        for cfg in candidates:
            if cfg in seen:
                continue
            seen.add(cfg)
            raw_cfg = cfg.expanduser()
            if not raw_cfg.exists():
                return raw_cfg.parent / "state"
            resolved_cfg = raw_cfg.resolve()
            data = tomllib.loads(resolved_cfg.read_text(encoding="utf-8"))
            configured = data.get("state_dir")
            if configured:
                path = Path(os.path.expandvars(str(configured))).expanduser()
                if not path.is_absolute():
                    path = resolved_cfg.parent / path
                return path
            return resolved_cfg.parent / "state"
    except Exception:
        pass
    return Path.home() / ".claudeteam"


def facts_dir() -> Path:
    """Where local_facts stores inbox / status / log / heartbeats."""
    return state_dir() / "facts"


def agent_dir(agent: str) -> Path:
    """Root of one agent's own space.

    Holds `identity.md` + `memory.jsonl`, the `workspace/` scratch dir, and
    the `home/` subdir (the CLI's HOME, where claude looks for ~/.claude —
    see `agent_home` below). CLI-agnostic: the same location
    for every agent regardless of which CLI it runs under. The native
    CLAUDE.md under `home/` is a projection of the identity/memory kept
    here."""
    return state_dir() / "agents" / agent


def agent_workspace(agent: str) -> Path:
    """Per-agent private scratch area (drafts, long reports, temp files).

    Agents collaborate in the shared project repo (the pane's cwd); this
    is the one directory each agent owns, so long content / scratch doesn't
    collide across panes in the repo root."""
    return agent_dir(agent) / "workspace"


def agent_home(agent: str) -> str:
    """Per-agent HOME — the `home/` subdir of the agent's own state dir.

    Returns a str (it gets spliced into shell spawn commands). Defaults to
    `<state_dir>/agents/<agent>/home`, so each agent's CLI dotfiles
    (`.claude` / `.codex` / `.gemini` / ...) sit beside its `identity.md` +
    `memory.jsonl` — one directory per agent. Set `CLAUDETEAM_AGENT_HOME_ROOT`
    to relocate the homes onto a separate mount (e.g. a Docker volume that
    persists creds across image rebuilds, or a writable path on macOS where
    `~` is a read-only firmlink); the home is then `<root>/<agent>`.
    """
    root = env_str("CLAUDETEAM_AGENT_HOME_ROOT")
    if root:
        return str(Path(root) / agent)
    return str(agent_dir(agent) / "home")


def share_dir() -> Path:
    """Team-shared knowledge space: durable experience the whole team reads
    and writes (distinct from `facts/`, which is live coordination state)."""
    return state_dir() / "share"


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
