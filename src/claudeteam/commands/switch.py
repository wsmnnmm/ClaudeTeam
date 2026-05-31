"""`claudeteam switch <team-dir>` — print shell exports for a team directory.

Multi-team isolation today is env-var-based: a deployment is whichever
`claudeteam.toml` / `team.json` + `runtime_config.json` +
`CLAUDETEAM_STATE_DIR` the current shell sees. Switching teams means
re-exporting those vars.

This command emits ready-to-eval export lines so the operator runs:

    eval "$(claudeteam switch ~/teams/projectA)"

The directory layout this assumes (created either by `claudeteam init`
in that dir or by hand) is:

    <team-dir>/
        claudeteam.toml       # preferred
        team.json             # legacy fallback
        runtime_config.json
        state/                # auto-created when claudeteam writes anything

`claudeteam.toml` or `team.json` is the marker file — switch refuses to
point at a directory without one, so a typo doesn't silently succeed.

With no argument, prints the current active team (resolved from env
vars) so an operator can confirm what they're pointing at without
greping their shell history.
"""
from __future__ import annotations

import json
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

from claudeteam.runtime import config, paths, providers, tunables
from claudeteam.util import (
    atomic_write_text,
    env_str,
    error_exit,
    maybe_print_help,
    pop_bool_flag,
    pop_flag,
    print_json,
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
    "       claudeteam switch model models [--preset <name>] [--auth-token <token>]\n"
    "                                      [--save] [--json]\n"
    "       claudeteam switch model service [--use <service-or-preset> | --auto]\n"
    "                                       [--order <a,b,c>] [--auth-token <token>]\n"
    "                                       [--list] [--clear] [--json]\n"
    "       claudeteam switch model agent <agent> [--preset <name> | --clear]\n"
    "  no arg          — print the current active team\n"
    "  <team-dir>      — print exports; wrap in `eval \"$(...)\"` to apply\n"
    "  model           — show or update project-local Claude Code model routing"
)


_PROVIDER_ENV_KEYS = providers.PROVIDER_ENV_KEYS

_ALIAS_ENV_KEY = providers.ALIAS_ENV_KEY

_MANAGED_PROVIDER_ENV = "claudeteam-provider.env"
_PRESETS_FILE = "provider-presets.json"
_MODELS_SNAPSHOT_FILE = "provider-models.json"

_SERVICE_ALIASES = {
    "zyao": "zyapi",
    "tuluo": "zyapi",
    "zyapi": "zyapi",
    "flux": "flux",
    "fluxincode": "flux",
    "onekey": "onekey",
    "dual": "onekey",
    "dualseason": "onekey",
}

_SERVICE_ORDER_DEFAULT = ["zyapi", "onekey", "flux"]


def _provider_env_dir() -> Path:
    return Path.cwd() / ".env.local.d"


def _presets_path() -> Path:
    return paths.state_file(_PRESETS_FILE)


def _models_snapshot_path() -> Path:
    return paths.state_file(_MODELS_SNAPSHOT_FILE)


def _agent_overrides_path() -> Path:
    return providers.agent_overrides_path()


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
    env_path, _, current_effort = _load_provider_state()
    env = {
        key: value
        for key, value in payload.items()
        if key in _PROVIDER_ENV_KEYS and value
    }
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
    service_state = providers.load_service_state()
    service_env = service_state.get("env") if isinstance(service_state, dict) else {}
    print(f"provider_env: {env_path}")
    print(f"ccswitch:     {config.claude_code_settings_file()}")
    print(f"agent_overrides: {_agent_overrides_path()}")
    print(f"service:      {service_state.get('active_service') or '(unset)'}")
    print(f"service_state:{providers.service_state_path()}")
    if isinstance(service_env, dict):
        service_base = (
            service_env.get("OPENAI_BASE_URL")
            or service_env.get("ANTHROPIC_BASE_URL")
            or ""
        )
        print(f"service_url:  {service_base or '(unset)'}")
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
        preset = providers.provider_preset_name(agent)
        effective = providers.effective_model_for_agent(agent, requested)
        suffix = f" provider_preset={preset}" if preset else ""
        print(f"  - {agent}: requested={requested} effective={effective}{suffix}")
    return 0


def _model_candidates(env: dict[str, str]) -> list[str]:
    ordered = [
        env.get("OPENAI_MODEL", ""),
        env.get("ANTHROPIC_MODEL", ""),
        env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", ""),
        env.get("ANTHROPIC_DEFAULT_SONNET_MODEL", ""),
        env.get("ANTHROPIC_DEFAULT_OPUS_MODEL", ""),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for item in ordered:
        value = str(item or "").strip()
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _resolve_models_payload(preset_name: str) -> tuple[str, dict[str, str]]:
    if preset_name:
        presets = _load_presets()
        payload = presets.get(preset_name)
        if payload is None:
            raise KeyError(preset_name)
        return preset_name, dict(payload)
    _, env, _ = _load_provider_state()
    return "current", dict(env)


def _read_openai_auth_token() -> str:
    candidates = [
        paths.codex_auth_file("manager"),
        paths.codex_auth_file(),
    ]
    env_home = env_str("CODEX_HOME")
    if env_home:
        candidates.append(Path(env_home) / "auth.json")
    candidates.append(Path.home() / ".codex" / "auth.json")
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        token = str(data.get("OPENAI_API_KEY") or "").strip()
        if token:
            return token
    return ""


def _models_list_url_from_base(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


def _parse_model_ids(payload: dict) -> list[str]:
    if not isinstance(payload, dict):
        return []
    ids: list[str] = []
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                model_id = str(item.get("id") or "").strip()
                if model_id:
                    ids.append(model_id)
    models = payload.get("models")
    if isinstance(models, list):
        for item in models:
            if isinstance(item, str):
                model_id = item.strip()
            elif isinstance(item, dict):
                model_id = str(item.get("id") or "").strip()
            else:
                model_id = ""
            if model_id:
                ids.append(model_id)
    deduped: list[str] = []
    seen: set[str] = set()
    for model_id in ids:
        if model_id not in seen:
            deduped.append(model_id)
            seen.add(model_id)
    return deduped


def _http_json(url: str, headers: dict[str, str]) -> dict:
    req = urlrequest.Request(url, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"models api failed ({e.code})"
            f"{': ' + body[:200] if body else ''}"
        ) from e
    except urlerror.URLError as e:
        raise RuntimeError(f"models api failed: {e.reason}") from e


def _fetch_model_catalog(source: str, payload: dict[str, str],
                         *, auth_token_override: str = "") -> dict:
    base_url = (
        str(payload.get("OPENAI_BASE_URL") or "").strip()
        or str(payload.get("ANTHROPIC_BASE_URL") or "").strip()
    )
    if not base_url:
        raise RuntimeError("provider base_url is empty")
    auth_token = (
        auth_token_override.strip()
        or str(payload.get("OPENAI_API_KEY") or "").strip()
        or str(payload.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
        or _read_openai_auth_token()
    )
    if not auth_token:
        raise RuntimeError("no auth token available for provider models api")
    models_url = _models_list_url_from_base(base_url)
    raw = _http_json(
        models_url,
        {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "User-Agent": "claudeteam-switch/1.0",
        },
    )
    models = _parse_model_ids(raw)
    configured = _model_candidates(payload)
    return {
        "source": source,
        "base_url": base_url,
        "models_url": models_url,
        "configured_models": configured,
        "available_configured_models": [m for m in configured if m in models],
        "missing_configured_models": [m for m in configured if m not in models],
        "models": models,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "raw": raw,
    }


def _save_model_catalog(catalog: dict) -> Path:
    path = _models_snapshot_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    providers_data = data.get("providers")
    if not isinstance(providers_data, dict):
        providers_data = {}
        data["providers"] = providers_data
    providers_data[str(catalog.get("source") or "current")] = catalog
    write_json(path, data)
    return path


def _emit_model_catalog(catalog: dict, *, snapshot_path: Path | None = None) -> None:
    print(f"source:       {catalog.get('source', '')}")
    print(f"base_url:     {catalog.get('base_url', '')}")
    print(f"models_url:   {catalog.get('models_url', '')}")
    print(f"fetched_at:   {catalog.get('fetched_at', '')}")
    models = catalog.get("models") or []
    print(f"models_count: {len(models)}")
    if models:
        print(f"models:       {', '.join(models)}")
    configured = catalog.get("configured_models") or []
    if configured:
        print(f"configured:   {', '.join(configured)}")
    available = catalog.get("available_configured_models") or []
    if available:
        print(f"verified:     {', '.join(available)}")
    missing = catalog.get("missing_configured_models") or []
    if missing:
        print(f"missing:      {', '.join(missing)}")
    if snapshot_path is not None:
        print(f"snapshot:     {snapshot_path}")


def _models_subcommand(rest: list[str]) -> int:
    preset_name = pop_flag(rest, "--preset") or ""
    auth_token = pop_flag(rest, "--auth-token") or pop_flag(rest, "--api-key") or ""
    save = pop_bool_flag(rest, "--save")
    as_json = pop_bool_flag(rest, "--json")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    try:
        source, payload = _resolve_models_payload(preset_name)
    except KeyError:
        return error_exit(f"❌ no such preset: {preset_name}")
    try:
        catalog = _fetch_model_catalog(
            source, payload, auth_token_override=auth_token)
    except RuntimeError as e:
        return error_exit(f"❌ switch model models: {e}")
    snapshot_path = _save_model_catalog(catalog) if save else None
    if as_json:
        out = dict(catalog)
        if snapshot_path is not None:
            out["snapshot"] = str(snapshot_path)
        print_json(out)
        return 0
    _emit_model_catalog(catalog, snapshot_path=snapshot_path)
    return 0


def _canonical_service_name(raw: str) -> str:
    value = str(raw or "").strip().lower()
    return _SERVICE_ALIASES.get(value, value)


def _payload_base_url(payload: dict[str, str]) -> str:
    return (
        str(payload.get("OPENAI_BASE_URL") or "").strip()
        or str(payload.get("ANTHROPIC_BASE_URL") or "").strip()
    )


def _payload_token(payload: dict[str, str]) -> str:
    return (
        str(payload.get("OPENAI_API_KEY") or "").strip()
        or str(payload.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    )


def _team_has_codex_agents() -> bool:
    for agent in config.agent_names():
        try:
            if config.agent_cli(agent) == "codex-cli":
                return True
        except KeyError:
            continue
    return False


def _payload_looks_codex_compatible(payload: dict[str, str]) -> bool:
    if payload.get("OPENAI_BASE_URL"):
        return True
    base_url = _payload_base_url(payload).lower().rstrip("/")
    if "fluxincode" in base_url or "zyapi" in base_url or "tuluo" in base_url:
        return True
    if "onekey" in base_url and base_url.endswith("/v1"):
        return True
    for model in _model_candidates(payload):
        low = model.lower()
        if low.startswith("gpt-") or "codex" in low:
            return True
    return False


def _service_for_preset(name: str, payload: dict[str, str]) -> str:
    base_url = _payload_base_url(payload).lower()
    if "fluxincode" in base_url:
        return "flux"
    if "zyapi" in base_url or "tuluo" in base_url or "zyao" in base_url:
        return "zyapi"
    if "onekey" in base_url or "dualseason" in base_url:
        return "onekey"
    preset_name = name.lower()
    if "flux" in preset_name:
        return "flux"
    if "zyapi" in preset_name or "tuluo" in preset_name or "zyao" in preset_name:
        return "zyapi"
    if "onekey" in preset_name or "dualseason" in preset_name:
        return "onekey"
    return ""


def _service_env_from_payload(payload: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in providers.SERVICE_ENV_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            env[key] = value
    return env


def _configured_service_order(raw_order: str | None = None) -> list[str]:
    if raw_order:
        raw = [item.strip() for item in raw_order.split(",") if item.strip()]
    else:
        value = tunables.tunable("provider_service.order", list(_SERVICE_ORDER_DEFAULT))
        raw = value if isinstance(value, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = _canonical_service_name(str(item))
        if key and key not in seen:
            out.append(key)
            seen.add(key)
    return out or list(_SERVICE_ORDER_DEFAULT)


def _resolve_service_candidate(name: str,
                               presets: dict[str, dict[str, str]]
                               ) -> tuple[str, str, dict[str, str]] | None:
    """Resolve a service alias or exact preset to (service, preset, payload)."""
    requested = _canonical_service_name(name)
    if name in presets:
        payload = dict(presets[name])
        return _service_for_preset(name, payload) or requested, name, payload
    needs_codex = _team_has_codex_agents()
    matches: list[tuple[int, str, dict[str, str]]] = []
    for preset_name, payload in sorted(presets.items()):
        if needs_codex and not _payload_looks_codex_compatible(payload):
            continue
        if _service_for_preset(preset_name, payload) == requested:
            low_name = preset_name.lower()
            score = 0
            if "rescue" in low_name:
                score += 10
            if requested not in low_name:
                score += 1
            matches.append((score, preset_name, dict(payload)))
    if matches:
        _, preset_name, payload = sorted(matches, key=lambda row: (row[0], row[1]))[0]
        return requested, preset_name, payload
    for preset_name, payload in sorted(presets.items()):
        if needs_codex and not _payload_looks_codex_compatible(payload):
            continue
        if requested in preset_name.lower() and not _service_for_preset(preset_name, payload):
            return requested, preset_name, dict(payload)
    return None


def _service_candidates(order: list[str] | None = None) -> list[dict]:
    presets = _load_presets()
    rows: list[dict] = []
    seen_presets: set[str] = set()
    for item in (order or _configured_service_order()):
        resolved = _resolve_service_candidate(item, presets)
        if not resolved:
            continue
        service, preset_name, payload = resolved
        if preset_name in seen_presets:
            continue
        seen_presets.add(preset_name)
        rows.append({
            "service": service,
            "preset": preset_name,
            "payload": payload,
            "base_url": _payload_base_url(payload),
        })
    return rows


def _apply_service(service: str, preset: str, payload: dict[str, str],
                   *, reason: str, quiet: bool = False) -> int:
    env = _service_env_from_payload(payload)
    if not _payload_base_url(env):
        return error_exit(f"❌ preset {preset} has no provider base_url")
    providers.save_service_state({
        "active_service": service,
        "source_preset": preset,
        "env": env,
        "reason": reason,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    })
    if not quiet:
        print(f"✅ applied service: {service} ({preset})")
        print(f"base_url: {_payload_base_url(env)}")
        print(f"service_state: {providers.service_state_path()}")
        print("hint         recycle active panes so Codex/Claude homes pick up the new service")
    return 0


def _probe_service_candidates(candidates: list[dict], *,
                              auth_token_override: str = "") -> list[dict]:
    results: list[dict] = []
    for row in candidates:
        started = time.perf_counter()
        try:
            catalog = _fetch_model_catalog(
                row["preset"], row["payload"],
                auth_token_override=auth_token_override,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            results.append({
                **row,
                "ok": True,
                "elapsed_ms": elapsed_ms,
                "models_count": len(catalog.get("models") or []),
            })
        except RuntimeError as e:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            results.append({
                **row,
                "ok": False,
                "elapsed_ms": elapsed_ms,
                "error": str(e),
            })
    return results


def _emit_service_rows(rows: list[dict]) -> None:
    state = providers.load_service_state()
    active = state.get("source_preset") or state.get("active_service") or ""
    print(f"service_state: {providers.service_state_path()}")
    print(f"active:        {state.get('active_service') or '(unset)'}")
    print("services:")
    for row in rows:
        marker = "*" if active in {row.get("preset"), row.get("service")} else "-"
        status = "ok" if row.get("ok") else row.get("error") or "not probed"
        elapsed = f" {row.get('elapsed_ms')}ms" if "elapsed_ms" in row else ""
        print(
            f"  {marker} {row.get('service')}: preset={row.get('preset')} "
            f"base_url={row.get('base_url') or '(unset)'} status={status}{elapsed}")


def _service_subcommand(rest: list[str]) -> int:
    use_name = pop_flag(rest, "--use")
    raw_order = pop_flag(rest, "--order")
    auth_token = pop_flag(rest, "--auth-token") or pop_flag(rest, "--api-key") or ""
    do_auto = pop_bool_flag(rest, "--auto")
    do_list = pop_bool_flag(rest, "--list")
    do_clear = pop_bool_flag(rest, "--clear")
    as_json = pop_bool_flag(rest, "--json")
    chosen = sum(1 for value in (use_name, do_auto, do_clear) if value)
    if chosen > 1:
        return error_exit(f"❌ choose only one of --use / --auto / --clear\n{USAGE}")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc

    if do_clear:
        providers.clear_service_state()
        print(f"✅ cleared service override: {providers.service_state_path()}")
        return 0

    order = _configured_service_order(raw_order)
    candidates = _service_candidates(order)

    if do_auto:
        results = _probe_service_candidates(
            candidates, auth_token_override=auth_token)
        ok = [row for row in results if row.get("ok")]
        ok.sort(key=lambda row: row.get("elapsed_ms", 10**9))
        if as_json:
            winner_payload = ok[0] if ok else {}
            if ok:
                rc = _apply_service(
                    winner_payload["service"], winner_payload["preset"],
                    winner_payload["payload"], reason="auto-fastest", quiet=True)
            else:
                rc = 1
            print_json({
                "order": order,
                "results": [
                    {k: v for k, v in row.items() if k != "payload"}
                    for row in results
                ],
                "winner": {
                    k: v for k, v in winner_payload.items()
                    if k != "payload"
                },
                "applied": rc == 0,
            })
            return rc
        else:
            _emit_service_rows(results)
        if not ok:
            return error_exit("❌ no provider service passed the live probe")
        winner = ok[0]
        return _apply_service(
            winner["service"], winner["preset"], winner["payload"],
            reason="auto-fastest",
        )

    if use_name:
        presets = _load_presets()
        resolved = _resolve_service_candidate(use_name, presets)
        if not resolved:
            return error_exit(f"❌ no such provider service or preset: {use_name}")
        service, preset_name, payload = resolved
        return _apply_service(service, preset_name, payload, reason="manual")

    rows = _probe_service_candidates(candidates, auth_token_override=auth_token) if do_list else candidates
    if as_json:
        print_json({
            "active": providers.load_service_state(),
            "services": [
                {k: v for k, v in row.items() if k != "payload"}
                for row in rows
            ],
        })
        return 0
    _emit_service_rows(rows)
    return 0


def _agent_override_subcommand(rest: list[str]) -> int:
    if not rest:
        return error_exit(f"❌ missing agent name\n{USAGE}")
    agent = rest.pop(0)
    try:
        config.agent_config(agent)
    except KeyError:
        return error_exit(f"❌ unknown agent: {agent} (not in team.json)")
    preset = pop_flag(rest, "--preset")
    clear = pop_bool_flag(rest, "--clear")
    if preset and clear:
        return error_exit(f"❌ choose only one of --preset / --clear\n{USAGE}")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc

    overrides = providers.load_agent_overrides()
    current = overrides.get(agent, {})
    if not preset and not clear:
        requested = config.agent_model(agent)
        effective = providers.effective_model_for_agent(agent, requested)
        print(f"agent:          {agent}")
        print(f"requested:      {requested}")
        print(f"effective:      {effective}")
        print(f"provider_preset {providers.provider_preset_name(agent) or '(unset)'}")
        print(f"overrides:      {_agent_overrides_path()}")
        return 0

    if clear:
        overrides.pop(agent, None)
        providers.save_agent_overrides(overrides)
        print(f"✅ cleared agent override: {agent}")
        print(f"overrides: {_agent_overrides_path()}")
        return 0

    if preset not in _load_presets():
        return error_exit(f"❌ no such preset: {preset}")
    current["provider_preset"] = preset
    overrides[agent] = current
    providers.save_agent_overrides(overrides)
    print(f"✅ applied agent preset: {agent} -> {preset}")
    print(f"overrides: {_agent_overrides_path()}")
    print("hint         run `claudeteam switch model` to verify, then reidentify or restart the team")
    return 0


def _apply_model_switch(rest: list[str]) -> int:
    if rest and rest[0] == "preset":
        return _preset_subcommand(rest[1:])
    if rest and rest[0] == "models":
        return _models_subcommand(rest[1:])
    if rest and rest[0] == "service":
        return _service_subcommand(rest[1:])
    if rest and rest[0] == "agent":
        return _agent_override_subcommand(rest[1:])
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

    env_path, _, current_effort = _load_provider_state()
    payload, resolved_effort = _resolve_payload_from_flags(
        shared_model=shared_model,
        base_url=base_url,
        auth_token=auth_token,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        opus_model=opus_model,
        effort=effort,
    )
    env = {k: v for k, v in payload.items() if k in _PROVIDER_ENV_KEYS and v}
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
    cf = env_str("CLAUDETEAM_CONFIG_FILE") or f"(default) {paths.config_file()}"
    team = env_str("CLAUDETEAM_TEAM_FILE") or f"(default) {config.team_file()}"
    rt = env_str("CLAUDETEAM_RUNTIME_CONFIG") or f"(default) {config.runtime_config_file()}"
    print(f"state_dir:      {state}")
    print(f"config_file:    {cf}")
    print(f"team_file:      {team}")
    print(f"runtime_config: {rt}")
    return 0


def _emit_exports(team_dir: Path) -> int:
    if not team_dir.exists():
        return error_exit(f"❌ {team_dir} does not exist")
    toml = team_dir / "claudeteam.toml"
    team_json = team_dir / "team.json"
    if not toml.exists() and not team_json.exists():
        return error_exit(
            f"❌ {team_dir} is not a claudeteam directory"
            f"\n   expected claudeteam.toml or team.json"
            f"\n   (run `claudeteam init` inside that directory first)")
    state_dir = team_dir / "state"
    rt_json = team_dir / "runtime_config.json"
    print(f"export CLAUDETEAM_STATE_DIR={shlex.quote(str(state_dir))}")
    print(f"export CLAUDETEAM_CONFIG_FILE={shlex.quote(str(toml))}")
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
