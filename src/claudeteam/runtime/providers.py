"""Provider routing for project-local model backends.

ClaudeTeam already supports a project-local "current provider" via
`state/ccswitch.json` + `state/provider-presets.json`. This module adds
an extra layer: individual agents can optionally pin a named preset (or
inline env overrides) without changing the rest of the team's routing.

Resolution order for an agent:

  1. global project-local provider env from `ccswitch.json`
  2. agent `provider_preset = "<name>"` from `provider-presets.json`
  3. agent inline `[team.agents.<name>.provider_env]`
  4. team-wide service override from `provider-service.json`

That gives us a safe default: most agents keep following the current
project provider, while specific roles (for example translation /
integration) can be routed to a cheaper or older backend. The service
override is deliberately last so an operator can move the whole Codex
fleet off a broken provider without editing every employee preset.

Runtime failover is a special case: it may temporarily mark a failing
agent to bypass the team-wide service override so a targeted rescue lane
can actually take effect even while the rest of the fleet stays pinned
to a manual service switch.
"""
from __future__ import annotations

import json
from pathlib import Path

from claudeteam.runtime import config, paths
from claudeteam.util import write_json


ANTHROPIC_PROVIDER_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
)

OPENAI_PROVIDER_ENV_KEYS = (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_MODEL_PROVIDER",
    "OPENAI_WIRE_API",
    "OPENAI_REQUIRES_OPENAI_AUTH",
    "OPENAI_DISABLE_RESPONSE_STORAGE",
    "OPENAI_REASONING_EFFORT",
)

PROVIDER_ENV_KEYS = ANTHROPIC_PROVIDER_ENV_KEYS + OPENAI_PROVIDER_ENV_KEYS

ALIAS_ENV_KEY = {
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
}

_PRESETS_FILE = "provider-presets.json"
_MANAGED_PROVIDER_ENV = "claudeteam-provider.env"
_AGENT_OVERRIDES_FILE = "agent-provider-overrides.json"
_SERVICE_STATE_FILE = "provider-service.json"

SERVICE_ENV_KEYS = PROVIDER_ENV_KEYS


def _normalize_service_model_env(env: dict[str, str]) -> dict[str, str]:
    """Keep service overrides self-consistent across Anthropic/OpenAI keys.

    Team-wide service overrides are applied last to rescue a whole fleet from a
    broken backend. If the service env only overrides OPENAI_* keys but leaves
    stale ANTHROPIC_* model aliases behind from ccswitch/agent presets, callers
    like router.first_response can assemble a mixed config: new base/token with
    an old model name. That produces false recoveries such as zyapi auth +
    deepseek model 403s. Normalize the service layer itself so it fully wins.
    """
    openai_model = env.get("OPENAI_MODEL", "").strip()
    anthropic_model = env.get("ANTHROPIC_MODEL", "").strip()
    if openai_model and not anthropic_model:
        env["ANTHROPIC_MODEL"] = openai_model
        anthropic_model = openai_model
    if anthropic_model and not openai_model:
        env["OPENAI_MODEL"] = anthropic_model
        openai_model = anthropic_model
    default_model = anthropic_model or openai_model
    if default_model:
        for key in (
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL"):
            env.setdefault(key, default_model)
    return env


def presets_path() -> Path:
    return paths.state_file(_PRESETS_FILE)


def agent_overrides_path() -> Path:
    return paths.state_file(_AGENT_OVERRIDES_FILE)


def service_state_path() -> Path:
    return paths.state_file(_SERVICE_STATE_FILE)


def _clean_provider_env(raw: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key in PROVIDER_ENV_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return out


def _clean_service_env(raw: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key in SERVICE_ENV_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return _normalize_service_model_env(out)


def _provider_env_dir() -> Path:
    return Path.cwd() / ".env.local.d"


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


def _looks_like_provider_env(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(f"{key}=" in text for key in PROVIDER_ENV_KEYS)


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


def _global_provider_env() -> dict[str, str]:
    env = _clean_provider_env(_read_env_file(_provider_env_path()))
    settings = config.load_claude_code_settings()
    raw = settings.get("env", {})
    for key, value in _clean_provider_env(raw if isinstance(raw, dict) else None).items():
        env[key] = value
    return env


def load_presets() -> dict[str, dict[str, str]]:
    path = presets_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("presets", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, payload in raw.items():
        if isinstance(name, str):
            clean = _clean_provider_env(payload if isinstance(payload, dict) else None)
            if clean:
                out[name] = clean
    return out


def load_service_state() -> dict:
    path = service_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_service_state(state: dict) -> None:
    clean = dict(state)
    clean["env"] = _clean_service_env(clean.get("env"))
    path = service_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, clean)


def clear_service_state() -> None:
    try:
        service_state_path().unlink()
    except FileNotFoundError:
        pass


def service_env_for_agent(agent: str) -> dict[str, str]:
    """Return the team-wide service override for an agent.

    The service override is intentionally applied after agent presets so
    operators can switch the whole team from flux → zyapi/onekey without
    editing every employee's `provider_preset`. Agents listed under
    `[provider_service].exclude_agents` are left untouched so rescue lanes
    can remain on a different backend.
    """
    from claudeteam.runtime import tunables

    override = load_agent_overrides().get(agent, {})
    if override.get("bypass_service_override") is True:
        return {}
    excluded = tunables.tunable("provider_service.exclude_agents", ["worker_rescue"])
    if isinstance(excluded, list) and agent in {str(v) for v in excluded}:
        return {}
    try:
        cli = config.agent_cli(agent)
    except KeyError:
        cli = ""
    if cli == "claude-code" and not bool(
            tunables.tunable("provider_service.apply_to_claude_code", False)):
        return {}
    return _clean_service_env(load_service_state().get("env"))


def _clean_agent_override(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, object] = {}
    preset = raw.get("provider_preset")
    if isinstance(preset, str) and preset.strip():
        out["provider_preset"] = preset.strip()
    env = _clean_provider_env(raw.get("provider_env"))
    if env:
        out["provider_env"] = env
    if raw.get("bypass_service_override") is True:
        out["bypass_service_override"] = True
    if raw.get("failover_managed") is True:
        out["failover_managed"] = True
    return out


def load_agent_overrides() -> dict[str, dict]:
    path = agent_overrides_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("agents", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for name, payload in raw.items():
        if isinstance(name, str):
            clean = _clean_agent_override(payload if isinstance(payload, dict) else None)
            if clean:
                out[name] = clean
    return out


def save_agent_overrides(overrides: dict[str, dict]) -> None:
    clean: dict[str, dict] = {}
    for name, payload in overrides.items():
        if not isinstance(name, str):
            continue
        normalized = _clean_agent_override(payload if isinstance(payload, dict) else None)
        if normalized:
            clean[name] = normalized
    path = agent_overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"agents": clean})


def provider_preset_name(agent: str) -> str:
    override = load_agent_overrides().get(agent, {})
    raw = override.get("provider_preset")
    if isinstance(raw, str) and raw:
        return raw
    try:
        cfg = config.agent_config(agent)
    except KeyError:
        return ""
    raw = cfg.get("provider_preset")
    return raw.strip() if isinstance(raw, str) else ""


def _agent_provider_overrides(agent: str) -> dict[str, str]:
    try:
        cfg = config.agent_config(agent)
    except KeyError:
        return {}
    out: dict[str, str] = {}
    preset_name = cfg.get("provider_preset")
    if preset_name:
        out.update(load_presets().get(str(preset_name).strip(), {}))
    out.update(_clean_provider_env(cfg.get("provider_env")))
    override = load_agent_overrides().get(agent, {})
    preset_name = override.get("provider_preset")
    if isinstance(preset_name, str) and preset_name:
        out.update(load_presets().get(preset_name, {}))
    out.update(_clean_provider_env(override.get("provider_env")))
    return out


def _base_provider_env_for_agent(agent: str) -> dict[str, str]:
    env = _global_provider_env()
    env.update(_agent_provider_overrides(agent))
    return env


def provider_env_for_agent(agent: str) -> dict[str, str]:
    env = _base_provider_env_for_agent(agent)
    env.update(service_env_for_agent(agent))
    return env


def effective_model_for_agent(agent: str, requested_model: str | None = None) -> str:
    requested = (requested_model or config.agent_model(agent) or "").strip()
    try:
        cli = config.agent_cli(agent)
    except KeyError:
        cli = ""
    if cli == "codex-cli":
        # Team-global provider switches should reroute Codex workers to a
        # backend, not flatten every worker onto one stale OPENAI_MODEL.
        # Only agent-local provider overrides may replace the requested model.
        agent_env = _agent_provider_overrides(agent)
        alias_key = ALIAS_ENV_KEY.get(requested.lower())
        if alias_key and agent_env.get(alias_key):
            return agent_env[alias_key]
        openai_model = agent_env.get("OPENAI_MODEL", "").strip()
        if openai_model:
            return openai_model
        return requested
    alias_key = ALIAS_ENV_KEY.get(requested.lower())
    if alias_key:
        env = provider_env_for_agent(agent)
        if env.get(alias_key):
            return env[alias_key]
    return requested


def codex_provider_env_for_agent(agent: str) -> dict[str, str]:
    """Return the OpenAI-compatible provider env Codex should use.

    Codex workers prefer explicit OPENAI_* settings. If an agent only has
    Anthropic-style preset data but the target model itself is OpenAI-native,
    derive an equivalent OPENAI-compatible payload from that preset.
    """
    env = provider_env_for_agent(agent)
    out: dict[str, str] = {}
    for key in OPENAI_PROVIDER_ENV_KEYS:
        value = env.get(key, "")
        if value:
            out[key] = value

    effective_model = effective_model_for_agent(agent)
    if effective_model:
        out["OPENAI_MODEL"] = effective_model

    anthropic_base = env.get("ANTHROPIC_BASE_URL", "")
    anthropic_token = env.get("ANTHROPIC_AUTH_TOKEN", "")
    if "OPENAI_BASE_URL" not in out and anthropic_base:
        out["OPENAI_BASE_URL"] = anthropic_base
    if "OPENAI_API_KEY" not in out and anthropic_token:
        out["OPENAI_API_KEY"] = anthropic_token

    out.setdefault("OPENAI_MODEL_PROVIDER", "custom")
    out.setdefault("OPENAI_WIRE_API", "responses")
    out.setdefault("OPENAI_REQUIRES_OPENAI_AUTH", "true")
    if "OPENAI_DISABLE_RESPONSE_STORAGE" not in out:
        out["OPENAI_DISABLE_RESPONSE_STORAGE"] = "true"
    if "OPENAI_REASONING_EFFORT" not in out:
        try:
            cfg = config.agent_config(agent)
        except KeyError:
            cfg = {}
        thinking = str(cfg.get("thinking", "")).strip().lower()
        if thinking in {"minimal", "low", "medium", "high", "xhigh", "max"}:
            out["OPENAI_REASONING_EFFORT"] = thinking
    return out
