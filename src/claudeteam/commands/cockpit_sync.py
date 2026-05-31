"""`claudeteam cockpit-sync` — project local facts -> boss cockpit rows.

One-shot and idempotent by design.  The command builds boss-readable rows
from each team's local state (`tasks.json`, `facts/status.json`, health)
and can write them to the Feishu boss cockpit table keyed by `战场`.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from claudeteam.commands import fleet_health, founder_os
from claudeteam.feishu import lark
from claudeteam.runtime import team_registry
from claudeteam.util import (
    env_str, error_exit, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, read_json, reject_extra_args,
)


DEFAULT_BASE_TOKEN = "Hjsibewe7aL9RmsYiUEcjq3bn3e"
DEFAULT_TABLE_ID = "tblEyoEGZOZ0gfJr"
DEFAULT_TASK_TABLE_ID = "tblJ67mLhY9oM91G"
DEFAULT_AGENT_TABLE_NAME = "员工状态明细"
_CST = timezone(timedelta(hours=8), name="CST")
_TERMINAL = {"已完成", "已取消"}
_BACKLOG = {"历史候选", "候选", "待归档", "已归档", "待排期"}
_STAGE_BY_ID = {stage["id"]: stage for stage in founder_os._STAGES}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_VIEWABLE_EXTS = _IMAGE_EXTS | {".pdf", ".md", ".txt", ".csv", ".json"}
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

USAGE = f"""usage: claudeteam cockpit-sync [--root <dir>] [--json] [--write]
                             [--base-token <token>] [--table-id <id>]
                             [--agent-table-id <id-or-name>]
                             [--task-table-id <id-or-name>]
                             [--upload-artifacts]
                             [--artifact-field-id <id-or-name>]
                             [--remote-state-dir <dir>]
                             [--profile <lark-profile>]
                             [--registry-script <path>] [--no-registry]
                             [team-dir ...]

Default target:
  base-token: {DEFAULT_BASE_TOKEN}
  table-id:   {DEFAULT_TABLE_ID}

Examples:
  claudeteam cockpit-sync --root /Users/wsm/Project
  claudeteam cockpit-sync --root /Users/wsm/Project --json
  claudeteam cockpit-sync --root /Users/wsm/Project --write --profile product-lab
  claudeteam cockpit-sync --root /Users/wsm/Project --write --agent-table-id {DEFAULT_AGENT_TABLE_NAME}
  claudeteam cockpit-sync --root /Users/wsm/Project --write --task-table-id {DEFAULT_TASK_TABLE_ID}
"""

_LABELS = {
    "product-lab": "Product Lab 本地",
    "todo002-study-coach": "TODO002 本地",
    "website-chuhai-team": "WebsiteChuhai",
    "work-assistant-team": "工作分身",
    "ClaudeTeam": "ClaudeTeam 系统",
}
_REMOTE_LABELS = {
    "product_lab_cloud": "Product Lab 云上",
    "product-lab-cloud": "Product Lab 云上",
    "todo002_cloud": "TODO002 云上",
    "todo002-study-coach-cloud": "TODO002 云上",
}


def _discover(root: Path) -> list[Path]:
    return fleet_health._discover(root)


def _label_for(team_dir: Path) -> str:
    return _LABELS.get(team_dir.name, team_dir.name)


def _tasks_path(team_dir: Path) -> Path:
    for rel in ("state/tasks.json", "tasks.json"):
        path = team_dir / rel
        if path.exists():
            return path
    return team_dir / "state" / "tasks.json"


def _status_path(team_dir: Path) -> Path:
    for rel in ("state/facts/status.json", "facts/status.json", "status.json"):
        path = team_dir / rel
        if path.exists():
            return path
    return team_dir / "state" / "facts" / "status.json"


def _config_path(team_dir: Path) -> Path:
    for name in ("claudeteam.toml", "claudeteam.cloud.toml", "config.toml"):
        path = team_dir / name
        if path.exists():
            return path
    return team_dir / "claudeteam.toml"


def _read_tasks(team_dir: Path) -> list[dict]:
    data = read_json(_tasks_path(team_dir), {"tasks": [], "_meta": {"last_id": 0}})
    rows = data.get("tasks", [])
    return rows if isinstance(rows, list) else []


def _read_statuses(team_dir: Path) -> list[dict]:
    data = read_json(_status_path(team_dir), {"agents": {}})
    agents = data.get("agents", {})
    if not isinstance(agents, dict):
        return []
    return [row for row in agents.values() if isinstance(row, dict)]


def _title(task: dict) -> str:
    return str(task.get("title") or task.get("id") or "untitled").strip()


def _clip(text: str, limit: int = 180) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _task_line(task: dict) -> str:
    tid = str(task.get("id") or "?")
    status = str(task.get("status") or "?")
    assignee = str(task.get("assignee") or "?")
    stage = _first_task_text(task, "founder_stage", "stage", "current_stage", "当前阶段")
    stage_part = f" / stage={stage}" if stage else ""
    return _clip(f"{tid} [{status}] {_title(task)} -> {assignee}{stage_part}", 220)


def _sort_tasks(tasks: list[dict]) -> list[dict]:
    def key(task: dict) -> tuple[int, int]:
        status = str(task.get("status") or "")
        rank = 0 if status == "进行中" else 1 if status == "待验收" else 2
        stamp = int(task.get("updated_at") or task.get("created_at") or 0)
        return (rank, -stamp)

    return sorted(tasks, key=key)


def _is_terminal_or_backlog(task: dict) -> bool:
    status = str(task.get("status") or "")
    return status in _TERMINAL or status in _BACKLOG


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M CST")


def _age_text(created_ms: int | None, now: datetime) -> str:
    if not created_ms:
        return "未知"
    started = datetime.fromtimestamp(int(created_ms) / 1000, _CST)
    delta = max(timedelta(0), now.astimezone(_CST) - started)
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    if days:
        return f"{days}天{hours}小时"
    if hours:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


def _latest_status(statuses: list[dict]) -> dict:
    if not statuses:
        return {}
    manager = [s for s in statuses if s.get("agent") == "manager"]
    pool = manager or statuses
    return max(pool, key=lambda s: int(s.get("updated_at") or 0))


def _read_team_agents(team_dir: Path) -> dict[str, dict]:
    path = _config_path(team_dir)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    team = data.get("team")
    if not isinstance(team, dict):
        return {}
    agents = team.get("agents")
    if not isinstance(agents, dict):
        return {}
    return {str(name): cfg for name, cfg in agents.items() if isinstance(cfg, dict)}


def _read_agents_from_config(path: Path) -> dict[str, dict]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    team = data.get("team")
    agents = team.get("agents") if isinstance(team, dict) else {}
    return {
        str(name): cfg for name, cfg in agents.items()
        if isinstance(name, str) and isinstance(cfg, dict)
    } if isinstance(agents, dict) else {}


def _first_task_text(task: dict, *keys: str) -> str:
    for key in keys:
        value = str(task.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_url(text: str) -> bool:
    return bool(_URL_RE.match(str(text or "").strip()))


def _resolve_artifact_path(team_dir: Path, artifact: str) -> Path | None:
    artifact = str(artifact or "").strip()
    if not artifact or _is_url(artifact):
        return None
    path = Path(artifact).expanduser()
    if path.is_absolute():
        return path
    return team_dir / path


def _clean_markdown_ref(raw: str) -> str:
    ref = raw.strip().strip("<>").strip()
    if not ref:
        return ""
    if ref[0] in {"'", '"'}:
        quote = ref[0]
        end = ref.find(quote, 1)
        return ref[1:end] if end > 1 else ref.strip(quote)
    return ref.split()[0]


def _markdown_image_refs(path: Path) -> list[Path]:
    if path.suffix.lower() != ".md" or not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    refs: list[Path] = []
    for match in _MARKDOWN_IMAGE_RE.finditer(text):
        raw_ref = _clean_markdown_ref(match.group(1))
        if not raw_ref or _is_url(raw_ref):
            continue
        ref_path = Path(raw_ref).expanduser()
        if not ref_path.is_absolute():
            ref_path = path.parent / ref_path
        if ref_path.exists() and ref_path.suffix.lower() in _IMAGE_EXTS:
            refs.append(ref_path)
    return refs


def _best_visible_artifact(path: Path | None) -> Path | None:
    if path is None or not path.exists():
        return None
    if path.is_dir():
        files = [item for item in path.rglob("*") if item.is_file()]
        for item in sorted(files):
            if item.suffix.lower() in _IMAGE_EXTS:
                return item
        for item in sorted(files):
            if item.suffix.lower() in _VIEWABLE_EXTS:
                return item
        return None
    image_refs = _markdown_image_refs(path)
    if image_refs:
        return image_refs[0]
    if path.suffix.lower() in _VIEWABLE_EXTS:
        return path
    return path


def _artifact_visibility(team_dir: Path, artifact: str) -> tuple[str, Path | None]:
    artifact = str(artifact or "").strip()
    if not artifact:
        return "无产物", None
    if _is_url(artifact):
        return "🔗 外链可打开", None
    path = _resolve_artifact_path(team_dir, artifact)
    if path is None or not path.exists():
        return "⚠️ 本地缺失", None
    return "📎 本地待上传", _best_visible_artifact(path)


def _boss_artifact_cell(artifact: str, visibility: str) -> str:
    artifact = str(artifact or "").strip()
    if _is_url(artifact):
        return artifact
    if visibility == "📎 本地待上传":
        return "见【老板可见产物】附件；若未显示附件，需运行上传产物"
    if visibility == "⚠️ 本地缺失":
        return "本地缺失，需员工补交可打开产物"
    return "无产物"


def _normalise_founder_stage(raw: str) -> str:
    stage = founder_os._normalise_stage(raw)
    if stage:
        return stage
    lowered = raw.strip().lower()
    for stage_id, stage_row in _STAGE_BY_ID.items():
        label = str(stage_row.get("label") or "").lower()
        if stage_id in lowered or label == lowered:
            return stage_id
    return ""


def _founder_os_fields(top: dict, *, active_count: int,
                       source_label: str = "本机") -> dict:
    if not active_count:
        no_active = f"无{source_label}未完成任务"
        return {
            "stage": "待接入",
            "exit_evidence": no_active,
            "evidence_action": f"{no_active}，可直接派工",
            "non_goal": no_active,
            "boss_question": "下一件事是否值得进入 Idea / MVP / Launch / Scale？",
            "status": "无活跃任务",
            "complete": True,
        }

    raw_stage = _first_task_text(
        top, "founder_stage", "stage", "current_stage", "当前阶段")
    stage_id = _normalise_founder_stage(raw_stage) if raw_stage else ""
    stage = _STAGE_BY_ID.get(stage_id, {})

    exit_evidence = _first_task_text(
        top, "stage_exit_evidence", "exit_evidence",
        "evidence", "阶段出口证据")
    evidence_action = _first_task_text(
        top, "evidence_action", "today_evidence_action",
        "today_action", "今天最小证据动作")
    non_goal = _first_task_text(
        top, "non_goal", "do_not", "not_doing", "不做什么")

    missing: list[str] = []
    if not stage_id:
        missing.append("当前阶段")
    if not exit_evidence:
        missing.append("阶段出口证据")
    if not evidence_action:
        missing.append("今天最小证据动作")
    if not non_goal:
        missing.append("不做什么")

    if stage_id:
        stage_label = str(stage.get("label") or raw_stage)
    elif raw_stage:
        stage_label = f"阶段值异常：{raw_stage}"
    else:
        stage_label = "待团队回写"

    return {
        "stage": stage_label,
        "exit_evidence": exit_evidence or "待团队回写：当前任务未声明阶段出口证据",
        "evidence_action": evidence_action or "待团队回写：当前任务未声明今天最小证据动作",
        "non_goal": non_goal or "待团队回写：当前任务未声明不做什么",
        "boss_question": str(stage.get("boss_question") or (
            "今天哪一个动作最能证明真实需求、使用、付费或可复制增长？")),
        "status": "已回写" if not missing else "待回写：" + "、".join(missing),
        "complete": not missing,
    }


def _progress_score(*, bad: int, warn: int, active: bool) -> int:
    if bad:
        return 20
    if warn:
        return 45
    if active:
        return 65
    return 100


def _operation_hint(action: str, *, target: str = "manager",
                    mode: str = "recheck") -> str:
    if mode == "new_task":
        return "派新任务：去【老板任务流】新增一行；或在本卡【老板决策】写一句话。"
    if mode == "decision":
        return "拍板：在【老板决策】写“接入，先审计”或“不接入，移除”。"
    if mode == "continue":
        return (
            f"催进度：下拉【老板操作】选「{action}」会下发到 {target}；"
            "改方向就在【老板决策】写一句话。"
        )
    return (
        f"下发：下拉【老板操作】选「{action}」会下发到 {target}；"
        "自定义要求写【老板决策】。"
    )


def _edit_hint(default_action: str = "") -> str:
    action = f"选「{default_action}」" if default_action else "选择一个动作"
    return (
        f"你只改这里：在【老板操作】{action}，或在【老板决策】写一句话；"
        "系统下发后会写【下发回执】。"
    )


def build_row(team_dir: Path, *, health: dict | None = None,
              now: datetime | None = None, label: str | None = None,
              source_label: str = "本机", fact_source: str = "") -> dict:
    """Build one Feishu field dict for a team directory."""
    now = now or datetime.now(_CST)
    label = label or _label_for(team_dir)
    health = health if health is not None else fleet_health._health_payload(team_dir)
    tasks = _read_tasks(team_dir)
    active = _sort_tasks([t for t in tasks if not _is_terminal_or_backlog(t)])
    done = _sort_tasks([t for t in tasks if t.get("status") in _TERMINAL])
    statuses = _read_statuses(team_dir)
    latest_status = _latest_status(statuses)

    bad = int(health.get("bad", 0) or 0)
    warn = int(health.get("warn", 0) or 0)
    issues = [str(i) for i in health.get("issues", []) if str(i).strip()]
    top = active[0] if active else {}
    active_count = len(active)
    founder = _founder_os_fields(
        top, active_count=active_count, source_label=source_label)
    founder_needs_backfill = active_count > 0 and not founder["complete"]

    if top:
        title = _title(top)
        owner = str(top.get("assignee") or "manager")
        current_action = title
        current_step = f"{top.get('id', '?')} / {top.get('status', '?')} / owner={owner}"
        task_path = str(top.get("artifact_path") or team_dir / "tasks")
        duration = _age_text(top.get("created_at"), now)
    else:
        title = "可接单"
        owner = str(latest_status.get("agent") or "manager")
        current_action = f"无{source_label}未完成任务，可接单"
        current_step = str(latest_status.get("task") or "ready")
        task_path = str(_tasks_path(team_dir))
        duration = "无进行中任务"

    founder_backfill_action = (
        f"同时让 {owner} 给 {top.get('id', '?')} 补 Founder OS 字段："
        "当前阶段、出口证据、今天最小证据动作、不做什么。"
        if founder_needs_backfill else ""
    )

    if bad:
        state_column = "有阻塞"
        boss_group = "现在要你决定"
        suggested_action = "重新核验"
        operation_hint = _operation_hint(suggested_action)
        health_lamp = "红｜健康红项需处理"
        risk = "红｜缺真实反馈"
        verification = "过期需重核"
        current_status = "阻塞"
        boss_action = (
            f"{operation_hint} 复核后先处理健康红项；必要时重启 team/router/watchdog。"
            + (f" {founder_backfill_action}" if founder_backfill_action else "")
        )
    elif warn:
        state_column = "待核验"
        boss_group = "先催团队回执"
        suggested_action = "重新核验"
        operation_hint = _operation_hint(suggested_action)
        health_lamp = f"黄｜{source_label}状态已过期待重核"
        risk = f"黄｜{source_label}状态已过期待重核"
        verification = "过期需重核"
        current_status = "待核验"
        boss_action = (
            f"{operation_hint} 要求 manager 做 live health + 任务回执。"
            + (f" {founder_backfill_action}" if founder_backfill_action else "")
        )
    elif founder_needs_backfill:
        state_column = "待核验"
        boss_group = "先补阶段证据"
        suggested_action = "重新核验"
        operation_hint = _operation_hint(suggested_action)
        health_lamp = "黄｜Founder OS 元数据待回写"
        risk = "黄｜阶段/证据字段不完整"
        verification = "任务证据待回写"
        current_status = "待核验"
        boss_action = f"{operation_hint} {founder_backfill_action}"
    elif active_count:
        state_column = "执行中"
        boss_group = "等团队结果"
        suggested_action = "继续执行"
        operation_hint = _operation_hint("继续执行", mode="continue")
        health_lamp = f"绿｜{source_label}任务与状态已核验"
        risk = f"绿｜{source_label}任务与状态已核验"
        verification = f"{source_label}事实已核验"
        current_status = "执行中"
        boss_action = f"暂无；等团队按下次汇报回执。{operation_hint}"
    else:
        state_column = "待接入"
        boss_group = "暂不处理"
        suggested_action = "派新任务"
        operation_hint = _operation_hint(suggested_action, mode="new_task")
        health_lamp = f"绿｜{source_label}任务与状态已核验"
        risk = f"绿｜{source_label}任务与状态已核验"
        verification = f"{source_label}事实已核验"
        current_status = "待接入"
        boss_action = operation_hint

    blocker = "; ".join(issues[:3]) if issues else str(latest_status.get("blocker") or "")
    task_list = "\n".join(_task_line(t) for t in active[:5]) or f"无{source_label}未完成任务"
    finished = "\n".join(_task_line(t) for t in done[:3]) or "无新完成播报"
    waiting_review_tasks = [t for t in active if str(t.get("status") or "") == "待验收"]
    evidence_gap_tasks = [
        t for t in active
        if not str(t.get("artifact_path") or "").strip()
        or not _founder_os_fields(
            t, active_count=active_count, source_label=source_label)["complete"]
    ]
    next_report = now + (timedelta(minutes=30) if (bad or warn or active_count)
                         else timedelta(hours=4))

    stuck = (
        "健康红项" if bad else
        "需重核" if warn else
        "Founder OS 字段待回写" if founder_needs_backfill else
        "未发现明显卡住"
    )
    boss_need = (
        "需你处理" if bad else
        "需团队回执" if warn else
        "需补阶段证据" if founder_needs_backfill else
        "暂无需你出手"
    )
    boss_one_liner = (
        f"{label}: {current_status}，{active_count} 个{source_label}活跃任务；"
        f"{boss_need}。"
    )
    fact_source = fact_source or (
        f"health={_config_path(team_dir)}; "
        f"tasks={_tasks_path(team_dir)}; "
        f"status={_status_path(team_dir)}"
    )

    return {
        "战场": label,
        "负责人团队": label,
        "负责人agent": owner,
        "当前动作": _clip(current_action, 240),
        "当前步骤": _clip(current_step, 240),
        "阶段": _clip(founder["stage"], 120),
        "阶段出口证据": _clip(founder["exit_evidence"], 240),
        "今天最小证据动作": _clip(founder["evidence_action"], 240),
        "不做什么": _clip(founder["non_goal"], 240),
        "Founder OS 状态": founder["status"],
        "阶段老板问题": _clip(founder["boss_question"], 240),
        "当前状态": current_status,
        "产物": str(top.get("artifact_path") or "") if top else "",
        "阻塞": _clip(blocker or "无", 240),
        "是否需要老板": "是" if (bad or warn or founder_needs_backfill) else "否",
        "需要老板做什么": _clip(boss_action, 240),
        "下次汇报": _fmt_time(next_report),
        "最后更新时间": _fmt_time(now),
        "健康灯": health_lamp,
        "进度": _progress_score(
            bad=bad, warn=warn + int(founder_needs_backfill),
            active=bool(active_count)),
        "状态分栏": state_column,
        "任务路径": task_path,
        "完成播报": finished,
        "步骤条": "健康核验 -> 任务对账 -> 写回驾驶舱 -> 下次回报",
        "进度任务标签": _clip(f"{label}｜{title}", 120),
        "老板动作标签": suggested_action,
        "建议操作": _clip(_edit_hint(suggested_action), 240),
        "风险详情": risk,
        "核验状态": verification,
        "本机可见活跃任务数": active_count,
        "待验收任务数": len(waiting_review_tasks),
        "证据缺口数": len(evidence_gap_tasks),
        "待验收代表任务": _task_line(waiting_review_tasks[0]) if waiting_review_tasks else "",
        "证据缺口代表任务": _task_line(evidence_gap_tasks[0]) if evidence_gap_tasks else "",
        "事实类型": source_label,
        "事实来源": fact_source,
        "任务清单": task_list,
        "进行时长": duration,
        "卡住判断": stuck,
        "老板一句话": _clip(boss_one_liner, 240),
        "老板分组": boss_group,
        "老板下一步": _clip(boss_action, 240),
    }


def _registry_row(source: dict, *, now: datetime) -> dict:
    label = str(source.get("label") or source.get("key") or "未命名团队")
    key = str(source.get("key") or "")
    status = str(source.get("status") or "待核验")
    chat_id = str(source.get("chat_id") or "")
    config_path = str(source.get("config_path") or "")
    notes = str(source.get("notes") or "")
    agent_count = int(source.get("agent_count") or 0)
    cron_summary = str(source.get("cron_summary") or "")
    cron_attention = str(source.get("cron_attention") or "")
    cron_latest = str(source.get("cron_latest") or "")
    cron_dedupe = str(source.get("cron_dedupe") or "")
    needs_boss = key == "smart_partner"

    if key == "local_openclaw":
        ready = "网关可达" in status and "模型已对齐" in status
        state_column = "运行中" if ready else "待核验"
        current_status = (
            "已接入｜定时任务异常" if ready and cron_attention
            else "已接入" if ready else "需处理"
        )
        phase = "本机 OpenClaw 运行态"
        suggested_action = "继续执行" if ready else "恢复执行"
        operation_hint = _operation_hint(
            suggested_action,
            target="Product Lab manager",
            mode="continue" if ready else "recheck",
        )
        founder_status = "本机 OpenClaw 已登记"
        health_lamp = (
            "黄｜OpenClaw 可用，cron 异常" if ready and cron_attention
            else "绿｜OpenClaw 可用" if ready else f"黄｜{status}"
        )
        risk = (
            "黄｜OpenClaw cron 产出未闭环" if cron_attention
            else "绿｜本机可控" if ready else "黄｜本机 OpenClaw 待恢复"
        )
        verification = "cron需处理" if cron_attention else ("已核验" if ready else "过期需重核")
        boss_group = "先催团队回执" if cron_attention else ("有内容先看这里" if ready else "先催团队回执")
        boss_action = (
            f"{operation_hint} 也可以直接在【老板决策】写要 OpenClaw 做什么；"
            "系统会交给 Product Lab manager 代管本机 OpenClaw，不混入飞书智能伙伴。"
        )
        if cron_attention:
            if cron_dedupe or "去重" in cron_latest or "增量" in cron_latest:
                boss_action = (
                    f"{boss_action} 当前建议：去重规则已启用；下一步重跑验证，"
                    "若仍失败只处理飞书多维表写入权限。"
                )
            else:
                boss_action = (
                    f"{boss_action} 当前建议：修复 OpenClaw 定时任务的飞书投递目标/表格写入权限，"
                    "然后重跑并把产出回写驾驶舱。"
                )
        evidence_action = (
            "核验本机 openclaw status/models list/cron list/cron runs，"
            "确认网关、默认模型、Feishu channel 和定时任务产出。"
        )
        if cron_dedupe:
            evidence_action = f"{evidence_action} Toolify cron 增量去重：{cron_dedupe}。"
        exit_evidence = "网关可达，默认模型为 codex/gpt-5.5，并有可重复核验的本机状态。"
        non_goal = "不把本地 OpenClaw 与飞书智能伙伴合并；不自动新建飞书 App。"
        blocker = cron_attention or ("无" if ready else status)
        cron_text = "；".join(x for x in [cron_summary, cron_dedupe and f"增量去重 {cron_dedupe}", cron_latest] if x)
        task_list = (
            "本机 OpenClaw main agent；老板决策会交给 Product Lab manager 处理。"
            f"{f' {cron_text}' if cron_text else ''}"
        )
        duration = "本机网关在线" if ready else "等待恢复后重新计时"
        stuck = "定时任务未闭环" if cron_attention else ("未卡住" if ready else "网关/模型待恢复")
        boss_sentence = (
            f"{label}: {current_status}；"
            f"{'有定时任务产出异常，需处理。' if cron_attention else '可直接写老板决策让 Product Lab manager 代管本机 OpenClaw。'}"
        )
        needs_boss = bool(cron_attention)
        progress = 65 if cron_attention else (80 if ready else 55)
    elif key == "smart_partner":
        state_column = "需要老板动作"
        current_status = "待接入"
        phase = "待决定是否纳入"
        suggested_action = "接入/不接入决策"
        operation_hint = _operation_hint(suggested_action, mode="decision")
        founder_status = "非 ClaudeTeam 账本"
        health_lamp = "灰｜未接入 ClaudeTeam 审计"
        risk = "灰｜未接入"
        verification = "未接入"
        boss_group = "现在要你决定"
        boss_action = operation_hint
        evidence_action = "确认智能伙伴是否需要任务账本、阶段证据和驾驶舱回写。"
        exit_evidence = "有明确接入/不接入决策；若接入，产生可审计任务账本。"
        non_goal = "不把普通聊天记录伪装成 ClaudeTeam 已审计任务。"
        blocker = "需接入决策"
        task_list = "非 ClaudeTeam 任务账本"
        duration = "未接入本机任务账本"
        stuck = "待接入决策"
        boss_sentence = f"{label}: {current_status}；需要你决定是否接入。"
        progress = 20
    else:
        state_column = "待核验"
        current_status = "待核验"
        phase = "待远端回写"
        suggested_action = "重新核验"
        operation_hint = _operation_hint(suggested_action)
        founder_status = "需远端账本回写"
        health_lamp = f"黄｜{status}"
        risk = "黄｜云机未核验"
        verification = "过期需重核"
        boss_group = "先催团队回执"
        boss_action = (
            f"{operation_hint} 催对应云上 manager 运行 Founder OS 审计并回写阶段证据；"
            "未回写前不当作已核验。"
        )
        evidence_action = "远端运行 founder-os audit，回写当前阶段、出口证据、今天最小证据动作和不做什么。"
        exit_evidence = "云上 manager 给出真实 health、任务账本审计结果和下次回报时间。"
        non_goal = "不把本机注册表状态当作云机真实运行结果。"
        blocker = status
        task_list = "非本机账本；等待远端回写"
        duration = "未接入本机任务账本"
        stuck = "远端未回写"
        boss_sentence = f"{label}: {current_status}；需远端 manager 回写账本。"
        progress = 45

    return {
        "战场": label,
        "负责人团队": label,
        "负责人agent": "manager" if agent_count else "待接入",
        "当前动作": _clip(status or notes or "待核验", 240),
        "当前步骤": _clip(
            f"registry={key or '?'} / chat_id={'已登记' if chat_id else '缺失'} / "
            f"agents={agent_count}", 240),
        "阶段": phase,
        "阶段出口证据": exit_evidence,
        "今天最小证据动作": evidence_action,
        "不做什么": non_goal,
        "Founder OS 状态": founder_status,
        "阶段老板问题": "这个团队是否已有可审计任务账本，还是只是注册表占位？",
        "当前状态": current_status,
        "产物": "",
        "阻塞": _clip(blocker, 240),
        "是否需要老板": "是" if needs_boss else "否",
        "需要老板做什么": _clip(boss_action, 240),
        "下次汇报": _fmt_time(now + timedelta(minutes=30)),
        "最后更新时间": _fmt_time(now),
        "健康灯": health_lamp,
        "进度": progress,
        "状态分栏": state_column,
        "任务路径": config_path or "registry-only",
        "完成播报": "无远端完成播报",
        "步骤条": "注册表核验 -> 远端账本审计 -> 写回驾驶舱 -> 下次回报",
        "进度任务标签": _clip(f"{label}｜{phase}", 120),
        "老板动作标签": suggested_action,
        "建议操作": _clip(_edit_hint(
            "" if key == "smart_partner" else suggested_action), 240),
        "风险详情": risk,
        "核验状态": verification,
        "本机可见活跃任务数": 0,
        "事实类型": "注册表",
        "事实来源": (
            f"registry={source.get('config_path') or 'team-registry'}; "
            f"notes={notes}"
        ),
        "任务清单": task_list,
        "进行时长": duration,
        "卡住判断": stuck,
        "老板一句话": _clip(boss_sentence, 240),
        "老板分组": boss_group,
        "老板下一步": _clip(boss_action, 240),
    }


def _local_config_paths(team_dirs: list[Path]) -> set[str]:
    return {str((path / "claudeteam.toml").resolve()) for path in team_dirs}


def _registry_rows(root: Path, team_dirs: list[Path], *,
                   registry_script: Path | None = None,
                   occupied_labels: set[str] | None = None,
                   now: datetime | None = None,
                   sources: list[dict] | None = None,
                   run: Callable = subprocess.run) -> list[dict]:
    script = registry_script or team_registry.default_script(root)
    sources = sources if sources is not None else team_registry.load(script, run=run)
    local_configs = _local_config_paths(team_dirs)
    local_labels = {_label_for(path) for path in team_dirs}
    local_labels.update(occupied_labels or set())
    rows: list[dict] = []
    for source in sources:
        label = str(source.get("label") or "")
        config_path = str(source.get("config_path") or "")
        if label in local_labels:
            continue
        if config_path and str(Path(config_path).expanduser().resolve()) in local_configs:
            continue
        rows.append(_registry_row(source, now=now or datetime.now(_CST)))
    return rows


def _default_remote_state_dir(root: Path) -> Path | None:
    for candidate in (
        root / "product-lab" / "state" / "remote-teams",
        root / "state" / "remote-teams",
    ):
        if candidate.exists():
            return candidate
    return None


def _read_remote_meta(remote_dir: Path) -> dict:
    data = read_json(remote_dir / "meta.json", {})
    return data if isinstance(data, dict) else {}


def _remote_snapshot_dirs(remote_state_dir: Path | None) -> list[Path]:
    if remote_state_dir is None or not remote_state_dir.exists():
        return []
    try:
        children = sorted(remote_state_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    rows: list[Path] = []
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (
            _config_path(child).exists()
            or _tasks_path(child).exists()
            or _status_path(child).exists()
            or (child / "meta.json").exists()
        ):
            rows.append(child)
    return rows


def _remote_label(remote_dir: Path, *,
                  registry_labels: dict[str, str] | None = None) -> str:
    meta = _read_remote_meta(remote_dir)
    key = str(meta.get("key") or remote_dir.name)
    label = str(meta.get("label") or "").strip()
    if label:
        return label
    if registry_labels and registry_labels.get(key):
        return registry_labels[key]
    return _REMOTE_LABELS.get(key, remote_dir.name)


def _snapshot_health(remote_dir: Path, *, now: datetime) -> dict:
    health = read_json(remote_dir / "health.json", {})
    if isinstance(health, dict) and any(k in health for k in ("ok", "bad", "warn")):
        issues = health.get("issues")
        if not isinstance(issues, list):
            issues = [
                line for line in health.get("lines", [])
                if isinstance(line, str) and ("❌" in line or "⚠️" in line)
            ] if isinstance(health.get("lines"), list) else []
        return {
            "team": remote_dir.name,
            "path": str(remote_dir),
            "ok": bool(health.get("ok")),
            "bad": int(health.get("bad", 0) or 0),
            "warn": int(health.get("warn", 0) or 0),
            "issues": [str(i) for i in issues[:5]],
        }

    statuses = _read_statuses(remote_dir)
    if not statuses:
        return {
            "team": remote_dir.name,
            "path": str(remote_dir),
            "ok": False,
            "bad": 0,
            "warn": 1,
            "issues": [f"⚠️ 云上快照缺员工心跳: {_status_path(remote_dir)}"],
        }
    latest = max(int(s.get("updated_at") or 0) for s in statuses)
    if not latest:
        return {
            "team": remote_dir.name,
            "path": str(remote_dir),
            "ok": False,
            "bad": 0,
            "warn": 1,
            "issues": ["⚠️ 云上快照没有 updated_at，需远端重新回写"],
        }
    updated = datetime.fromtimestamp(latest / 1000, _CST)
    stale = now.astimezone(_CST) - updated > timedelta(hours=2)
    return {
        "team": remote_dir.name,
        "path": str(remote_dir),
        "ok": not stale,
        "bad": 0,
        "warn": 1 if stale else 0,
        "issues": (
            [f"⚠️ 云上员工心跳已过期: {_fmt_time(updated)}"]
            if stale else []
        ),
    }


def _remote_fact_source(remote_dir: Path) -> str:
    meta = _read_remote_meta(remote_dir)
    fetched_at = str(meta.get("fetched_at") or meta.get("updated_at") or "").strip()
    parts = [f"remote_snapshot={remote_dir}"]
    if fetched_at:
        parts.append(f"fetched_at={fetched_at}")
    parts.extend([
        f"config={_config_path(remote_dir)}",
        f"tasks={_tasks_path(remote_dir)}",
        f"status={_status_path(remote_dir)}",
    ])
    return "; ".join(parts)


def _is_cloud_source(source: dict) -> bool:
    key = str(source.get("key") or "")
    label = str(source.get("label") or "")
    return "cloud" in key or "云上" in label


def _registry_config_agent_rows(sources: list[dict], *,
                                occupied_labels: set[str],
                                now: datetime) -> list[dict]:
    """Show configured cloud staff even before live status snapshots arrive."""
    rows: list[dict] = []
    for source in sources:
        if not _is_cloud_source(source):
            continue
        label = str(source.get("label") or source.get("key") or "").strip()
        if not label or label in occupied_labels:
            continue
        config_path = Path(str(source.get("config_path") or "")).expanduser()
        if not config_path.exists():
            continue
        agents = _read_agents_from_config(config_path)
        if not agents:
            continue
        rows.extend(build_agent_rows(
            config_path.parent, now=now, label=label,
            configured=agents, tasks=[], statuses=[]))
    return rows


def _agent_status_for(statuses: list[dict], agent: str) -> dict:
    rows = [s for s in statuses if s.get("agent") == agent]
    if not rows:
        return {}
    return max(rows, key=lambda s: int(s.get("updated_at") or 0))


def _active_agent_tasks(tasks: list[dict], agent: str) -> list[dict]:
    return _sort_tasks([
        t for t in tasks
        if str(t.get("assignee") or "") == agent and not _is_terminal_or_backlog(t)
    ])


def _backlog_agent_tasks(tasks: list[dict], agent: str) -> list[dict]:
    return _sort_tasks([
        t for t in tasks
        if str(t.get("assignee") or "") == agent and str(t.get("status") or "") in _BACKLOG
    ])


def _status_age(updated_ms: int | None, now: datetime) -> tuple[str, bool]:
    if not updated_ms:
        return "未知", True
    updated = datetime.fromtimestamp(int(updated_ms) / 1000, _CST)
    delta = max(timedelta(0), now.astimezone(_CST) - updated)
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}分钟", minutes > 30
    hours = minutes // 60
    if hours < 24:
        return f"{hours}小时{minutes % 60}分钟", hours >= 2
    return f"{hours // 24}天{hours % 24}小时", True


def _load_level(active_count: int, *, blocker: str, stale: bool) -> str:
    if blocker:
        return "卡住求援"
    if stale:
        return "状态过期"
    if active_count <= 0:
        return "空闲"
    if active_count == 1:
        return "手头有活"
    if active_count <= 3:
        return "稍显忙碌"
    return "忙到起飞"


def _mental_state(load: str, *, raw_status: str, blocker: str) -> str:
    if blocker:
        return "卡住，需要主管介入"
    if load == "状态过期":
        return "状态过期，先重核"
    if load == "忙到起飞":
        return "过载风险，建议降载"
    if load == "稍显忙碌":
        return "忙但可控"
    if load == "手头有活":
        return "专注执行"
    if load == "等验收":
        return "已交活，等主管看一眼"
    if "待命" in raw_status or "lazy" in raw_status:
        return "待命稳定，可唤醒"
    return "在线稳定，可接活"


def _can_take_work(load: str) -> str:
    if load in {"空闲", "状态过期"}:
        return "可接新活（先唤醒/核验）" if load == "状态过期" else "可接新活"
    if load == "等验收":
        return "可少量接"
    if load == "手头有活":
        return "可少量接"
    if load == "稍显忙碌":
        return "谨慎加活"
    return "不要再加"


def _status_task_is_work(raw_status: str, task_text: str) -> bool:
    task = task_text.strip()
    if not task:
        return False
    idle_markers = {
        "ready", "待命", "无当前任务", "无", "none", "idle",
        "cloud-smoke-ok",
    }
    if task.lower() in idle_markers:
        return False
    if "待命" in raw_status or "lazy" in raw_status:
        return False
    return True


def _light_status(load: str) -> str:
    return {
        "空闲": "可接活",
        "手头有活": "专注中",
        "等验收": "等验收",
        "稍显忙碌": "小忙一下",
        "忙到起飞": "满满当当",
        "卡住求援": "需要帮忙",
        "状态过期": "状态待确认",
    }.get(load, load or "先看看")


def _boss_people_group(load: str, *, active_count: int, blocker: str) -> str:
    if blocker or active_count > 0 or load in {
        "手头有活", "等验收", "稍显忙碌", "忙到起飞", "卡住求援",
    }:
        return "01 有活 / 要看"
    if load == "空闲":
        return "02 空闲可派"
    return "03 待唤醒确认"


def _agent_card_name(label: str, agent: str, identity: str) -> str:
    suffix = "主管" if identity == "主管" else "伙伴"
    return f"{label} / {agent} · {suffix}"


def _boss_focus(agent: str, load: str, active_count: int, *,
                judgement: str, current_task: str) -> str:
    if agent == "manager":
        if load == "空闲":
            return "主管空着，可以接新决策或做验收。"
        if active_count >= 3:
            return "主管手上偏多，适合让他转派一件给空闲员工。"
        return "主管在看场，等他给回执或收口。"
    if load == "空闲":
        return "这位现在比较松，可以接一件小活。"
    if load == "等验收":
        return "活已经交出来了，等主管验收；可以少量接新活。"
    if load == "状态过期":
        return "先轻轻唤醒确认状态，再决定要不要派活。"
    if load == "卡住求援":
        return "这里需要帮一把，最好让主管先看。"
    if load == "忙到起飞":
        return "今天别再加活了，适合等他收口。"
    if load == "稍显忙碌":
        return "可以催收口，不建议临时塞大任务。"
    if current_task and current_task != "无当前任务":
        return _clip(f"正在处理：{current_task}", 120)
    return judgement


def _task_ids(rows: list[dict], limit: int = 3) -> str:
    ids = [str(t.get("id") or "?") for t in rows[:limit]]
    suffix = "…" if len(rows) > limit else ""
    return "、".join(ids) + suffix


def _looks_waiting_on_boss(task: dict) -> bool:
    blob = " ".join(
        str(task.get(key) or "")
        for key in (
            "status", "title", "description", "evidence_action",
            "stage_exit_evidence", "non_goal",
        )
    )
    markers = (
        "等老板", "待老板", "需要老板", "老板回复", "老板拍板", "老板确认",
        "等你", "需要你", "待你", "授权", "验证码", "登录", "支付", "审批",
    )
    return any(marker in blob for marker in markers)


def _closure_reason(active: list[dict], *, blocker: str) -> str:
    if blocker:
        return _clip(f"卡住了：{blocker}", 180)
    if not active:
        return "没有未收口"
    boss_wait = [t for t in active if _looks_waiting_on_boss(t)]
    review = [t for t in active if str(t.get("status") or "") == "待验收"]
    pending = [t for t in active if str(t.get("status") or "") in {"待处理", "待下发"}]
    doing = [t for t in active if str(t.get("status") or "") == "进行中"]
    parts: list[str] = []
    if boss_wait:
        parts.append(f"等老板回复：{_task_ids(boss_wait)}")
    if review:
        parts.append(f"等主管验收：{_task_ids(review)}")
    if pending:
        parts.append(f"主管未启动/未分派：{_task_ids(pending)}")
    if doing:
        parts.append(f"执行中未收口：{_task_ids(doing)}")
    return _clip("；".join(parts) or f"未收口：{_task_ids(active)}", 240)


def _manager_judgement(agent: str, load: str, *, role: str, manager_active: int,
                       free_workers: int, overloaded_workers: int,
                       backlog_count: int) -> str:
    is_manager = agent == "manager" or role == "主管"
    if is_manager:
        if manager_active >= 3 and free_workers:
            return "主管背活偏多，应转派给空闲员工"
        if overloaded_workers:
            return "有员工过载，主管需重新分派"
        if manager_active == 0:
            return "主管空闲，可接收新决策或做验收"
        return "主管有活，分派基本到位"
    if load == "忙到起飞":
        return "主管需降载或拆给其他人"
    if load == "空闲":
        return "可被主管分派"
    if backlog_count and load in {"空闲", "手头有活"}:
        return "有旧账候选，不算当前负荷"
    return "分派正常"


def build_agent_rows(team_dir: Path, *, now: datetime | None = None,
                     label: str | None = None,
                     configured: dict[str, dict] | None = None,
                     tasks: list[dict] | None = None,
                     statuses: list[dict] | None = None) -> list[dict]:
    """Build per-agent cockpit rows for boss-level workload inspection."""
    now = now or datetime.now(_CST)
    label = label or _label_for(team_dir)
    configured = configured if configured is not None else _read_team_agents(team_dir)
    tasks = tasks if tasks is not None else _read_tasks(team_dir)
    statuses = statuses if statuses is not None else _read_statuses(team_dir)
    names = set(configured)
    names.update(str(s.get("agent") or "") for s in statuses)
    names.update(str(t.get("assignee") or "") for t in tasks)
    names.discard("")

    active_by_agent = {
        name: _active_agent_tasks(tasks, name)
        for name in names
    }
    manager_active = len(active_by_agent.get("manager", []))
    free_workers = sum(
        1 for name, rows in active_by_agent.items()
        if name.startswith("worker") and not rows
    )
    overloaded_workers = sum(
        1 for name, rows in active_by_agent.items()
        if name.startswith("worker") and len(rows) >= 4
    )

    rows: list[dict] = []
    for agent in sorted(names, key=lambda n: (n != "manager", n)):
        cfg = configured.get(agent, {})
        role_text = str(cfg.get("role") or ("主管" if agent == "manager" else "员工"))
        identity = "主管" if agent == "manager" or "主管" in role_text else "员工"
        status = _agent_status_for(statuses, agent)
        raw_status = str(status.get("status") or ("未启动" if agent in configured else "未登记"))
        blocker = str(status.get("blocker") or "").strip()
        active = active_by_agent.get(agent, [])
        backlog = _backlog_agent_tasks(tasks, agent)
        active_count = len(active)
        waiting_review = len([t for t in active if str(t.get("status") or "") == "待验收"])
        in_progress = len([t for t in active if str(t.get("status") or "") == "进行中"])
        hand_count = max(0, active_count - waiting_review)
        age, stale = _status_age(status.get("updated_at"), now)
        status_task = str(status.get("task") or "")
        status_task_active = (
            not stale and not blocker and not active
            and _status_task_is_work(raw_status, status_task)
        )
        effective_hand_count = hand_count + (1 if status_task_active else 0)
        effective_in_progress = in_progress + (1 if status_task_active else 0)
        load = (
            "等验收" if waiting_review and hand_count == 0 and not blocker and not stale
            else _load_level(effective_hand_count, blocker=blocker, stale=stale)
        )
        mental = _mental_state(load, raw_status=raw_status, blocker=blocker)
        can_take = _can_take_work(load)
        current_task = (
            "\n".join(_task_line(t) for t in active[:3])
            or str(status.get("task") or "")
            or "无当前任务"
        )
        suggested = (
            _edit_hint("唤醒员工")
            if load in {"空闲", "状态过期"} else
            _edit_hint("继续执行")
        )
        judgement = _manager_judgement(
            agent, load, role=identity, manager_active=manager_active,
            free_workers=free_workers, overloaded_workers=overloaded_workers,
            backlog_count=len(backlog))
        light_status = _light_status(load)
        boss_people_group = _boss_people_group(
            load, active_count=active_count, blocker=blocker)
        card_name = _agent_card_name(label, agent, identity)
        boss_focus = _boss_focus(
            agent, load, hand_count, judgement=judgement,
            current_task=_clip(current_task, 120))
        closure_reason = _closure_reason(active, blocker=blocker)
        next_action = (
            f"请 {agent} 汇报当前任务、卡点、下一次回报时间。"
            if load != "空闲" else
            f"请 {agent} 确认 ready，并等待主管派单。"
        )
        rows.append({
            "名片": f"{label}/{agent}",
            "员工名片": card_name,
            "战场": label,
            "负责人团队": label,
            "员工": agent,
            "负责人agent": agent,
            "身份": identity,
            "角色": _clip(role_text, 120),
            "工作状态": load,
            "轻松状态": light_status,
            "老板看人分组": boss_people_group,
            "精神状态": mental,
            "老板看点": _clip(boss_focus, 240),
            "未收口原因": closure_reason,
            "手头任务数": effective_hand_count,
            "进行中任务数": effective_in_progress,
            "待验收任务数": waiting_review,
            "候选/旧账数": len(backlog),
            "当前任务": _clip(current_task, 500),
            "当前状态": load,
            "下一步动作": _clip(next_action, 240),
            "是否可接新活": can_take,
            "主管调度判断": _clip(judgement, 240),
            "建议操作": _clip(suggested, 240),
            "最近心跳": (
                _fmt_time(datetime.fromtimestamp(int(status.get("updated_at")) / 1000, _CST))
                if status.get("updated_at") else "未知"
            ),
            "心跳年龄": age,
            "阻塞": _clip(blocker or "无", 240),
            "任务清单": "\n".join(_task_line(t) for t in active[:5]) or "无当前活跃任务",
        })
    return rows


def build_all_agent_rows(team_dirs: list[Path], *,
                         now: datetime | None = None,
                         labels: dict[Path, str] | None = None) -> list[dict]:
    rows: list[dict] = []
    for team_dir in team_dirs:
        rows.extend(build_agent_rows(
            team_dir, now=now, label=(labels or {}).get(team_dir)))
    return rows


def _task_card_relevant(task: dict, *, now: datetime, recent_days: int = 2) -> bool:
    status = str(task.get("status") or "")
    if status in _BACKLOG:
        return False
    if status not in _TERMINAL:
        return True
    stamp = int(task.get("updated_at") or task.get("completed_at")
                or task.get("created_at") or 0)
    if not stamp:
        return False
    updated = datetime.fromtimestamp(stamp / 1000, _CST)
    return now.astimezone(_CST) - updated <= timedelta(days=recent_days)


def _task_card_sort_key(row: dict) -> tuple[int, int]:
    status = str(row.get("status") or "")
    order = {
        "待验收": 0,
        "进行中": 1,
        "待处理": 2,
        "待下发": 2,
        "已完成": 3,
        "已取消": 4,
    }.get(status, 5)
    stamp = int(row.get("updated_at") or row.get("created_at") or 0)
    return (order, -stamp)


def _task_boss_focus(task: dict) -> str:
    status = str(task.get("status") or "")
    artifact = str(task.get("artifact_path") or "")
    if status == "待验收":
        return "员工已交活，等主管/老板看产物并决定是否继续收细节。"
    if status == "进行中":
        return "正在推进，重点看下一次回报和是否有真实产物。"
    if status in {"待处理", "待下发"}:
        return "已入账，等负责人启动或拆给具体员工。"
    if status == "已完成":
        return "已收口，可点详情看结论和产物。"
    if status == "已取消":
        return "已取消，不再推进。"
    if artifact:
        return "有产物记录，建议点开详情核对。"
    return "待负责人补充进展。"


def _task_unclosed_reason(task: dict) -> str:
    status = str(task.get("status") or "")
    if status in _TERMINAL:
        return "没有未收口"
    if _looks_waiting_on_boss(task):
        return "等老板确认/拍板"
    if status == "待验收":
        return "等主管验收"
    if status in {"待处理", "待下发"}:
        return "负责人未启动/未分派"
    if status == "进行中":
        return "执行中未收口"
    return "需要负责人补状态"


def _task_boss_next_action(task: dict) -> str:
    status = str(task.get("status") or "")
    assignee = str(task.get("assignee") or "manager")
    if status == "待验收":
        return "点开产物看一下；认可就让主管关单，不认可就在【老板决策】写调整意见。"
    if status in {"待处理", "待下发"}:
        return f"把【老板操作】改成「继续执行」会提醒 {assignee} 启动/分派。"
    if status == "进行中":
        return "等下一次回报；要改方向就在【老板决策】写一句话。"
    if status == "已完成":
        return "无需操作；如要复盘或继续加任务，在【老板决策】写新要求。"
    return "需要时在【老板决策】写新要求。"


def _task_boss_lane(task: dict) -> str:
    status = str(task.get("status") or "")
    if status in _TERMINAL:
        return "90 已收口"
    if _looks_waiting_on_boss(task):
        return "01 等我拍板"
    if status == "待验收":
        return "02 等验收"
    if status == "进行中":
        return "03 进行中"
    if status in {"待处理", "待下发"}:
        return "04 未启动"
    return "99 其他"


def build_task_rows(team_dir: Path, *, now: datetime | None = None,
                    label: str | None = None,
                    source_label: str = "本地",
                    include_private: bool = False) -> list[dict]:
    """Build compact boss task cards keyed by `任务号`.

    The visible Base view only needs the first eight boss-facing fields, but
    the row also carries legacy routing fields so Base edits can still flow
    back through `base_intake`.
    """
    now = now or datetime.now(_CST)
    label = label or _label_for(team_dir)
    tasks = [
        t for t in _read_tasks(team_dir)
        if _task_card_relevant(t, now=now)
    ]
    rows: list[dict] = []
    for task in sorted(tasks, key=_task_card_sort_key)[:30]:
        tid = str(task.get("id") or "").strip()
        if not tid:
            continue
        title = _title(task)
        assignee = str(task.get("assignee") or "manager")
        status = str(task.get("status") or "待核验")
        artifact = str(task.get("artifact_path") or "")
        artifact_visibility, artifact_upload_path = _artifact_visibility(team_dir, artifact)
        boss_artifact = _boss_artifact_cell(artifact, artifact_visibility)
        boss_focus = _task_boss_focus(task)
        unclosed = _task_unclosed_reason(task)
        next_action = _task_boss_next_action(task)
        updated_at = int(task.get("updated_at") or task.get("created_at") or 0)
        row = {
            "任务卡ID": f"{label}/{tid}",
            "任务号": tid,
            "任务名": _clip(title, 120),
            "负责人": assignee,
            "状态": status,
            "老板看点": _clip(boss_focus, 240),
            "未收口原因": unclosed,
            "老板处理分类": _task_boss_lane(task),
            "产物可见性": artifact_visibility,
            "产物链接": boss_artifact,
            "所属战场": label,
            "负责人团队": label,
            "负责人agent": assignee,
            "任务标题": _clip(title, 240),
            "当前状态": status,
            "下一步动作": _clip(next_action, 240),
            "需要老板做什么": _clip(next_action, 240),
            "是否需要老板": "是" if "老板" in unclosed else "否",
            "真实产物链接": artifact,
            "风险等级": "黄" if status in {"待处理", "待下发", "进行中"} else "绿",
            "最后更新时间": (
                _fmt_time(datetime.fromtimestamp(updated_at / 1000, _CST))
                if updated_at else "未知"
            ),
            "来源群": f"{source_label} ClaudeTeam tasks.json",
        }
        if include_private and artifact_upload_path is not None:
            row["_artifact_upload_path"] = str(artifact_upload_path)
        rows.append(row)
    return rows


def build_all_task_rows(team_dirs: list[Path], *,
                        now: datetime | None = None,
                        labels: dict[Path, str] | None = None,
                        source_labels: dict[Path, str] | None = None,
                        include_private: bool = False) -> list[dict]:
    rows: list[dict] = []
    for team_dir in team_dirs:
        rows.extend(build_task_rows(
            team_dir, now=now,
            label=(labels or {}).get(team_dir),
            source_label=(source_labels or {}).get(team_dir, "本地"),
            include_private=include_private))
    return rows


def _record_items(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    matrix = payload.get("data")
    field_names = payload.get("fields")
    record_ids = payload.get("record_id_list")
    if isinstance(matrix, list) and isinstance(field_names, list):
        rows: list[dict] = []
        for idx, values in enumerate(matrix):
            if not isinstance(values, list):
                continue
            fields = {
                str(field_names[col]): values[col]
                for col in range(min(len(field_names), len(values)))
            }
            item = {"fields": fields}
            if isinstance(record_ids, list) and idx < len(record_ids):
                item["record_id"] = str(record_ids[idx])
            rows.append(item)
        return rows
    for key in ("items", "records"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        return _record_items(data)
    return []


def _field_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "value", "name"):
            if key in value:
                return _field_text(value[key])
    if isinstance(value, list):
        return "".join(_field_text(v) for v in value)
    return ""


def _existing_by_field(payload: dict | None, field_name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in _record_items(payload):
        fields = item.get("fields")
        if not isinstance(fields, dict):
            continue
        key_value = _field_text(fields.get(field_name)).strip()
        record_id = str(item.get("record_id") or item.get("recordId")
                        or item.get("id") or "")
        if key_value and record_id:
            out[key_value] = record_id
    return out


def _existing_by_battle(payload: dict | None) -> dict[str, str]:
    return _existing_by_field(payload, "战场")


def _has_more(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if "has_more" in payload:
        return bool(payload.get("has_more"))
    data = payload.get("data")
    if isinstance(data, dict):
        return _has_more(data)
    return False


def _list_existing_by_field(*, base_token: str, table_id: str, key_field: str,
                            profile: str, lark_call: Callable) -> dict[str, str] | None:
    existing: dict[str, str] = {}
    offset = 0
    limit = 200
    while True:
        payload = lark_call([
            "base", "+record-list",
            "--base-token", base_token,
            "--table-id", table_id,
            "--field-id", key_field,
            "--offset", str(offset),
            "--limit", str(limit),
            "--format", "json",
        ], profile=profile)
        if payload is None:
            return None
        items = _record_items(payload)
        existing.update(_existing_by_field(payload, key_field))
        if not _has_more(payload):
            return existing
        if not items:
            return existing
        offset += len(items)


def _list_existing_by_battle(*, base_token: str, table_id: str,
                             profile: str, lark_call: Callable) -> dict[str, str] | None:
    return _list_existing_by_field(
        base_token=base_token, table_id=table_id, key_field="战场",
        profile=profile, lark_call=lark_call)


def _public_row(row: dict) -> dict:
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def _public_rows(rows: list[dict]) -> list[dict]:
    return [_public_row(row) for row in rows]


def sync_rows(rows: list[dict], *, base_token: str, table_id: str,
              profile: str = "", lark_call: Callable | None = None,
              key_field: str = "战场") -> dict:
    lark_call = lark_call or lark.call
    existing = _list_existing_by_field(
        base_token=base_token, table_id=table_id, key_field=key_field,
        profile=profile, lark_call=lark_call)
    if existing is None:
        return {"ok": False, "updated": 0, "created": 0,
                "failed": len(rows), "errors": ["record-list failed"]}

    updated = created = failed = 0
    errors: list[str] = []
    for row in rows:
        row_key = str(row.get(key_field) or "")
        record_id = existing.get(row_key)
        args = [
            "base", "+record-upsert",
            "--base-token", base_token,
            "--table-id", table_id,
            "--json", json.dumps(_public_row(row), ensure_ascii=False),
        ]
        if record_id:
            args.extend(["--record-id", record_id])
        result = lark_call(args, profile=profile)
        if result is None:
            failed += 1
            errors.append(f"{row_key or '?'}: record-upsert failed")
        elif record_id:
            updated += 1
        else:
            created += 1
    return {
        "ok": failed == 0,
        "updated": updated,
        "created": created,
        "failed": failed,
        "errors": errors,
    }


def upload_task_artifacts(task_rows: list[dict], *, base_token: str,
                          table_id: str, profile: str = "",
                          attachment_field: str = "老板可见产物",
                          lark_call: Callable | None = None) -> dict:
    """Upload locally visible task artifacts to the Base attachment field.

    Kept behind an explicit CLI flag because watchdog sync can run often and
    attachment uploads are not naturally idempotent from the local side.
    """
    lark_call = lark_call or lark.call
    existing = _list_existing_by_field(
        base_token=base_token, table_id=table_id, key_field="任务卡ID",
        profile=profile, lark_call=lark_call)
    if existing is None:
        return {"ok": False, "uploaded": 0, "skipped": 0,
                "failed": len(task_rows), "errors": ["record-list failed"]}

    uploaded = skipped = failed = 0
    errors: list[str] = []
    for row in task_rows:
        row_key = str(row.get("任务卡ID") or "")
        path_text = str(row.get("_artifact_upload_path") or "")
        if not path_text:
            skipped += 1
            continue
        path = Path(path_text)
        if not path.exists():
            failed += 1
            errors.append(f"{row_key or '?'}: upload file missing: {path}")
            continue
        record_id = existing.get(row_key)
        if not record_id:
            failed += 1
            errors.append(f"{row_key or '?'}: Base record not found")
            continue
        result = lark_call([
            "base", "+record-upload-attachment",
            "--base-token", base_token,
            "--table-id", table_id,
            "--record-id", record_id,
            "--field-id", attachment_field,
            "--file", path.name,
        ], profile=profile, cwd=str(path.parent))
        if result is None:
            failed += 1
            errors.append(f"{row_key or '?'}: artifact upload failed")
        else:
            uploaded += 1
    return {
        "ok": failed == 0,
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


def _emit_text(rows: list[dict], *, write: bool, sync_result: dict | None,
               agent_rows: list[dict] | None = None,
               agent_sync_result: dict | None = None,
               task_rows: list[dict] | None = None,
               task_sync_result: dict | None = None,
               artifact_upload_result: dict | None = None) -> None:
    mode = "write" if write else "dry-run"
    print(f"cockpit-sync: {len(rows)} row(s) ({mode})")
    for row in rows:
        print(
            f"- {row['战场']} | {row['当前状态']} | "
            f"active={row['本机可见活跃任务数']} | {row['阶段']} | {row['老板分组']}"
        )
        print(f"  {row['老板一句话']}")
        print(f"  证据动作: {row['今天最小证据动作']}")
        print(f"  老板下一步: {row['老板下一步']}")
    if write and sync_result is not None:
        print(
            "\nwrite result: "
            f"updated={sync_result['updated']} created={sync_result['created']} "
            f"failed={sync_result['failed']}"
        )
        for err in sync_result.get("errors", [])[:5]:
            print(f"  ⚠️ {err}")
    if agent_rows is not None:
        print(f"\nagent rows: {len(agent_rows)}")
    if write and agent_sync_result is not None:
        print(
            "agent write result: "
            f"updated={agent_sync_result['updated']} created={agent_sync_result['created']} "
            f"failed={agent_sync_result['failed']}"
        )
        for err in agent_sync_result.get("errors", [])[:5]:
            print(f"  ⚠️ {err}")
    if task_rows is not None:
        print(f"\ntask rows: {len(task_rows)}")
    if write and task_sync_result is not None:
        print(
            "task write result: "
            f"updated={task_sync_result['updated']} created={task_sync_result['created']} "
            f"failed={task_sync_result['failed']}"
        )
        for err in task_sync_result.get("errors", [])[:5]:
            print(f"  ⚠️ {err}")
    if artifact_upload_result is not None:
        print(
            "artifact upload result: "
            f"uploaded={artifact_upload_result['uploaded']} "
            f"skipped={artifact_upload_result['skipped']} "
            f"failed={artifact_upload_result['failed']}"
        )
        for err in artifact_upload_result.get("errors", [])[:5]:
            print(f"  ⚠️ {err}")
    elif not write:
        print("\ndry-run only; add --write to update Feishu.")


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    write = pop_bool_flag(rest, "--write")
    upload_artifacts = pop_bool_flag(rest, "--upload-artifacts")
    # Accepted for readability in scripts; dry-run is already default.
    pop_bool_flag(rest, "--dry-run")
    include_registry = not pop_bool_flag(rest, "--no-registry")
    root_arg = pop_flag(rest, "--root")
    registry_script_arg = pop_flag(rest, "--registry-script")
    remote_state_dir_arg = (
        pop_flag(rest, "--remote-state-dir")
        or env_str("CLAUDETEAM_COCKPIT_REMOTE_STATE_DIR")
        or ""
    )
    base_token = (pop_flag(rest, "--base-token")
                  or env_str("CLAUDETEAM_COCKPIT_BASE_TOKEN")
                  or DEFAULT_BASE_TOKEN)
    table_id = (pop_flag(rest, "--table-id")
                or env_str("CLAUDETEAM_COCKPIT_TABLE_ID")
                or DEFAULT_TABLE_ID)
    agent_table_id = (pop_flag(rest, "--agent-table-id")
                      or env_str("CLAUDETEAM_COCKPIT_AGENT_TABLE_ID")
                      or "")
    task_table_id = (pop_flag(rest, "--task-table-id")
                     or env_str("CLAUDETEAM_COCKPIT_TASK_TABLE_ID")
                     or "")
    artifact_field_id = (pop_flag(rest, "--artifact-field-id")
                         or env_str("CLAUDETEAM_COCKPIT_ARTIFACT_FIELD_ID")
                         or "老板可见产物")
    profile = (pop_flag(rest, "--profile")
               or env_str("CLAUDETEAM_COCKPIT_PROFILE")
               or "")
    if (rc := reject_extra_args([a for a in rest if a.startswith("--")], USAGE)) is not None:
        return rc
    if upload_artifacts and not write:
        return error_exit("❌ --upload-artifacts requires --write")
    if upload_artifacts and not task_table_id:
        return error_exit("❌ --upload-artifacts requires --task-table-id")

    explicit_team_dirs = bool(rest)
    team_dirs = [Path(item).expanduser().resolve() for item in rest]
    if not team_dirs:
        root = Path(root_arg).expanduser().resolve() if root_arg else Path.cwd()
        team_dirs = _discover(root)
    else:
        root = Path(root_arg).expanduser().resolve() if root_arg else Path.cwd()

    now = datetime.now(_CST)
    rows = [build_row(path, now=now) for path in team_dirs]
    registry_active = include_registry and (not explicit_team_dirs or registry_script_arg)
    registry_script = (
        Path(registry_script_arg).expanduser().resolve()
        if registry_script_arg else None
    )
    registry_sources = (
        team_registry.load(registry_script or team_registry.default_script(root))
        if registry_active else []
    )
    registry_labels = {
        str(source.get("key") or ""): str(source.get("label") or "")
        for source in registry_sources
        if str(source.get("key") or "") and str(source.get("label") or "")
    }
    remote_state_dir = (
        Path(remote_state_dir_arg).expanduser().resolve()
        if remote_state_dir_arg else _default_remote_state_dir(root)
    )
    remote_dirs = _remote_snapshot_dirs(remote_state_dir)
    remote_labels = {
        path: _remote_label(path, registry_labels=registry_labels)
        for path in remote_dirs
    }
    remote_label_values = set(remote_labels.values())
    for path in remote_dirs:
        rows.append(build_row(
            path,
            now=now,
            label=remote_labels[path],
            health=_snapshot_health(path, now=now),
            source_label="云上",
            fact_source=_remote_fact_source(path),
        ))
    if registry_active:
        rows.extend(_registry_rows(root, team_dirs,
                                   registry_script=registry_script,
                                   occupied_labels=remote_label_values,
                                   sources=registry_sources, now=now))
    sync_result = None
    agent_rows = build_all_agent_rows(team_dirs, now=now)
    agent_rows.extend(build_all_agent_rows(remote_dirs, now=now, labels=remote_labels))
    agent_rows.extend(_registry_config_agent_rows(
        registry_sources, occupied_labels=remote_label_values, now=now))
    agent_sync_result = None
    task_rows = build_all_task_rows(
        team_dirs, now=now, include_private=upload_artifacts)
    task_rows.extend(build_all_task_rows(
        remote_dirs, now=now, labels=remote_labels,
        source_labels={path: "云上" for path in remote_dirs},
        include_private=upload_artifacts))
    task_sync_result = None
    artifact_upload_result = None
    if write:
        sync_result = sync_rows(
            rows, base_token=base_token, table_id=table_id, profile=profile)
        if agent_table_id:
            agent_sync_result = sync_rows(
                agent_rows, base_token=base_token, table_id=agent_table_id,
                profile=profile, key_field="名片")
        if task_table_id:
            task_sync_result = sync_rows(
                task_rows, base_token=base_token, table_id=task_table_id,
                profile=profile, key_field="任务卡ID")
            if upload_artifacts and task_sync_result.get("ok"):
                artifact_upload_result = upload_task_artifacts(
                    task_rows, base_token=base_token, table_id=task_table_id,
                    profile=profile, attachment_field=artifact_field_id)

    if as_json:
        ok = (
            (sync_result or {"ok": True}).get("ok", True)
            and (agent_sync_result or {"ok": True}).get("ok", True)
            and (task_sync_result or {"ok": True}).get("ok", True)
            and (artifact_upload_result or {"ok": True}).get("ok", True)
        )
        print_json({
            "ok": ok,
            "write": write,
            "base_token": base_token,
            "table_id": table_id,
            "agent_table_id": agent_table_id,
            "task_table_id": task_table_id,
            "remote_state_dir": str(remote_state_dir) if remote_state_dir else "",
            "rows": rows,
            "agent_rows": agent_rows,
            "task_rows": _public_rows(task_rows),
            "sync": sync_result,
            "agent_sync": agent_sync_result,
            "task_sync": task_sync_result,
            "artifact_upload": artifact_upload_result,
        })
    else:
        _emit_text(rows, write=write, sync_result=sync_result,
                   agent_rows=agent_rows if agent_table_id or as_json else None,
                   agent_sync_result=agent_sync_result,
                   task_rows=task_rows if task_table_id or as_json else None,
                   task_sync_result=task_sync_result,
                   artifact_upload_result=artifact_upload_result)
    failed = (
        (sync_result is not None and not sync_result.get("ok"))
        or (agent_sync_result is not None and not agent_sync_result.get("ok"))
        or (task_sync_result is not None and not task_sync_result.get("ok"))
        or (artifact_upload_result is not None
            and not artifact_upload_result.get("ok"))
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
