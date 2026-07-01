"""`claudeteam cross-send <team> <to> <from> <message> [priority]`

Send a request into another ClaudeTeam's own state.  This is deliberately
separate from `send`, which is local-team only.
"""
from __future__ import annotations

import contextlib
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claudeteam.commands import send as send_cmd
from claudeteam.runtime import config, team_registry, tunables
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag, read_json, usage_error,
)


USAGE = (
    "usage: claudeteam cross-send <team-ref> <to> <from> <message> [priority] "
    "[--registry-script <path>] [--root <dir>] [--remote-state-dir <dir>] "
    "[--no-task] [--no-inject] "
    "[--cross-track-id <XT-id>] [--cross-track-action <action>]"
)


@dataclass
class TargetTeam:
    key: str
    label: str
    config_path: Path
    team_dir: Path
    remote_meta: dict


@contextlib.contextmanager
def _temporary_env(kvs: dict[str, str]):
    old = {key: os.environ.get(key) for key in kvs}
    for key, value in kvs.items():
        os.environ[key] = value
    try:
        tunables.reset_cache()
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tunables.reset_cache()


def _default_root() -> Path:
    cwd = Path.cwd().resolve()
    candidates = [cwd, cwd.parent, cwd.parent.parent]
    for candidate in candidates:
        if (candidate / "product-lab" / "scripts" / "team-registry.py").exists():
            return candidate
    return cwd


def _config_for_path(raw: str) -> Path | None:
    path = Path(raw).expanduser()
    if not path.exists():
        return None
    if path.is_dir():
        candidate = path / "claudeteam.toml"
        return candidate if candidate.exists() else None
    return path if path.name.endswith(".toml") else None


def _remote_meta_for(root: Path, key: str, label: str,
                     remote_state_dir: Path | None) -> dict:
    base = remote_state_dir
    if base is None:
        for candidate in (
            root / "product-lab" / "state" / "remote-teams",
            root / "state" / "remote-teams",
        ):
            if candidate.exists():
                base = candidate
                break
    if base is None or not base.exists():
        return {}
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        meta = read_json(child / "meta.json", {})
        if not isinstance(meta, dict):
            continue
        if child.name == key or meta.get("key") == key or meta.get("label") == label:
            return meta
    return {}


def _resolve_target(team_ref: str, *, root: Path,
                    registry_script: Path | None,
                    remote_state_dir: Path | None) -> TargetTeam | None:
    config_path = _config_for_path(team_ref)
    if config_path:
        return TargetTeam(
            key=config_path.parent.name,
            label=config_path.parent.name,
            config_path=config_path.resolve(),
            team_dir=config_path.parent.resolve(),
            remote_meta={},
        )
    script = registry_script or team_registry.default_script(root)
    sources = team_registry.load(script)
    for row in sources:
        keys = {
            str(row.get("key") or ""),
            str(row.get("label") or ""),
            str(row.get("expected_chat") or ""),
            str(row.get("lark_profile") or ""),
        }
        if team_ref not in keys:
            continue
        raw_config = str(row.get("config_path") or "")
        if not raw_config:
            return None
        config_path = Path(raw_config).expanduser()
        key = str(row.get("key") or config_path.parent.name)
        label = str(row.get("label") or key)
        return TargetTeam(
            key=key,
            label=label,
            config_path=config_path,
            team_dir=config_path.parent,
            remote_meta=_remote_meta_for(root, key, label, remote_state_dir),
        )
    return None


def _target_env(team: TargetTeam) -> dict[str, str]:
    state_dir = _state_dir_for(team)
    return {
        "CLAUDETEAM_STATE_DIR": str(state_dir),
        "CLAUDETEAM_CONFIG_FILE": str(team.config_path),
        "CLAUDETEAM_TEAM_FILE": str(team.team_dir / "team.json"),
        "CLAUDETEAM_RUNTIME_CONFIG": str(team.team_dir / "runtime_config.json"),
    }


def _cloud_project_dir(config_path: Path) -> Path | None:
    if config_path.name != "claudeteam.cloud.toml":
        return None
    if (
        config_path.parent.name == "claudeteam-cloud"
        and config_path.parent.parent.name == "ops"
    ):
        return config_path.parent.parent.parent
    return config_path.parent


def _state_dir_for(team: TargetTeam) -> Path:
    project_dir = _cloud_project_dir(team.config_path)
    if project_dir is not None:
        runtime_root = Path(os.environ.get("CLAUDETEAM_CLOUD_RUNTIME_ROOT", "/srv/ai/runtime"))
        return runtime_root / f"{project_dir.name}-cloud" / "state"
    return team.team_dir / "state"


def _agent_names_for(team: TargetTeam) -> list[str]:
    if not team.config_path.exists():
        return []
    with _temporary_env(_target_env(team)):
        return config.agent_names()


def _resolve_agent(requested: str, agents: list[str]) -> tuple[str, str]:
    if not agents or requested in agents:
        return requested, ""
    if requested.endswith("_manager") and "manager" in agents:
        return "manager", f"原请求目标 `{requested}` 不在目标团队，已交给 `manager` 分派。"
    if "manager" in agents:
        return "manager", f"原请求目标 `{requested}` 不在目标团队，已交给 `manager` 分派。"
    return requested, ""


def _with_alias_note(message: str, note: str) -> str:
    if not note:
        return message
    return f"{message}\n\n{note}"


def _remote_paths(team: TargetTeam) -> tuple[str, str, str, str]:
    meta = team.remote_meta
    product = str(meta.get("remote_product") or "").strip()
    runtime = str(meta.get("remote_runtime") or "").strip()
    remote_root = product.split("/projects/", 1)[0] if "/projects/" in product else "/srv/ai"
    config_file = str(meta.get("remote_config") or "").strip() or str(team.config_path)
    runtime_config = (
        f"{product}/runtime_config.cloud.json"
        if team.key == "todo002_cloud" else
        f"{product}/runtime_config.json"
    )
    state_dir = f"{runtime}/state" if runtime else f"{product}/state"
    return remote_root, config_file, runtime_config, state_dir


def _remote_send(team: TargetTeam, to: str, frm: str, message: str, priority: str,
                 *, no_task: bool, no_inject: bool,
                 run: Callable = subprocess.run) -> tuple[int, str]:
    meta = team.remote_meta
    host = str(meta.get("remote_host") or "").strip()
    product = str(meta.get("remote_product") or "").strip()
    if not host or not product:
        return 1, f"❌ remote team not dispatchable: {team.label}"
    remote_root, config_file, runtime_config, state_dir = _remote_paths(team)
    send_args = [
        "claudeteam", "send", to, frm, message, priority,
    ]
    if no_task:
        send_args.append("--no-task")
    if no_inject:
        send_args.append("--no-inject")
    send_line = " ".join(shlex.quote(part) for part in send_args)
    setup = [
        "set -euo pipefail",
        f"test -f {shlex.quote(remote_root + '/ClaudeTeam/.venv/bin/activate')} "
        f"&& source {shlex.quote(remote_root + '/ClaudeTeam/.venv/bin/activate')} || true",
        f"cd {shlex.quote(product)}",
        f"export CLAUDETEAM_STATE_DIR={shlex.quote(state_dir)}",
        f"export CLAUDETEAM_CONFIG_FILE={shlex.quote(config_file)}",
        f"export CLAUDETEAM_TEAM_FILE={shlex.quote(product + '/team.json')}",
        f"export CLAUDETEAM_RUNTIME_CONFIG={shlex.quote(runtime_config)}",
        "export LARK_CLI_NO_PROXY=${LARK_CLI_NO_PROXY:-1}",
    ]
    runtime = str(meta.get("remote_runtime") or "").strip()
    if runtime:
        setup.append(
            f"if [ -f {shlex.quote(runtime + '/feishu.env')} ]; "
            f"then set -a; source {shlex.quote(runtime + '/feishu.env')}; set +a; fi"
        )
    setup.append(send_line)
    remote_cmd = "; ".join(setup)
    try:
        proc = run(
            ["ssh", host, f"bash --noprofile --norc -c {shlex.quote(remote_cmd)}"],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"❌ remote dispatch failed: {e}"
    output = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
    return int(proc.returncode), output or f"ssh rc={proc.returncode}"


def _local_send(team: TargetTeam, to: str, frm: str, message: str, priority: str,
                *, no_task: bool, no_inject: bool) -> tuple[int, str]:
    args = [to, frm, message, priority]
    if no_task:
        args.append("--no-task")
    if no_inject:
        args.append("--no-inject")
    with _temporary_env(_target_env(team)):
        import io
        import contextlib as _ctx
        out = io.StringIO()
        with _ctx.redirect_stdout(out):
            rc = int(send_cmd.main(args) or 0)
        return rc, out.getvalue().strip()


def _cross_track_update_remote(team: TargetTeam, track_id: str,
                               action: str, message: str) -> None:
    """Run a cross-track store update inside the target team's env on a remote host."""
    meta = team.remote_meta
    host = str(meta.get("remote_host") or "").strip()
    product = str(meta.get("remote_product") or "").strip()
    if not host or not product:
        return
    remote_root, config_file, runtime_config, state_dir = _remote_paths(team)
    source_team = config.team_file().parent.name
    source_label = config.session_name()
    python_code = (
        "import sys; sys.path.insert(0, '{root}/ClaudeTeam/src'); "
        "from claudeteam.store import cross_track as ct; "
        "from claudeteam.commands.cross_track import _apply_remote_action; "
        "_apply_remote_action('{tid}', '{act}', '''{msg}''', "
        "source_team='{source_team}', source_label='{source_label}')"
    ).format(
        root=remote_root,
        tid=track_id,
        act=action,
        msg=message,
        source_team=source_team,
        source_label=source_label,
    )
    update_cmd = (
        f"cd {shlex.quote(product)} && "
        f"export CLAUDETEAM_STATE_DIR={shlex.quote(state_dir)} && "
        f"{shlex.quote(remote_root + '/ClaudeTeam/.venv/bin/python3')} "
        f"-c {shlex.quote(python_code)}"
    )
    try:
        subprocess.run(
            ["ssh", host, f"bash --noprofile --norc -c {shlex.quote(update_cmd)}"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass


def _cross_track_update_local(team: TargetTeam, track_id: str,
                              action: str, message: str) -> None:
    """Update cross-track store inside the target team's env locally."""
    source_team = config.team_file().parent.name
    source_label = config.session_name()
    with _temporary_env(_target_env(team)):
        from claudeteam.commands.cross_track import _apply_remote_action
        _apply_remote_action(
            track_id, action, message,
            source_team=source_team, source_label=source_label,
        )


def main(argv: list[str], *, run: Callable = subprocess.run) -> int:
    rest = list(argv)
    registry_raw = pop_flag(rest, "--registry-script")
    root_raw = pop_flag(rest, "--root")
    remote_state_raw = pop_flag(rest, "--remote-state-dir")
    no_task = pop_bool_flag(rest, "--no-task")
    no_inject = pop_bool_flag(rest, "--no-inject")
    cross_track_id = pop_flag(rest, "--cross-track-id") or ""
    cross_track_action = pop_flag(rest, "--cross-track-action") or ""
    if len(rest) < 4:
        return usage_error(USAGE)
    team_ref, requested_to, frm, message = rest[:4]
    priority = rest[4] if len(rest) > 4 else "高"
    if len(rest) > 5:
        return usage_error(USAGE)
    root = Path(root_raw).expanduser().resolve() if root_raw else _default_root()
    registry_script = Path(registry_raw).expanduser().resolve() if registry_raw else None
    remote_state_dir = (
        Path(remote_state_raw).expanduser().resolve() if remote_state_raw else None
    )
    target = _resolve_target(
        team_ref, root=root, registry_script=registry_script,
        remote_state_dir=remote_state_dir,
    )
    if target is None:
        return error_exit(f"❌ unknown target team: {team_ref}")
    agents = _agent_names_for(target)
    resolved_to, alias_note = _resolve_agent(requested_to, agents)
    final_message = _with_alias_note(message, alias_note)
    if target.remote_meta.get("remote_host"):
        rc, output = _remote_send(
            target, resolved_to, frm, final_message, priority,
            no_task=no_task, no_inject=no_inject, run=run,
        )
    else:
        rc, output = _local_send(
            target, resolved_to, frm, final_message, priority,
            no_task=no_task, no_inject=no_inject,
        )
    m_local = re.search(r"\[local_id=([^\]\s]+)\]", output)
    m_task = re.search(r"\[task_id=([^\]\s]+)\]", output)
    summary = (
        f"cross-send: target={target.label} requested_target={requested_to} "
        f"resolved_target={resolved_to}"
    )
    if m_local:
        summary += f" local_id={m_local.group(1)}"
    if m_task:
        summary += f" task_id={m_task.group(1)}"
    print(summary)
    if output:
        print(output)
    if rc == 0 and cross_track_id and cross_track_action:
        try:
            if target.remote_meta.get("remote_host"):
                _cross_track_update_remote(
                    target, cross_track_id, cross_track_action, final_message)
            else:
                _cross_track_update_local(
                    target, cross_track_id, cross_track_action, final_message)
        except Exception as e:
            print(f"  ⚠️ cross-track update best-effort failed: {e}")
    return 0 if rc == 0 else error_exit(f"❌ cross-send failed: {output}")
