"""Pane provisioning shared between `start` and `hire`.

`provision_pane(agent, target)` writes identity, handles lazy panes,
spawns the configured CLI, waits for the ready banner, injects the
identity init prompt, and updates the agent's status row. Both
`commands/start.py` (looping over the team) and `commands/hire.py`
(single agent) call into this so the spawn-and-init contract lives in
one place.

Returns one of five outcome strings (callers render differently):
  LAZY            agent has `lazy: true` in team.json; no spawn attempted,
                  status set to 待命
  READY           CLI spawned + ready marker seen + identity init injected
  READY_NO_INIT   CLI spawned but ready marker didn't appear in 20s;
                  identity init skipped (caller surfaces a warning)
  SPAWN_FAILED    `tmux.spawn_agent` returned False (tmux send-keys failed)
  CONFIG_ERROR    bad `cli` value (typo, dropped adapter) caught as
                  KeyError on adapter lookup; caller logs + skips this
                  agent, keeps going for the rest of the team rather
                  than aborting the whole `claudeteam start`.

Also home for `pane_env_prefix()` — the shell env-var prefix prepended
to every spawn_cmd so worker agents inherit `CLAUDETEAM_STATE_DIR`,
project-level `CODEX_HOME`, and the Feishu env into their
`claudeteam say` shell-outs.
"""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from claudeteam.agents import get_adapter, identity
from claudeteam.agents.claude_code import managed_mcp_config
from claudeteam.agents.codex_cli import ensure_workdir_trusted
from claudeteam.runtime import config, paths, providers, tmux, wake
from claudeteam.store import local_facts
from claudeteam.util import atomic_write_text, env_path, env_str


# env vars to propagate from the operator's shell into every spawned pane
# so worker agents' shell-out calls (via Bash tool) see the deployment's
# state dir instead of falling back to ~/.claudeteam.
#
# FEISHU_APP_*/LARKSUITE_CLI_APP_* added 2026-05-08 (bringup B5): when
# tmux server was started by an earlier checkout's `claudeteam up`, new
# panes inherit *its* global env (no FEISHU_APP_ID/SECRET). lark.py's
# tenant_token_from_env() returned None and fell back to the saved
# lark-cli profile — a different app — yielding HTTP 400 "Bot/User can
# NOT be out of the chat" on every `claudeteam say`. Embedding the creds
# in the spawn-cmd prefix sidesteps the tmux-server-env quirk entirely.
_PROPAGATED_ENV = (
    "PYTHONPATH",
    "LARK_CLI_PROFILE",
    "LARK_CLI_NO_PROXY",
    "CLAUDETEAM_LARK_SEND_AS",
    "CLAUDETEAM_TEAM_FILE",
    "CLAUDETEAM_RUNTIME_CONFIG",
    "CLAUDETEAM_DEFAULT_MODEL",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "LARKSUITE_CLI_APP_ID",
    "LARKSUITE_CLI_APP_SECRET",
)


def _venv_path_prefix() -> str:
    """Short PATH prefix injected into panes.

    Prefer the Python interpreter's bin dir (the same environment the
    running `claudeteam` command came from). This matters when the team
    is launched FROM a target project directory: `Path.cwd()` then points
    at the target repo, not the ClaudeTeam repo that actually contains the
    `claudeteam` executable. Fall back to `<cwd>/.venv/bin` for older
    setups.

    Keep literal `$PATH` instead of expanding the caller's full PATH,
    which can exceed tmux send-keys practical length on macOS and truncate
    the spawn command.
    """
    candidates = [
        Path(sys.executable).parent,
        Path(sys.prefix) / "bin",
        Path.cwd() / ".venv" / "bin",
    ]
    seen: set[Path] = set()
    for venv_bin in candidates:
        if venv_bin in seen:
            continue
        seen.add(venv_bin)
        if (venv_bin / "claudeteam").exists():
            return f"{venv_bin}:$PATH"
    return ""


def _path_readable(p: Path) -> bool:
    """Returns True iff `p` can be stat'd. False on PermissionError /
    not-found / any OSError. deploy-issues 2026-05-08 #1: on Linux host
    where /root is mode 700, Path("/root/...").exists() raised
    PermissionError instead of returning False (Python <3.13 behavior),
    killing `claudeteam up` for non-root deployers. Three /root probes
    in this module need the soft semantic."""
    try:
        return p.exists()
    except OSError:
        return False


def _read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_runtime_env_into_claude_settings(settings_path: Path,
                                            provider_env: dict[str, str]) -> None:
    """Make project/runtime model env override host-global Claude settings.

    Host `~/.claude/settings.json` often pins a third-party Anthropic-
    compatible backend globally. That is useful for ad-hoc personal use,
    but in ClaudeTeam we need per-project routing to win. Otherwise a
    project may export `ANTHROPIC_DEFAULT_SONNET_MODEL=MiniMax-...` while
    the copied host settings inside each agent home still force `sonnet`
    back to some other provider/model (caught 2026-05-10 on product-lab:
    spawn env said MiniMax, Claude UI still showed `glm-5.1`).
    """
    data = _read_json_file(settings_path)
    if not isinstance(data, dict):
        data = {}
    env = data.setdefault("env", {})
    if not isinstance(env, dict):
        env = {}
        data["env"] = env
    changed = False
    for key in providers.PROVIDER_ENV_KEYS:
        value = provider_env.get(key, "")
        if not value:
            continue
        if env.get(key) != value:
            env[key] = value
            changed = True
    if changed:
        try:
            settings_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass


def _managed_mcp_payload() -> dict:
    """Return the MCP config panes should use for unattended startup.

    Prefer the operator's explicit global `~/.mcp.json` because that's
    where browser/context7/ocr/mastergo are currently configured on this
    machine. Fall back to `~/.claude/.mcp.json`, then to an empty config.
    """
    candidates = (
        Path.home() / ".mcp.json",
        Path.home() / ".claude" / ".mcp.json",
    )
    for path in candidates:
        if _path_readable(path):
            data = _read_json_file(path)
            if isinstance(data.get("mcpServers"), dict):
                return data
    return {"mcpServers": {}}


def _ensure_claude_agent_home(agent: str) -> None:
    """Materialise a per-agent claude state dir at /data/agent-home/<agent>.

    Each claude pane spawns with `HOME=/data/agent-home/<agent>` so
    each agent has its own `~/.claude.json` (avoids the shared-file
    write-race that corrupts a single-mount setup). The directory
    contains:
      .claude/settings.json     — silent-launch flags (theme, perms)
      .claude/.credentials.json — symlink to /root/.claude/.credentials.json
                                  so OAuth tokens stay bind-mount shared
      .claude/projects          — symlink to /root/.claude/projects
                                  so ccusage in /usage finds session logs
    Best-effort: if /data isn't writable (host tests where the path
    doesn't exist), silently skip and let claude fall back to its
    default `$HOME` discovery.
    """
    from claudeteam.agents.claude_code import agent_home as _agent_home
    home = Path(_agent_home(agent))
    claude_dir = home / ".claude"
    try:
        claude_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    # Host fallback: claude on macOS keys keychain lookup by $HOME, so a
    # per-agent HOME with no .credentials.json gets "Not logged in" even
    # though the keychain entry exists for the user. Export it to a file
    # the first time so each pane has working OAuth.
    cred_link = claude_dir / ".credentials.json"
    # macOS host: prefer the live keychain over a (potentially-stale) host
    # ~/.claude/.credentials.json. Claude refreshes OAuth into the keychain
    # but only writes the file occasionally, so a symlink to the host file
    # can hand the pane a `refreshToken` the server has already revoked.
    # 2026-05-07 caught: pane symlinked to stale host file, refresh
    # round-tripped 401, claude blanked the field, pane logged "401
    # Invalid auth credentials". Re-extract on every provision and write
    # a *regular file* — not a symlink — because claude's atomic-write
    # of credentials replaces the symlink target with a plain file on
    # first refresh anyway, defeating the original sharing intent.
    import platform
    keychain_extracted = False
    if platform.system() == "Darwin":
        import subprocess
        try:
            out = subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                if cred_link.is_symlink() or cred_link.exists():
                    cred_link.unlink()
                cred_link.write_text(out.stdout)
                keychain_extracted = True
        except (OSError, subprocess.TimeoutExpired):
            # `security` missing / keychain locked / subprocess timeout →
            # silent skip and fall through to the host-file branch below.
            pass
    if not keychain_extracted and not cred_link.exists():
        user_creds = Path.home() / ".claude" / ".credentials.json"
        if user_creds.exists():
            try:
                # Copy, not symlink: claude's atomic-write replaces the
                # symlink with a plain file anyway, so start with one.
                cred_link.write_bytes(user_creds.read_bytes())
            except OSError:
                pass
    user_claude_json = Path.home() / ".claude.json"
    claude_json = home / ".claude.json"
    if _path_readable(user_claude_json) and not claude_json.exists():
        try:
            claude_json.write_bytes(user_claude_json.read_bytes())
        except OSError:
            pass
    settings = claude_dir / "settings.json"
    if not settings.exists():
        settings.write_text(
            '{\n'
            '  "skipDangerousModePermissionPrompt": true,\n'
            '  "hasCompletedOnboarding": true,\n'
            '  "theme": "dark",\n'
            '  "permissions": {\n'
            '    "allow": ["Bash", "Edit", "Read", "Write"]\n'
            '  }\n'
            '}\n'
        )
    # Host deploys often keep the effective Claude auth/provider setup
    # spread across ~/.claude/config.json + settings*.json,
    # while the per-agent HOME only gets ~/.claude.json copied above.
    # 2026-05-10 product-lab smoke caught this: per-agent panes showed
    # "Not logged in · Please run /login" even though the operator's
    # default HOME could run `claude -p "OK"` successfully. Copy the
    # small local config files into each agent HOME so provider /
    # auth-adjacent local state follows the pane.
    #
    # Deliberately DO NOT copy ~/.claude/.mcp.json here. Doing so
    # triggers Claude's interactive "new MCP servers found" approval
    # dialog inside fresh panes, which deadlocks unattended team bringup.
    # Project-scoped MCP servers should be enabled explicitly by the
    # operator or via future trusted-project wiring, not inherited
    # blindly from the operator's personal HOME.
    for rel in ("config.json", "settings.local.json"):
        src = Path.home() / ".claude" / rel
        dst = claude_dir / rel
        if _path_readable(src) and not dst.exists():
            try:
                dst.write_bytes(src.read_bytes())
            except OSError:
                pass
    # Prefer the operator's real settings.json over the tiny default
    # stub above when it exists. This carries through provider env,
    # enabled MCP servers, and other local toggles needed for the same
    # auth/provider behavior the operator gets in their normal HOME.
    user_settings = Path.home() / ".claude" / "settings.json"
    if _path_readable(user_settings):
        try:
            settings.write_bytes(user_settings.read_bytes())
        except OSError:
            pass
    _merge_runtime_env_into_claude_settings(settings, providers.provider_env_for_agent(agent))
    managed_mcp = Path(managed_mcp_config(agent))
    try:
        managed_mcp.write_text(
            json.dumps(_managed_mcp_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    # Mirror the operator's "this project path is trusted" flag into the
    # per-agent HOME. Without this, Claude walks upward, sees a parent
    # `~/.mcp.json`, and on first interactive pane boot stops at
    # "new MCP servers found" waiting for manual confirmation. 2026-05-10
    # product-lab smoke caught exactly that. We don't blindly inherit the
    # global ~/.mcp.json file; we only stamp the current cwd's trust state
    # into the copied ~/.claude.json so fresh panes can boot unattended.
    if _path_readable(claude_json):
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
            projects = data.setdefault("projects", {})
            project_cfg = projects.setdefault(str(Path.cwd()), {})
            project_cfg["hasTrustDialogAccepted"] = True
            # If the operator already approved per-project mcpjson servers in
            # their own HOME, preserve that exact choice. Otherwise use an
            # empty approved-list sentinel which suppresses the first-run
            # chooser while leaving explicit per-project MCP wiring available.
            src_data = {}
            if _path_readable(user_claude_json):
                try:
                    src_data = json.loads(user_claude_json.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    src_data = {}
            src_proj = (src_data.get("projects", {}) or {}).get(str(Path.cwd()), {})
            project_cfg["enabledMcpjsonServers"] = list(src_proj.get("enabledMcpjsonServers") or [])
            project_cfg["disabledMcpjsonServers"] = list(src_proj.get("disabledMcpjsonServers") or [])
            project_cfg.setdefault("mcpContextUris", [])
            project_cfg.setdefault("mcpServers", {})
            project_cfg.setdefault("allowedTools", [])
            project_cfg.setdefault("permissions", {})
            project_cfg["permissions"]["allowBypass"] = True
            project_cfg.setdefault("workspaceConfig", {})
            project_cfg["workspaceConfig"]["permissionMode"] = "bypassPermissions"
            claude_json.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, json.JSONDecodeError):
            pass
    cred_link = claude_dir / ".credentials.json"
    cred_target = Path("/root/.claude/.credentials.json")
    if _path_readable(cred_target) and not cred_link.exists():
        try:
            cred_link.symlink_to(cred_target)
        except OSError:
            pass
    projects_link = claude_dir / "projects"
    projects_target = Path("/root/.claude/projects")
    if _path_readable(projects_target) and not projects_link.exists():
        try:
            projects_link.symlink_to(projects_target)
        except OSError:
            pass
    # Seed ~/.claude.json from host's read-only mount once. Without
    # `userID` + `oauthAccount` keys claude pops the OAuth login
    # dialog (the credentials.json alone isn't enough — claude checks
    # ~/.claude.json for "you've completed login" state). After the
    # initial copy, the per-agent file is writable so claude can
    # update its own session counters without affecting other agents.
    claude_json = home / ".claude.json"
    host_claude_json = Path("/root/host-claude.json")
    if _path_readable(host_claude_json) and not claude_json.exists():
        try:
            claude_json.write_bytes(host_claude_json.read_bytes())
        except OSError:
            pass


def _boolish(value: str, default: bool) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _render_codex_config(provider_env: dict[str, str]) -> str:
    model_provider = provider_env.get("OPENAI_MODEL_PROVIDER", "custom").strip() or "custom"
    model = provider_env.get("OPENAI_MODEL", "").strip()
    base_url = provider_env.get("OPENAI_BASE_URL", "").strip()
    wire_api = provider_env.get("OPENAI_WIRE_API", "responses").strip() or "responses"
    requires_openai_auth = _boolish(
        provider_env.get("OPENAI_REQUIRES_OPENAI_AUTH", "true"),
        True,
    )
    disable_response_storage = _boolish(
        provider_env.get("OPENAI_DISABLE_RESPONSE_STORAGE", "true"),
        True,
    )
    effort = provider_env.get("OPENAI_REASONING_EFFORT", "").strip()
    lines = [
        f'model_provider = {json.dumps(model_provider, ensure_ascii=False)}',
    ]
    if model:
        lines.append(f'model = {json.dumps(model, ensure_ascii=False)}')
    if effort:
        lines.append(f'model_reasoning_effort = {json.dumps(effort, ensure_ascii=False)}')
    lines.append('model_verbosity = "medium"')
    lines.append(f"disable_response_storage = {'true' if disable_response_storage else 'false'}")
    # Codex TUI may show an update prompt before the chat input. In an
    # automated tmux worker, our injected Enter can select "Update now",
    # causing the worker to update and exit instead of processing inbox.
    lines.append("check_for_update_on_startup = false")
    lines.append("")
    lines.append(f"[model_providers.{model_provider}]")
    lines.append(f'name = {json.dumps(model_provider, ensure_ascii=False)}')
    lines.append(f'wire_api = {json.dumps(wire_api, ensure_ascii=False)}')
    lines.append(f"requires_openai_auth = {'true' if requires_openai_auth else 'false'}")
    if base_url:
        lines.append(f'base_url = {json.dumps(base_url, ensure_ascii=False)}')
    lines.append("")
    return "\n".join(lines)


def _read_codex_provider_defaults(path: Path) -> dict[str, str]:
    """Read OpenAI-compatible provider defaults from a Codex config.toml.

    ClaudeTeam writes per-agent CODEX_HOME directories. When the operator's
    global Codex config uses a custom OpenAI-compatible endpoint, copying only
    auth.json makes the isolated agent send that custom key to api.openai.com.
    This lightweight reader copies the selected provider's routing fields
    without pulling in unrelated project trust or MCP config.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    top: dict[str, object] = {}
    providers_by_name: dict[str, dict[str, object]] = {}
    current_provider: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            prefix = "model_providers."
            current_provider = section[len(prefix):] if section.startswith(prefix) else None
            if current_provider is not None:
                providers_by_name.setdefault(current_provider, {})
            continue
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value.startswith('"') and raw_value.endswith('"'):
            try:
                value: object = json.loads(raw_value)
            except json.JSONDecodeError:
                value = raw_value.strip('"')
        elif raw_value.lower() in {"true", "false"}:
            value = raw_value.lower() == "true"
        else:
            value = raw_value
        if current_provider is None:
            top[key] = value
        else:
            providers_by_name.setdefault(current_provider, {})[key] = value

    provider_name = str(top.get("model_provider") or "").strip()
    if not provider_name:
        return {}
    provider = providers_by_name.get(provider_name, {})
    out: dict[str, str] = {"OPENAI_MODEL_PROVIDER": provider_name}
    if model := top.get("model"):
        out["OPENAI_MODEL"] = str(model)
    if effort := top.get("model_reasoning_effort"):
        out["OPENAI_REASONING_EFFORT"] = str(effort)
    if "disable_response_storage" in top:
        out["OPENAI_DISABLE_RESPONSE_STORAGE"] = "true" if top["disable_response_storage"] else "false"
    if base_url := provider.get("base_url"):
        out["OPENAI_BASE_URL"] = str(base_url)
    if wire_api := provider.get("wire_api"):
        out["OPENAI_WIRE_API"] = str(wire_api)
    if "requires_openai_auth" in provider:
        out["OPENAI_REQUIRES_OPENAI_AUTH"] = "true" if provider["requires_openai_auth"] else "false"
    return out


def _host_codex_provider_defaults(agent: str) -> dict[str, str]:
    """Return provider defaults from shared/operator Codex configs."""
    seen: set[Path] = set()
    candidates = [
        paths.codex_config_file(),
        (env_path("CODEX_HOME") or Path.home() / ".codex") / "config.toml",
        Path.home() / ".codex" / "config.toml",
    ]
    for path in candidates:
        if path == paths.codex_config_file(agent) or path in seen:
            continue
        seen.add(path)
        defaults = _read_codex_provider_defaults(path)
        if defaults:
            return defaults
    return {}


def _extract_codex_mcp_sections(text: str) -> str:
    """Return verbatim [mcp_servers.*] TOML sections from a Codex config."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current:
                blocks.append(current)
            current = [line] if stripped.startswith("[mcp_servers.") else None
            continue
        if current is not None:
            current.append(line)
    if current:
        blocks.append(current)
    rendered = "\n\n".join("\n".join(block).rstrip() for block in blocks)
    return rendered.strip()


def _codex_mcp_sections(agent: str) -> str:
    """Find MCP server sections to preserve for an isolated Codex home.

    Older deployments installed MCP servers into the shared project Codex
    home. Per-agent Codex homes isolate auth/provider config, so we copy
    those MCP sections forward unless the agent already has its own.
    """
    seen: set[Path] = set()
    candidates = [
        paths.codex_config_file(agent),
        paths.codex_config_file(),
        (env_path("CODEX_HOME") or Path.home() / ".codex") / "config.toml",
        Path.home() / ".codex" / "config.toml",
    ]
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            sections = _extract_codex_mcp_sections(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if sections:
            return sections
    return ""


def _ensure_codex_home(agent: str, model: str) -> None:
    """Materialise the project-scoped Codex home.

    The codex pane runs with `CODEX_HOME=<state_dir>/codex-home` so its
    trust config, auth, and future state stay inside the project instead
    of mutating the operator's global `~/.codex`. To keep first-run
    setup smooth, we either:
      1. materialise project-local custom-provider config/auth from the
         agent's OpenAI-compatible routing, or
      2. fall back to bootstrapping `auth.json` once from the operator's
         current Codex home when that file exists.
    """
    codex_home = paths.codex_home_dir(agent)
    try:
        codex_home.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    provider_env = providers.codex_provider_env_for_agent(agent)
    for key, value in _host_codex_provider_defaults(agent).items():
        provider_env.setdefault(key, value)
    if model and not provider_env.get("OPENAI_MODEL"):
        provider_env["OPENAI_MODEL"] = model
    cfg_text = _render_codex_config(provider_env)
    mcp_sections = _codex_mcp_sections(agent)
    if mcp_sections:
        cfg_text = cfg_text.rstrip() + "\n\n" + mcp_sections + "\n"
    try:
        atomic_write_text(paths.codex_config_file(agent), cfg_text)
    except OSError:
        pass
    dst_auth = paths.codex_auth_file(agent)
    api_key = provider_env.get("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            atomic_write_text(
                dst_auth,
                json.dumps({"OPENAI_API_KEY": api_key}, ensure_ascii=False, indent=2) + "\n",
            )
            return
        except OSError:
            return
    if _path_readable(dst_auth):
        return
    seen: set[Path] = set()
    candidates = [
        (env_path("CODEX_HOME") or Path.home() / ".codex") / "auth.json",
        Path.home() / ".codex" / "auth.json",
    ]
    for src in candidates:
        if src in seen:
            continue
        seen.add(src)
        if not _path_readable(src):
            continue
        try:
            dst_auth.write_bytes(src.read_bytes())
            return
        except OSError:
            return


def pane_env_prefix(agent: str | None = None) -> str:
    """Build a shell env prefix that, prepended to a spawn_cmd, makes the
    spawned process inherit CLAUDETEAM_STATE_DIR, project-level
    CODEX_HOME, and the Feishu env so worker agents calling
    `claudeteam say` write to the project state dir, and codex-cli
    workers resolve config from the project-scoped codex home rather
    than falling back to `~/.codex`.
    """
    parts = [f"CLAUDETEAM_STATE_DIR={shlex.quote(str(paths.state_dir()))}"]
    parts.append(f"CLAUDETEAM_CONFIG_FILE={shlex.quote(str(paths.config_file()))}")
    codex_home = paths.codex_home_dir(agent) if agent else paths.codex_home_dir()
    parts.append(f"CODEX_HOME={shlex.quote(str(codex_home))}")
    pane_path = _venv_path_prefix()
    if pane_path:
        parts.append(f"PATH={pane_path}")
    for var in _PROPAGATED_ENV:
        val = env_str(var)
        if val:
            parts.append(f"{var}={shlex.quote(val)}")
    if agent:
        for key, value in providers.provider_env_for_agent(agent).items():
            parts.append(f"{key}={shlex.quote(value)}")
    return " ".join(parts)


def lazy_spawn_cmd(agent: str) -> str:
    """Build the exact spawn command used when waking a lazy pane.

    Keep lazy first-message wake behaviour identical to start/hire
    provisioning so codex-cli workers get their project-local CODEX_HOME
    pre-created before the CLI boots.
    """
    cli = config.agent_cli(agent)
    requested = config.agent_model(agent)
    model = providers.effective_model_for_agent(agent, requested)
    if cli == "codex-cli":
        _ensure_codex_home(agent, model)
        ensure_workdir_trusted(Path.cwd(), config_path=paths.codex_config_file(agent))
    adapter = get_adapter(cli)
    return f"{pane_env_prefix(agent)} {adapter.spawn_cmd(agent, model)}"


# Outcome strings returned by provision_pane. Callers print/log differently
# (start uses loop-style "  → spawned", hire uses "✅ hired") so the helper
# stays I/O-free and lets the caller render.
LAZY = "lazy"
READY = "ready"
READY_NO_INIT = "ready_no_init"
SPAWN_FAILED = "spawn_failed"
CONFIG_ERROR = "config_error"


def provision_pane(agent: str, target: tmux.Target) -> str:
    """Provision a freshly-created pane for `agent`.

    Pre-conditions: tmux window for `target` already exists and is empty
    (a shell prompt). Caller is responsible for window creation.

    Steps:
      1. Render + persist agent's identity.md (`agents/<name>/identity.md`).
      2. If agent is `lazy` in team.json: set status 待命, return LAZY.
      3. For codex CLI: ensure cwd is trusted in ~/.codex/config.toml.
      4. Spawn the adapter's CLI in the pane (with pane_env_prefix).
      5. Wait up to 20s for the adapter's ready marker to appear.
      6. Inject the identity init prompt so the agent reads identity.md
         and reports for duty.
      7. Set status 进行中.

    Returns one of:
      LAZY            — status set to 待命, no CLI spawn attempted
      READY           — CLI spawned + identity init injected
      READY_NO_INIT   — CLI spawned but ready marker didn't appear in 20s
      SPAWN_FAILED    — tmux.spawn_agent returned False
      CONFIG_ERROR    — agent's `cli` value isn't registered (typo /
                        missing adapter); caller should warn + continue
                        with the rest of the team, NOT kill the whole start.
    """
    # Load team config once. start.py loops over N agents calling this
    # helper, so paying 3-4 disk reads here per agent (one for cfg, one
    # for adapter resolution, one for model fallback) compounds. Cache
    # locally and derive cfg / cli / model from the same dict.
    team = config.load_team()
    cfg = team.get("agents", {}).get(agent)
    if cfg is None:
        import sys
        print(f"  ⚠️ {agent}: agent {agent!r} not in team.json", file=sys.stderr)
        return CONFIG_ERROR
    cli = cfg.get("cli", "claude-code")
    # Inline agent_model resolution: per-agent override → env var →
    # team default → "opus". Mirrors `config.agent_model` but uses the
    # already-loaded `team` dict for the default_model fallback.
    requested_model = (cfg.get("model")
                       or env_str("CLAUDETEAM_DEFAULT_MODEL")
                       or team.get("default_model", "opus"))
    model = providers.effective_model_for_agent(agent, requested_model)
    # Pass resolved fields to identity.write so its internal render()
    # skips a redundant config.agent_config() fallback. `role`
    # defaulting to `agent` matches render's own fallback so the
    # rendered file is byte-identical.
    identity.write(agent, role=cfg.get("role") or agent, cli=cli, model=model)
    if cfg.get("lazy"):
        local_facts.upsert_status(agent, "待命", "lazy: CLI starts on first message")
        return LAZY
    if cli == "codex-cli":
        _ensure_codex_home(agent, model)
        ensure_workdir_trusted(Path.cwd(), config_path=paths.codex_config_file(agent))
    if cli == "claude-code":
        _ensure_claude_agent_home(agent)
    try:
        adapter = get_adapter(cli)
    except KeyError as e:
        # Bad `cli` value in team.json — typo, dropped adapter, etc. One
        # bad agent shouldn't kill `claudeteam start` for the rest of
        # the team. Caller logs + skips.
        import sys
        print(f"  ⚠️ {agent}: {e}", file=sys.stderr)
        return CONFIG_ERROR
    cmd = f"{pane_env_prefix(agent)} {adapter.spawn_cmd(agent, model)}"
    if not tmux.spawn_agent(target, cmd):
        return SPAWN_FAILED
    # 60s ready timeout (was 20s): fresh container claude panes go
    # through up to 3 first-launch dialogs (theme picker / auth-method
    # picker / bypass-permissions confirm) before the ready marker
    # appears. The poll loop auto-Enters each dialog at ~1Hz, so a
    # 3-dialog chain plus boot time can run 30-40s; 60s gives headroom.
    from claudeteam.runtime import tunables
    ready_timeout = float(tunables.tunable("wake.ready_marker_timeout_s", 60.0))
    if wake.wait_until_ready(target, adapter, timeout_s=ready_timeout):
        tmux.inject(target, identity.init_prompt(agent),
                    submit_keys=adapter.submit_keys())
        outcome = READY
    else:
        outcome = READY_NO_INIT
    local_facts.upsert_status(agent, "进行中", "initializing")
    return outcome
