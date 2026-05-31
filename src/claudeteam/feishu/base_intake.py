"""Feishu Base change events -> ClaudeTeam dispatch.

This is the boss-cockpit write path: the boss edits a Base row, Feishu
emits `drive.file.bitable_record_changed_v1`, and the router turns that
record into a real local team task/inbox nudge.

Safety rules:
- Only enabled when `[base_intake].enabled = true`.
- Only records with an explicit `老板决策` / `老板操作` value, or task-flow
  records whose `当前状态` is in `trigger_statuses`, are dispatched.
- Dispatch is idempotent by `(table_id, record_id, decision fingerprint)`.
  Sync/writeback events for the same decision are skipped.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claudeteam.commands import cockpit_sync, send as send_cmd
from claudeteam.feishu import lark
from claudeteam.runtime import paths, tunables
from claudeteam.store import tasks as task_store
from claudeteam.util import flock, fmt_time_ms, now_ms, read_json, write_json


DEFAULT_TASK_TABLE_ID = "tblJ67mLhY9oM91G"
DEFAULT_EVENT_TYPES = ["drive.file.bitable_record_changed_v1"]
DEFAULT_DECISION_FIELDS = ["老板决策", "决策指令", "执行指令"]
DEFAULT_ACTION_FIELDS = ["老板操作", "人工操作"]
ACTION_ALIASES = {
    "重新校验": "重新核验",
}
MANAGER_ACTIONS = {"重新核验", "重新激活", "唤醒团队", "恢复执行", "重新分派"}
ACTION_ALIASES.update({action: action for action in MANAGER_ACTIONS | {"唤醒员工", "继续执行"}})
_RECORD_RE = re.compile(r"^rec[A-Za-z0-9_\-]+")
_TABLE_RE = re.compile(r"^tbl[A-Za-z0-9_\-]+")
_TASK_ID_RE = re.compile(r"\bT-\d+\b")
_DIRECT_STATUS_ALIASES = {
    "待处理": "待处理",
    "待下发": "待处理",
    "重新打开": "待处理",
    "重开": "待处理",
    "进行中": "进行中",
    "执行中": "进行中",
    "开始处理": "进行中",
    "待验收": "待验收",
    "已完成": "已完成",
    "完成": "已完成",
    "验收通过": "已完成",
    "关闭任务": "已完成",
    "关单": "已完成",
    "通过": "已完成",
    "已取消": "已取消",
    "取消": "已取消",
    "取消任务": "已取消",
    "作废": "已取消",
}


@dataclass(frozen=True)
class DispatchIntent:
    table_id: str
    record_id: str
    team_label: str
    agent: str
    title: str
    body: str
    fingerprint: str
    action_field: str = ""
    action_value: str = ""


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    task_id: str = ""
    message: str = ""
    skipped: bool = False


@dataclass(frozen=True)
class TaskStatusIntent:
    table_id: str
    record_id: str
    team_label: str
    task_id: str
    status: str
    artifact: str
    fingerprint: str
    action_field: str = ""
    action_value: str = ""


@contextlib.contextmanager
def _temporary_env(overrides: dict[str, str]):
    old = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    tunables.reset_cache()
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tunables.reset_cache()


def enabled() -> bool:
    return bool(tunables.tunable("base_intake.enabled", False))


def event_types() -> list[str]:
    raw = tunables.tunable("base_intake.event_types", DEFAULT_EVENT_TYPES)
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return list(DEFAULT_EVENT_TYPES)


def _base_token() -> str:
    return str(tunables.tunable(
        "base_intake.base_token",
        tunables.tunable("cockpit_sync.base_token", cockpit_sync.DEFAULT_BASE_TOKEN),
    ) or cockpit_sync.DEFAULT_BASE_TOKEN)


def _task_table_id() -> str:
    return str(tunables.tunable("base_intake.task_table_id", DEFAULT_TASK_TABLE_ID)
               or DEFAULT_TASK_TABLE_ID)


def _cockpit_table_id() -> str:
    return str(tunables.tunable(
        "base_intake.cockpit_table_id",
        tunables.tunable("cockpit_sync.table_id", cockpit_sync.DEFAULT_TABLE_ID),
    ) or cockpit_sync.DEFAULT_TABLE_ID)


def watched_table_ids() -> set[str]:
    extra = tunables.tunable("base_intake.table_ids", [])
    table_ids = {_task_table_id(), _cockpit_table_id()}
    if isinstance(extra, str):
        table_ids.update(s.strip() for s in extra.split(",") if s.strip())
    elif isinstance(extra, list):
        table_ids.update(str(s).strip() for s in extra if str(s).strip())
    return {t for t in table_ids if t}


def _trigger_statuses() -> set[str]:
    raw = tunables.tunable(
        "base_intake.trigger_statuses",
        ["待下发", "老板已决策", "已确认", "执行", "立即执行"],
    )
    if isinstance(raw, str):
        return {s.strip() for s in raw.split(",") if s.strip()}
    if isinstance(raw, list):
        return {str(s).strip() for s in raw if str(s).strip()}
    return {"待下发", "老板已决策", "已确认", "执行", "立即执行"}


def _configured_fields(key: str, default: list[str]) -> list[str]:
    raw = tunables.tunable(key, default)
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return list(default)


def _decision_fields() -> list[str]:
    return _configured_fields("base_intake.decision_fields", DEFAULT_DECISION_FIELDS)


def _action_fields() -> list[str]:
    return _configured_fields("base_intake.action_fields", DEFAULT_ACTION_FIELDS)


def _state_file() -> Path:
    return paths.state_file("base-intake.json")


def _locked():
    return flock(_state_file().with_suffix(".lock"))


def _load_state() -> dict:
    return read_json(_state_file(), {"records": {}})


def _save_state(data: dict) -> None:
    write_json(_state_file(), data)


def _field_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "value", "name", "link"):
            if key in value:
                return _field_text(value[key])
    if isinstance(value, list):
        return " ".join(_field_text(v) for v in value if _field_text(v)).strip()
    return str(value).strip()


def _first_named_field(fields: dict, names: list[str]) -> tuple[str, str]:
    for name in names:
        text = _field_text(fields.get(name))
        if text:
            return name, text
    return "", ""


def _normalize_action(action_text: str) -> str:
    action = action_text.strip()
    return ACTION_ALIASES.get(action, action)


def _event_type(payload: dict) -> str:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    return str(
        payload.get("type")
        or payload.get("event_type")
        or header.get("event_type")
        or ""
    )


def _event_id(payload: dict) -> str:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    return str(payload.get("event_id") or header.get("event_id") or "")


def _walk_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def extract_table_id(payload: dict) -> str:
    for value in _walk_values(payload.get("event", payload)):
        if isinstance(value, str) and _TABLE_RE.match(value):
            return value
    return ""


def extract_record_ids(payload: dict) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for value in _walk_values(payload.get("event", payload)):
        if not isinstance(value, str) or not _RECORD_RE.match(value):
            continue
        if value not in seen:
            ids.append(value)
            seen.add(value)
    return ids


def is_base_change_event(payload: dict, *, configured_types: list[str] | None = None) -> bool:
    etype = _event_type(payload)
    configured = set(configured_types or event_types())
    return etype in configured or etype.endswith("bitable_record_changed_v1")


def _record_fields(payload: dict | None) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    for item in cockpit_sync._record_items(payload):  # reuse lark-cli matrix parser
        fields = item.get("fields")
        rid = str(item.get("record_id") or item.get("recordId") or item.get("id") or "")
        if rid and isinstance(fields, dict):
            rows.append((rid, fields))
    return rows


def fetch_records(record_ids: list[str], *, base_token: str, table_id: str,
                  profile: str, lark_call: Callable = lark.call) -> list[tuple[str, dict]]:
    if not record_ids:
        return []
    args = [
        "base", "+record-get",
        "--base-token", base_token,
        "--table-id", table_id,
        "--format", "json",
    ]
    for rid in record_ids:
        args.extend(["--record-id", rid])
    payload = lark_call(args, profile=profile)
    return _record_fields(payload)


def _fingerprint(table_id: str, record_id: str, fields: dict,
                 decision_text: str, action_text: str) -> str:
    parts = [
        table_id,
        record_id,
        decision_text,
        action_text,
        _field_text(fields.get("当前状态")),
        _field_text(fields.get("老板分组")),
        _field_text(fields.get("任务标题")),
        _field_text(fields.get("当前动作")),
        _field_text(fields.get("下一步动作")),
        _field_text(fields.get("负责人团队") or fields.get("所属战场") or fields.get("战场")),
        _field_text(fields.get("负责人agent")),
    ]
    return "\n".join(parts)


def _is_noop_action(text: str) -> bool:
    return text.strip().lower() in {
        "",
        "无",
        "无需",
        "无需操作",
        "暂不处理",
        "不处理",
        "已处理",
        "done",
        "none",
        "noop",
    }


def _normalise_direct_status(raw: str) -> str:
    status = raw.strip()
    return _DIRECT_STATUS_ALIASES.get(status, "")


def _task_id_from_fields(fields: dict) -> str:
    for name in ("任务号", "任务ID", "任务id", "task_id", "Task ID"):
        value = _field_text(fields.get(name))
        if _TASK_ID_RE.fullmatch(value):
            return value
    for name in ("任务卡ID", "任务卡id", "任务标题", "当前步骤"):
        value = _field_text(fields.get(name))
        match = _TASK_ID_RE.search(value)
        if match:
            return match.group(0)
    return ""


def _team_label_from_task_fields(fields: dict) -> str:
    for name in ("所属战场", "负责人团队", "战场"):
        value = _field_text(fields.get(name))
        if value:
            return value
    card_id = _field_text(fields.get("任务卡ID"))
    if "/" in card_id:
        return card_id.rsplit("/", 1)[0].strip()
    return ""


def _artifact_from_fields(fields: dict) -> str:
    for name in ("真实产物链接", "产物链接", "产物", "任务路径"):
        value = _field_text(fields.get(name))
        if value:
            return value
    return ""


def build_task_status_intent(table_id: str, record_id: str,
                             fields: dict) -> TaskStatusIntent | None:
    """Build a direct Base-status -> local task status intent.

    This is deliberately limited to the task table. General cockpit rows may
    have status-like fields, but those describe a team summary rather than a
    canonical local task card.
    """
    if table_id != _task_table_id():
        return None
    action_field, action_text = _first_named_field(fields, _action_fields())
    action_status = _normalise_direct_status(_normalize_action(action_text))
    raw_status = (
        _field_text(fields.get("状态"))
        or _field_text(fields.get("当前状态"))
    )
    if action_status:
        raw_status = _normalize_action(action_text)
    status = _normalise_direct_status(raw_status)
    if not status:
        return None
    # `待下发` remains the task-flow dispatch path, not a local task close/edit.
    if raw_status in _trigger_statuses() and not _task_id_from_fields(fields):
        return None
    task_id = _task_id_from_fields(fields)
    team_label = _team_label_from_task_fields(fields)
    if not task_id or not team_label:
        return None
    artifact = _artifact_from_fields(fields)
    fingerprint = "\n".join([
        table_id,
        record_id,
        team_label,
        task_id,
        status,
        artifact,
        action_field,
        action_text,
        _field_text(fields.get("老板决策")),
        _field_text(fields.get("下一步动作")),
    ])
    return TaskStatusIntent(
        table_id=table_id,
        record_id=record_id,
        team_label=team_label,
        task_id=task_id,
        status=status,
        artifact=artifact,
        fingerprint=fingerprint,
        action_field=action_field if action_status else "",
        action_value=action_text if action_status else "",
    )


def _reactivation_instruction(action_text: str, fields: dict) -> str:
    action = _normalize_action(action_text)
    extra = _field_text(fields.get("老板下一步") or fields.get("需要老板做什么"))
    owner = _field_text(fields.get("负责人agent")) or "manager"
    if action in MANAGER_ACTIONS:
        base = (
            f"{action}：让 manager 先唤醒/确认 pane ready，运行 health 与任务账本对账，"
            "核对当前任务是否还要继续、是否卡住、下一次汇报时间，并把最新事实写回驾驶舱。"
        )
        return f"{base} 原建议：{extra}" if extra else base
    if action == "唤醒员工":
        base = (
            f"唤醒 {owner}：先回当前状态、手头任务、卡点和下一次回报时间，"
            "不要空发‘已收到’。"
        )
        return f"{base} 原建议：{extra}" if extra else base
    if action == "继续执行":
        base = (
            f"让 {owner} 继续执行当前任务，补最新事实、产物或 blocker，"
            "不要只回态度不回进展。"
        )
        return f"{base} 原建议：{extra}" if extra else base
    return action


def build_intent(table_id: str, record_id: str, fields: dict) -> DispatchIntent | None:
    _, decision_text = _first_named_field(fields, _decision_fields())
    action_field, action_text = _first_named_field(fields, _action_fields())
    action_text = _normalize_action(action_text)
    if _is_noop_action(action_text):
        action_field = ""
        action_text = ""
    status_action = _normalize_action(_field_text(fields.get("状态")))
    if not decision_text and not action_text and status_action in ACTION_ALIASES:
        action_text = status_action
        action_field = ""
    status = _field_text(fields.get("当前状态"))
    boss_group = _field_text(fields.get("老板分组"))
    task_table = table_id == _task_table_id()
    explicit = bool(decision_text or action_text)
    status_trigger = task_table and status in _trigger_statuses()
    if not explicit and not status_trigger:
        return None

    team_label = (
        _field_text(fields.get("负责人团队"))
        or _field_text(fields.get("所属战场"))
        or _field_text(fields.get("战场"))
    )
    title = (
        _field_text(fields.get("任务标题"))
        or _field_text(fields.get("当前动作"))
        or _field_text(fields.get("老板一句话"))
        or f"Base 决策 {record_id}"
    )
    if action_text and not decision_text:
        title = f"{action_text}：{team_label or title}"
    next_action = (
        decision_text
        or _reactivation_instruction(action_text, fields)
        or _field_text(fields.get("下一步动作"))
        or _field_text(fields.get("老板下一步"))
        or _field_text(fields.get("需要老板做什么"))
    )
    if not team_label or not next_action:
        return None
    agent = "manager" if action_text in MANAGER_ACTIONS else (
        _field_text(fields.get("负责人agent")) or "manager")
    if not agent or agent.lower() in {"codex", "claude", "claudeteam"}:
        agent = "manager"
    body = "\n".join([
        f"[Base老板决策] {title}",
        f"来源表: {table_id}",
        f"记录: {record_id}",
        f"战场: {team_label}",
        f"当前状态: {status or boss_group or '-'}",
        f"老板决策/下一步: {next_action}",
        "",
        "执行要求:",
        "- 先核对当前任务卡和现场事实。",
        "- 如能执行，直接创建/推进最小任务并回写产物或证据。",
        "- 如不能执行，5 分钟内回报 blocker、已尝试、需要谁帮、下次回报时间。",
    ])
    return DispatchIntent(
        table_id=table_id,
        record_id=record_id,
        team_label=team_label,
        agent=agent,
        title=title,
        body=body,
        fingerprint=_fingerprint(table_id, record_id, fields, next_action, action_text),
        action_field=action_field,
        action_value=action_text,
    )


def _team_dir_for_label(root: Path, label: str) -> Path | None:
    for team_dir in cockpit_sync._discover(root):
        if cockpit_sync._label_for(team_dir) == label or team_dir.name == label:
            return team_dir
    if label in {"智能伙伴", "本地 OpenClaw"}:
        for team_dir in cockpit_sync._discover(root):
            if cockpit_sync._label_for(team_dir) == "Product Lab 本地":
                return team_dir
    return None


def _remote_state_dir(root: Path) -> Path | None:
    raw = str(tunables.tunable(
        "base_intake.remote_state_dir",
        tunables.tunable("cockpit_sync.remote_state_dir", ""),
    ) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return cockpit_sync._default_remote_state_dir(root)


def _remote_snapshot_for_label(root: Path, label: str) -> Path | None:
    for snapshot in cockpit_sync._remote_snapshot_dirs(_remote_state_dir(root)):
        if cockpit_sync._remote_label(snapshot) == label:
            return snapshot
    return None


def _target_env(team_dir: Path) -> dict[str, str]:
    return {
        "CLAUDETEAM_STATE_DIR": str(team_dir / "state"),
        "CLAUDETEAM_CONFIG_FILE": str(team_dir / "claudeteam.toml"),
        "CLAUDETEAM_TEAM_FILE": str(team_dir / "team.json"),
        "CLAUDETEAM_RUNTIME_CONFIG": str(team_dir / "runtime_config.json"),
    }


def _dispatch_remote_intent(intent: DispatchIntent, *, root: Path,
                            run: Callable = subprocess.run) -> DispatchResult:
    snapshot = _remote_snapshot_for_label(root, intent.team_label)
    if snapshot is None:
        return DispatchResult(False, message=f"unknown team: {intent.team_label}")
    meta = cockpit_sync._read_remote_meta(snapshot)
    key = str(meta.get("key") or snapshot.name)
    host = str(meta.get("remote_host") or "").strip()
    product = str(meta.get("remote_product") or "").strip()
    runtime = str(meta.get("remote_runtime") or "").strip()
    if not host or not product:
        return DispatchResult(
            False,
            message=f"remote team not dispatchable yet: {intent.team_label}",
        )
    target = intent.agent or "manager"
    remote_root = product.split("/projects/", 1)[0] if "/projects/" in product else "/srv/ai"
    config_file = str(meta.get("remote_config") or "").strip()
    if not config_file:
        config_file = (
            f"{product}/ops/claudeteam-cloud/claudeteam.cloud.toml"
            if key == "product_lab_cloud" else
            f"{product}/claudeteam.cloud.toml"
        )
    runtime_config = (
        f"{product}/runtime_config.cloud.json"
        if key == "todo002_cloud" else
        f"{product}/runtime_config.json"
    )
    state_dir = f"{runtime}/state" if runtime else f"{product}/state"
    send_line = (
        f"claudeteam send {shlex.quote(target)} boss_base "
        f"{shlex.quote(intent.body)} 高"
    )
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
    if runtime:
        setup.append(
            f"if [ -f {shlex.quote(runtime + '/feishu.env')} ]; "
            f"then set -a; source {shlex.quote(runtime + '/feishu.env')}; set +a; fi"
        )
    setup.append(
        f"if [ -f {shlex.quote(product + '/.env.local.d/runtime-cloud.env')} ]; "
        f"then set -a; source {shlex.quote(product + '/.env.local.d/runtime-cloud.env')}; set +a; fi"
    )
    setup.append(send_line)
    remote_cmd = "; ".join(setup)
    try:
        proc = run(
            ["ssh", host, "bash", "-lc", shlex.quote(remote_cmd)],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return DispatchResult(False, message=f"remote dispatch failed: {e}")
    output = "\n".join(
        part for part in (proc.stdout.strip(), proc.stderr.strip()) if part
    )
    m = re.search(r"\[task_id=([^\]\s]+)\]", output)
    return DispatchResult(
        ok=proc.returncode == 0,
        task_id=m.group(1) if m else "",
        message=output or f"ssh rc={proc.returncode}",
    )


def dispatch_intent(intent: DispatchIntent, *, root: Path | None = None,
                    send_main: Callable = send_cmd.main,
                    run: Callable = subprocess.run) -> DispatchResult:
    root = root or Path(str(tunables.tunable(
        "base_intake.root",
        tunables.tunable("cockpit_sync.root", ""),
    ) or Path.cwd())).expanduser().resolve()
    team_dir = _team_dir_for_label(root, intent.team_label)
    if team_dir is None:
        return _dispatch_remote_intent(intent, root=root, run=run)

    from claudeteam.runtime import config
    with _temporary_env(_target_env(team_dir)):
        agents = set(config.agent_names())
        target = intent.agent if intent.agent in agents else "manager"
        message = intent.body
        if target != intent.agent:
            message += f"\n\n注意：Base 指定负责人 `{intent.agent}` 不在该团队，已先交给 manager 分派。"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = int(send_main([target, "boss_base", message, "高"]) or 0)
    output = out.getvalue()
    m = re.search(r"\[task_id=([^\]\s]+)\]", output)
    task_id = m.group(1) if m else ""
    return DispatchResult(
        ok=rc == 0,
        task_id=task_id,
        message=output.strip() or f"send rc={rc}",
    )


def _root_for_intake() -> Path:
    return Path(str(tunables.tunable(
        "base_intake.root",
        tunables.tunable("cockpit_sync.root", ""),
    ) or Path.cwd())).expanduser().resolve()


def _apply_remote_task_status(intent: TaskStatusIntent, *, root: Path,
                              run: Callable = subprocess.run) -> DispatchResult:
    snapshot = _remote_snapshot_for_label(root, intent.team_label)
    if snapshot is None:
        return DispatchResult(False, message=f"unknown team: {intent.team_label}")
    if intent.status == "已完成" and not intent.artifact:
        return DispatchResult(
            False,
            message=f"{intent.task_id} cannot be marked 已完成 without 产物链接",
        )
    meta = cockpit_sync._read_remote_meta(snapshot)
    key = str(meta.get("key") or snapshot.name)
    host = str(meta.get("remote_host") or "").strip()
    product = str(meta.get("remote_product") or "").strip()
    runtime = str(meta.get("remote_runtime") or "").strip()
    if not host or not product:
        return DispatchResult(
            False,
            message=f"remote team not dispatchable yet: {intent.team_label}",
        )
    remote_root = product.split("/projects/", 1)[0] if "/projects/" in product else "/srv/ai"
    config_file = str(meta.get("remote_config") or "").strip()
    if not config_file:
        config_file = (
            f"{product}/ops/claudeteam-cloud/claudeteam.cloud.toml"
            if key == "product_lab_cloud" else
            f"{product}/claudeteam.cloud.toml"
        )
    runtime_config = (
        f"{product}/runtime_config.cloud.json"
        if key == "todo002_cloud" else
        f"{product}/runtime_config.json"
    )
    state_dir = f"{runtime}/state" if runtime else f"{product}/state"
    update_line = (
        f"claudeteam task update {shlex.quote(intent.task_id)} "
        f"--status {shlex.quote(intent.status)}"
    )
    if intent.status == "已完成":
        update_line += " --by boss_base"
    if intent.artifact:
        update_line += f" --artifact {shlex.quote(intent.artifact)}"
    setup = [
        "set -euo pipefail",
        f"test -f {shlex.quote(remote_root + '/ClaudeTeam/.venv/bin/activate')} "
        f"&& source {shlex.quote(remote_root + '/ClaudeTeam/.venv/bin/activate')} || true",
        f"cd {shlex.quote(product)}",
        f"export CLAUDETEAM_STATE_DIR={shlex.quote(state_dir)}",
        f"export CLAUDETEAM_CONFIG_FILE={shlex.quote(config_file)}",
        f"export CLAUDETEAM_TEAM_FILE={shlex.quote(product + '/team.json')}",
        f"export CLAUDETEAM_RUNTIME_CONFIG={shlex.quote(runtime_config)}",
        update_line,
    ]
    remote_cmd = "; ".join(setup)
    try:
        proc = run(
            ["ssh", host, "bash", "-lc", shlex.quote(remote_cmd)],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return DispatchResult(False, message=f"remote status update failed: {e}")
    output = "\n".join(
        part for part in (proc.stdout.strip(), proc.stderr.strip()) if part
    )
    return DispatchResult(
        ok=proc.returncode == 0,
        task_id=intent.task_id,
        message=output or f"ssh rc={proc.returncode}",
    )


def apply_task_status_intent(intent: TaskStatusIntent, *, root: Path | None = None,
                             run: Callable = subprocess.run) -> DispatchResult:
    """Apply a direct Base status edit to the canonical local task store."""
    root = root or _root_for_intake()
    team_dir = _team_dir_for_label(root, intent.team_label)
    if team_dir is None:
        return _apply_remote_task_status(intent, root=root, run=run)

    with _temporary_env(_target_env(team_dir)):
        current = task_store.get(intent.task_id)
        if current is None:
            return DispatchResult(
                False,
                task_id=intent.task_id,
                message=f"no such task: {intent.task_id}",
            )
        artifact = intent.artifact or str(current.get("artifact_path") or "")
        if intent.status == "已完成" and not artifact:
            return DispatchResult(
                False,
                task_id=intent.task_id,
                message=f"{intent.task_id} cannot be marked 已完成 without 产物链接",
            )
        ok = task_store.update(
            intent.task_id,
            status=intent.status,
            artifact_path=artifact if artifact else None,
            reviewed_by="boss_base" if intent.status == "已完成" else None,
            _force=(intent.status == "已完成"),
        )
    return DispatchResult(
        ok=ok,
        task_id=intent.task_id,
        message=(
            f"status synced: {intent.team_label}/{intent.task_id} -> {intent.status}"
            if ok else f"no such task: {intent.task_id}"
        ),
    )


def _writeback(record_id: str, *, table_id: str, profile: str, intent: DispatchIntent,
               result: DispatchResult,
               lark_call: Callable = lark.call) -> None:
    field = str(tunables.tunable("base_intake.writeback_field", "下发回执") or "").strip()
    if not field:
        return
    stamp = fmt_time_ms(now_ms())
    body = (
        f"{stamp} 已下发"
        f"{f' {result.task_id}' if result.task_id else ''}: {result.message[:180]}"
        if result.ok else
        f"{stamp} 下发失败: {result.message[:220]}"
    )
    data = {field: body}
    if (
        intent.action_field
        and bool(tunables.tunable("base_intake.clear_action_after_dispatch", True))
    ):
        data[intent.action_field] = None
    lark_call([
        "base", "+record-upsert",
        "--base-token", _base_token(),
        "--table-id", table_id,
        "--record-id", record_id,
        "--json", json.dumps(data, ensure_ascii=False),
    ], profile=profile)


def _writeback_status(record_id: str, *, table_id: str, profile: str,
                      intent: TaskStatusIntent, result: DispatchResult,
                      lark_call: Callable = lark.call) -> None:
    field = str(tunables.tunable("base_intake.writeback_field", "下发回执") or "").strip()
    if not field:
        return
    stamp = fmt_time_ms(now_ms())
    body = (
        f"{stamp} 已同步状态 {intent.task_id} -> {intent.status}: {result.message[:180]}"
        if result.ok else
        f"{stamp} 状态同步失败 {intent.task_id}: {result.message[:220]}"
    )
    data = {
        field: body,
        "状态": intent.status,
        "当前状态": intent.status,
    }
    if (
        intent.action_field
        and bool(tunables.tunable("base_intake.clear_action_after_dispatch", True))
    ):
        data[intent.action_field] = None
    lark_call([
        "base", "+record-upsert",
        "--base-token", _base_token(),
        "--table-id", table_id,
        "--record-id", record_id,
        "--json", json.dumps(data, ensure_ascii=False),
    ], profile=profile)


def handle_payload(payload: dict, *, profile: str = "",
                   lark_call: Callable = lark.call,
                   dispatch: Callable[[DispatchIntent], DispatchResult] | None = None) -> int:
    """Handle one raw Feishu event payload. Returns number of dispatches."""
    if not enabled() or not is_base_change_event(payload):
        return 0
    table_id = extract_table_id(payload)
    if table_id not in watched_table_ids():
        return 0
    record_ids = extract_record_ids(payload)
    if not record_ids:
        return 0

    records = fetch_records(record_ids, base_token=_base_token(),
                            table_id=table_id, profile=profile,
                            lark_call=lark_call)
    sent = 0
    with _locked():
        state = _load_state()
        seen = state.setdefault("records", {})
        for rid, fields in records:
            status_intent = build_task_status_intent(table_id, rid, fields)
            if status_intent is not None:
                key = f"status:{table_id}:{rid}"
                prev = seen.get(key) or {}
                event_id = _event_id(payload)
                same_fingerprint = prev.get("fingerprint") == status_intent.fingerprint
                if not same_fingerprint:
                    result = apply_task_status_intent(status_intent)
                    print(
                        "📋 base_intake status "
                        f"{table_id}/{rid} -> {status_intent.team_label}/"
                        f"{status_intent.task_id} {status_intent.status} ok={result.ok}"
                    )
                    seen[key] = {
                        "fingerprint": status_intent.fingerprint,
                        "dispatched_at": now_ms(),
                        "ok": result.ok,
                        "task_id": status_intent.task_id,
                        "event_id": event_id,
                    }
                    sent += 1
                    if bool(tunables.tunable("base_intake.writeback", True)):
                        try:
                            _writeback_status(
                                rid, table_id=table_id, profile=profile,
                                intent=status_intent, result=result,
                                lark_call=lark_call)
                        except Exception as e:
                            print(f"  ⚠️ base_intake status writeback failed for {rid}: {e}")
                continue
            intent = build_intent(table_id, rid, fields)
            if intent is None:
                continue
            key = f"{table_id}:{rid}"
            prev = seen.get(key) or {}
            event_id = _event_id(payload)
            same_fingerprint = prev.get("fingerprint") == intent.fingerprint
            same_event = bool(event_id) and prev.get("event_id") == event_id
            if same_fingerprint and (same_event or not intent.action_value):
                continue
            result = (dispatch or dispatch_intent)(intent)
            print(
                "📋 base_intake dispatched "
                f"{table_id}/{rid} -> {intent.team_label}/{intent.agent} "
                f"({result.task_id or 'no-task-id'}) ok={result.ok}"
            )
            seen[key] = {
                "fingerprint": intent.fingerprint,
                "dispatched_at": now_ms(),
                "ok": result.ok,
                "task_id": result.task_id,
                "event_id": event_id,
            }
            sent += 1
            if bool(tunables.tunable("base_intake.writeback", True)):
                try:
                    _writeback(rid, table_id=table_id, profile=profile, intent=intent,
                               result=result, lark_call=lark_call)
                except Exception as e:
                    print(f"  ⚠️ base_intake writeback failed for {rid}: {e}")
        _save_state(state)
    return sent
