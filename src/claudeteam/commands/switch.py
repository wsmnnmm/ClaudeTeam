"""`claudeteam switch <team-dir>` — print shell exports for a team directory.

Multi-team isolation today is env-var-based: a deployment is whichever
`team.json` + `runtime_config.json` + `CLAUDETEAM_STATE_DIR` the current
shell sees. Switching teams means re-exporting those three vars.

This command emits ready-to-eval export lines so the operator runs:

    eval "$(claudeteam switch ~/teams/projectA)"

The directory layout this assumes (created either by `claudeteam init`
in that dir or by hand) is:

    <team-dir>/
        team.json
        runtime_config.json
        state/                # auto-created when claudeteam writes anything

`team.json` is the marker file — switch refuses to point at a directory
without one, so a typo doesn't silently succeed.

With no argument, prints the current active team (resolved from env
vars) so an operator can confirm what they're pointing at without
greping their shell history.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from claudeteam.runtime import config, paths
from claudeteam.util import (
    atomic_write_text,
    env_str,
    error_exit,
    maybe_print_help,
    pop_bool_flag,
    pop_flag,
    reject_extra_args,
    write_json,
)


USAGE = (
    "usage: claudeteam switch [<team-dir>]\n"
    "       claudeteam switch model [--model <name>] [--base-url <url>]\n"
    "                               [--auth-token <token>] [--haiku-model <name>]\n"
    "                               [--sonnet-model <name>] [--opus-model <name>]\n"
    "                               [--effort <level>]\n"
    "       claudeteam switch model preset [--save <name> | --use <name> | --list]\n"
    "                                      [--model <name>] [--base-url <url>]\n"
    "                                      [--auth-token <token>] [--haiku-model <name>]\n"
    "                                      [--sonnet-model <name>] [--opus-model <name>]\n"
    "                                      [--effort <level>]\n"
    "  no arg          — print the current active team\n"
    "  <team-dir>      — print exports; wrap in `eval \"$(...)\"` to apply\n"
    "  model           — show or update project-local Claude Code model routing"
)


_PROVIDER_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
)

_ALIAS_ENV_KEY = {
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
}

_MANAGED_PROVIDER_ENV = "claudeteam-provider.env"
_PRESETS_FILE = "provider-presets.json"


def _provider_env_dir() -> Path:
    return Path.cwd() / ".env.local.d"


def _presets_path() -> Path:
    return paths.state_file(_PRESETS_FILE)


def _looks_like_provider_env(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(f"{key}=" in text for key in _PROVIDER_ENV_KEYS)


def _provider_env_candidates() -> list[Path]:
    env_dir = _provider_env_dir()
    try:
        files = sorted(env_dir.glob("*.env"))
    except OSError:
        return []
    return [
        path for path in files
        if path.name.startswith("claudeteam-") or _looks_like_provider_env(path)
    ]


def _provider_env_path() -> Path:
    managed = _provider_env_dir() / _MANAGED_PROVIDER_ENV
    if managed.exists():
        return managed
    candidates = _provider_env_candidates()
    if len(candidates) == 1:
        return candidates[0]
    return managed


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip().strip("'\"")
    return out


def _load_provider_state() -> tuple[Path, dict[str, str], str]:
    env_path = _provider_env_path()
    env = _read_env_file(env_path)
    settings = config.load_claude_code_settings()
    raw_env = settings.get("env", {})
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            if key not in env and isinstance(value, str):
                env[key] = value
    effort = ""
    raw_effort = settings.get("effortLevel")
    if isinstance(raw_effort, str):
        effort = raw_effort
    return env_path, env, effort


def _write_provider_env(path: Path, env: dict[str, str]) -> None:
    lines = [
        "# managed by `claudeteam switch model`",
    ]
    for key in _PROVIDER_ENV_KEYS:
        value = env.get(key, "")
        if value:
            lines.append(f"{key}={value}")
    atomic_write_text(path, "\n".join(lines) + "\n")


def _cleanup_duplicate_provider_envs(keep: Path) -> None:
    for path in _provider_env_candidates():
        if path == keep:
            continue
        try:
            path.unlink()
        except OSError:
            pass


def _write_ccswitch(env: dict[str, str], effort: str) -> None:
    path = config.claude_code_settings_file()
    data = config.load_claude_code_settings()
    if not isinstance(data, dict):
        data = {}
    raw_env = data.get("env", {})
    preserved: dict[str, str] = {}
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            if key not in _PROVIDER_ENV_KEYS and isinstance(value, str):
                preserved[key] = value
    for key in _PROVIDER_ENV_KEYS:
        value = env.get(key, "")
        if value:
            preserved[key] = value
    data["env"] = preserved
    if effort:
        data["effortLevel"] = effort
    elif "effortLevel" in data:
        del data["effortLevel"]
    write_json(path, data)


def _effective_model(requested: str, env: dict[str, str]) -> str:
    key = _ALIAS_ENV_KEY.get((requested or "").strip().lower())
    if key and env.get(key):
        return env[key]
    return requested


def _load_presets() -> dict[str, dict[str, str]]:
    path = _presets_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("presets", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, payload in raw.items():
        if not isinstance(name, str) or not isinstance(payload, dict):
            continue
        clean: dict[str, str] = {}
        for key in _PROVIDER_ENV_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                clean[key] = value
        effort = payload.get("effortLevel")
        if isinstance(effort, str) and effort:
            clean["effortLevel"] = effort
        if clean:
            out[name] = clean
    return out


def _write_presets(data: dict[str, dict[str, str]]) -> None:
    path = _presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"presets": data})


def _show_presets() -> int:
    presets = _load_presets()
    if not presets:
        print("presets: (none)")
        print(f"path:    {_presets_path()}")
        return 0
    print(f"path:    {_presets_path()}")
    print("presets:")
    for name in sorted(presets):
        payload = presets[name]
        base = payload.get("ANTHROPIC_BASE_URL", "(unset)")
        model = (
            payload.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
            or payload.get("ANTHROPIC_MODEL")
            or "(unset)"
        )
        effort = payload.get("effortLevel", "(unset)")
        print(f"  - {name}: model={model} base_url={base} effort={effort}")
    return 0


def _resolve_payload_from_flags(*,
                                shared_model: str | None,
                                base_url: str | None,
                                auth_token: str | None,
                                haiku_model: str | None,
                                sonnet_model: str | None,
                                opus_model: str | None,
                                effort: str | None) -> tuple[dict[str, str], str]:
    env: dict[str, str] = {}
    if base_url is not None:
        env["ANTHROPIC_BASE_URL"] = base_url
    if auth_token is not None:
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    if shared_model is not None:
        env["ANTHROPIC_MODEL"] = shared_model
        haiku_model = haiku_model or shared_model
        sonnet_model = sonnet_model or shared_model
        opus_model = opus_model or shared_model
    if haiku_model is not None:
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = haiku_model
    if sonnet_model is not None:
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = sonnet_model
        env["ANTHROPIC_MODEL"] = sonnet_model
    if opus_model is not None:
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = opus_model
    if "ANTHROPIC_MODEL" not in env:
        env["ANTHROPIC_MODEL"] = (
            env.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
            or env.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
            or env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
            or ""
        )
    return env, (effort or "")


def _preset_subcommand(rest: list[str]) -> int:
    shared_model = pop_flag(rest, "--model")
    base_url = pop_flag(rest, "--base-url")
    auth_token = pop_flag(rest, "--auth-token") or pop_flag(rest, "--api-key")
    haiku_model = pop_flag(rest, "--haiku-model")
    sonnet_model = pop_flag(rest, "--sonnet-model")
    opus_model = pop_flag(rest, "--opus-model")
    effort = pop_flag(rest, "--effort")
    save_name = pop_flag(rest, "--save")
    use_name = pop_flag(rest, "--use")
    do_list = pop_bool_flag(rest, "--list")
    if sum(1 for x in (save_name, use_name) if x) + (1 if do_list else 0) > 1:
        return error_exit(f"❌ choose only one of --save / --use / --list\n{USAGE}")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    if do_list or (not save_name and not use_name):
        return _show_presets()

    presets = _load_presets()
    if save_name:
        payload: dict[str, str]
        resolved_effort: str
        if any(v is not None for v in (
                shared_model, base_url, auth_token,
                haiku_model, sonnet_model, opus_model, effort)):
            payload, resolved_effort = _resolve_payload_from_flags(
                shared_model=shared_model,
                base_url=base_url,
                auth_token=auth_token,
                haiku_model=haiku_model,
                sonnet_model=sonnet_model,
                opus_model=opus_model,
                effort=effort,
            )
        else:
            _, env, current_effort = _load_provider_state()
            payload = {k: v for k, v in env.items() if k in _PROVIDER_ENV_KEYS and v}
            resolved_effort = current_effort
        if resolved_effort:
            payload["effortLevel"] = resolved_effort
        if not payload:
            return error_exit("❌ current project-local provider state is empty; nothing to save")
        presets[save_name] = payload
        _write_presets(presets)
        print(f"✅ saved preset: {save_name}")
        print(f"path: {_presets_path()}")
        return 0

    payload = presets.get(use_name or "")
    if payload is None:
        return error_exit(f"❌ no such preset: {use_name}")
    env_path, env, current_effort = _load_provider_state()
    for key in _PROVIDER_ENV_KEYS:
        value = payload.get(key, "")
        if value:
            env[key] = value
    applied_effort = payload.get("effortLevel", current_effort)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    _write_provider_env(env_path, env)
    _cleanup_duplicate_provider_envs(env_path)
    _write_ccswitch(env, applied_effort)
    print(f"✅ applied preset: {use_name}")
    print(f"provider_env: {env_path}")
    print(f"ccswitch:     {config.claude_code_settings_file()}")
    print("hint         run `claudeteam switch model` to verify, then restart the team to apply")
    return 0


def _show_model_state() -> int:
    env_path, env, effort = _load_provider_state()
    print(f"provider_env: {env_path}")
    print(f"ccswitch:     {config.claude_code_settings_file()}")
    print(f"base_url:     {env.get('ANTHROPIC_BASE_URL', '') or '(unset)'}")
    token = env.get("ANTHROPIC_AUTH_TOKEN", "")
    print(f"auth_token:   {'set' if token else '(unset)'}")
    print(f"anthropic:    {env.get('ANTHROPIC_MODEL', '') or '(unset)'}")
    print(f"haiku:        {env.get('ANTHROPIC_DEFAULT_HAIKU_MODEL', '') or '(unset)'}")
    print(f"sonnet:       {env.get('ANTHROPIC_DEFAULT_SONNET_MODEL', '') or '(unset)'}")
    print(f"opus:         {env.get('ANTHROPIC_DEFAULT_OPUS_MODEL', '') or '(unset)'}")
    print(f"effort:       {effort or '(unset)'}")
    print("agents:")
    for agent in config.agent_names():
        requested = config.agent_model(agent)
        effective = _effective_model(requested, env)
        print(f"  - {agent}: requested={requested} effective={effective}")
    return 0


def _apply_model_switch(rest: list[str]) -> int:
    if rest and rest[0] == "preset":
        return _preset_subcommand(rest[1:])
    shared_model = pop_flag(rest, "--model")
    base_url = pop_flag(rest, "--base-url")
    auth_token = pop_flag(rest, "--auth-token") or pop_flag(rest, "--api-key")
    haiku_model = pop_flag(rest, "--haiku-model")
    sonnet_model = pop_flag(rest, "--sonnet-model")
    opus_model = pop_flag(rest, "--opus-model")
    effort = pop_flag(rest, "--effort")
    if pop_bool_flag(rest, "--show"):
        if (rc := reject_extra_args(rest, USAGE)) is not None:
            return rc
        return _show_model_state()
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc

    if not any(v is not None for v in (
            shared_model, base_url, auth_token,
            haiku_model, sonnet_model, opus_model, effort)):
        return _show_model_state()

    env_path, env, current_effort = _load_provider_state()
    payload, resolved_effort = _resolve_payload_from_flags(
        shared_model=shared_model,
        base_url=base_url,
        auth_token=auth_token,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        opus_model=opus_model,
        effort=effort,
    )
    env.update({k: v for k, v in payload.items() if v})
    applied_effort = resolved_effort or current_effort

    env_path.parent.mkdir(parents=True, exist_ok=True)
    _write_provider_env(env_path, env)
    _cleanup_duplicate_provider_envs(env_path)
    _write_ccswitch(env, applied_effort)

    print("✅ project-local model routing updated")
    print(f"provider_env: {env_path}")
    print(f"ccswitch:     {config.claude_code_settings_file()}")
    for label, key in (
        ("base_url", "ANTHROPIC_BASE_URL"),
        ("anthropic", "ANTHROPIC_MODEL"),
        ("haiku", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
        ("sonnet", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
        ("opus", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
    ):
        print(f"{label.ljust(12)} {env.get(key, '') or '(unset)'}")
    print(f"effort       {applied_effort or '(unset)'}")
    print("hint         run `claudeteam switch model` to verify, then restart the team to apply")
    return 0


def _show_current() -> int:
    """Print the active team (resolved from env), one fact per line."""
    state = env_str("CLAUDETEAM_STATE_DIR") or f"(default) {paths.state_dir()}"
    team = env_str("CLAUDETEAM_TEAM_FILE") or f"(default) {config.team_file()}"
    rt = env_str("CLAUDETEAM_RUNTIME_CONFIG") or f"(default) {config.runtime_config_file()}"
    print(f"state_dir:      {state}")
    print(f"team_file:      {team}")
    print(f"runtime_config: {rt}")
    return 0


def _emit_exports(team_dir: Path) -> int:
    if not team_dir.exists():
        return error_exit(f"❌ {team_dir} does not exist")
    team_json = team_dir / "team.json"
    if not team_json.exists():
        return error_exit(
            f"❌ {team_json} not found — pass a directory containing team.json"
            f"\n   (run `claudeteam init` inside that directory first)")
    state_dir = team_dir / "state"
    rt_json = team_dir / "runtime_config.json"
    print(f"export CLAUDETEAM_STATE_DIR={shlex.quote(str(state_dir))}")
    print(f"export CLAUDETEAM_TEAM_FILE={shlex.quote(str(team_json))}")
    print(f"export CLAUDETEAM_RUNTIME_CONFIG={shlex.quote(str(rt_json))}")
    print(f"# Active team: {team_dir}")
    print(f"# Apply with: eval \"$(claudeteam switch {team_dir})\"")
    return 0


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if rest and rest[0] == "model":
        return _apply_model_switch(rest[1:])
    if len(rest) > 1:
        return error_exit(f"❌ too many args: {rest}\n{USAGE}")
    if not rest:
        return _show_current()
    return _emit_exports(Path(rest[0]).expanduser().resolve())
