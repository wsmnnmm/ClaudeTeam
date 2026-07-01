"""`claudeteam cockpit-brief` — lightweight boss brief from cockpit facts.

The Feishu Base cockpit remains the durable data backend.  This command
renders the same local/cloud facts into a small text or JSON brief that Hermes
can send through WeChat without opening the memory-heavy Base web page.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from claudeteam.commands import cockpit_sync
from claudeteam.runtime import team_registry
from claudeteam.util import (
    atomic_write_text, env_str, error_exit, maybe_print_help, pop_bool_flag,
    pop_flag, print_json, reject_extra_args,
)


USAGE = """usage: claudeteam cockpit-brief [--root <dir>] [--json]
                                [--out <file>]
                                [--max-actions <n>] [--max-teams <n>]
                                [--approval-base-url <url>]
                                [--remote-state-dir <dir>]
                                [--registry-script <path>] [--no-registry]
                                [team-dir ...]

Examples:
  claudeteam cockpit-brief --root /Users/wsm/Project
  claudeteam cockpit-brief --root /Users/wsm/Project --json
  claudeteam cockpit-brief --root /Users/wsm/Project --out runtime-health/boss-brief.md
"""

_STABLE_DEFAULT_MAX_ACTIONS = 5
_STABLE_DEFAULT_MAX_TEAMS = 8
_NEGATIVE_BLOCKER = {"", "无", "未发现明显卡住", "未卡住"}
_FEISHU_BREAK_MARKERS = (
    "app secret", "app_id", "feishu", "飞书", "lark-cli", "lark profile",
    "授权", "secret", "机器人",
)
_STALE_MARKERS = ("心跳", "heartbeat", "状态已过期", "过期待重核", "过期需重核", "需重核")


def _human_action(text: str) -> str:
    """Translate cockpit/Base operation hints into mobile-friendly wording."""
    text = str(text or "").strip()
    text = text.replace("【老板操作】", "操作")
    text = text.replace("【老板决策】", "你的回复")
    text = text.replace("老板决策", "你的回复")
    text = text.replace("老板操作", "操作")
    import re
    text = re.sub(
        r"下拉操作选「([^」]+)」会下发到[^；。]*[；。]?",
        r"可直接回复“\1”。",
        text,
    )
    text = re.sub(r"自定义要求写你的回复[。；]?", "也可以直接写具体要求。", text)
    text = re.sub(r"改方向就在你的回复写一句话[。；]?", "要改方向就直接写要求。", text)
    text = re.sub(r"在你的回复写[“\"]([^”\"]+)[”\"]或[“\"]([^”\"]+)[”\"]", r"二选一：\1 / \2", text)
    return text


def _parse_positive_int(raw: str | None, *, default: int, flag: str) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{flag} must be an integer") from None
    if value <= 0:
        raise ValueError(f"{flag} must be positive")
    return value


def _team_dirs(root_arg: str | None, rest: list[str]) -> tuple[Path, list[Path], bool]:
    explicit = bool(rest)
    root = Path(root_arg).expanduser().resolve() if root_arg else Path.cwd()
    if explicit:
        return root, [Path(item).expanduser().resolve() for item in rest], True
    return root, cockpit_sync._discover(root), False


def _registry_sources(root: Path, registry_script: Path | None,
                      active: bool) -> list[dict]:
    if not active:
        return []
    return team_registry.load(registry_script or team_registry.default_script(root))


def _collect_rows(root: Path, team_dirs: list[Path], *,
                  explicit_team_dirs: bool,
                  include_registry: bool = True,
                  registry_script: Path | None = None,
                  remote_state_dir: Path | None = None,
                  now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(cockpit_sync._CST)
    rows = [cockpit_sync.build_row(path, now=now) for path in team_dirs]
    registry_active = include_registry and (not explicit_team_dirs or registry_script)
    sources = _registry_sources(root, registry_script, bool(registry_active))
    registry_labels = {
        str(source.get("key") or ""): str(source.get("label") or "")
        for source in sources
        if str(source.get("key") or "") and str(source.get("label") or "")
    }
    remote_base = remote_state_dir or cockpit_sync._default_remote_state_dirs(root, team_dirs)
    remote_dirs = cockpit_sync._remote_snapshot_dirs(remote_base)
    remote_labels = {
        path: cockpit_sync._remote_label(path, registry_labels=registry_labels)
        for path in remote_dirs
    }
    remote_label_values = set(remote_labels.values())
    for path in remote_dirs:
        rows.append(cockpit_sync.build_row(
            path,
            now=now,
            label=remote_labels[path],
            health=cockpit_sync._snapshot_health(path, now=now),
            source_label="云上",
            fact_source=cockpit_sync._remote_fact_source(path),
        ))
    if registry_active:
        rows.extend(cockpit_sync._registry_rows(
            root, team_dirs,
            registry_script=registry_script,
            occupied_labels=remote_label_values,
            sources=sources,
            now=now,
        ))
    return rows


def _blob(row: dict) -> str:
    return " ".join(str(row.get(key) or "") for key in (
        "健康灯", "风险详情", "核验状态", "阻塞", "卡住判断", "老板分组", "老板下一步",
    ))


def _is_stale_only(row: dict) -> bool:
    text = _blob(row).lower()
    if "founder os" in text or "阶段/证据" in text or "字段待回写" in text:
        return False
    if not any(marker.lower() in text for marker in _STALE_MARKERS):
        return False
    return not any(marker in text for marker in _FEISHU_BREAK_MARKERS)


def _brief_band(row: dict) -> str:
    state = str(row.get("状态分栏") or "")
    blocker = str(row.get("阻塞") or "").strip()
    if state == "有阻塞" or blocker not in _NEGATIVE_BLOCKER and "❌" in blocker:
        return "blocked"
    if str(row.get("是否需要老板") or "") == "是":
        return "stale_only" if _is_stale_only(row) else "needs_boss"
    if state in {"执行中", "运行中"}:
        return "active"
    return "ready"


def _band_label(band: str) -> str:
    return {
        "blocked": "红灯阻塞",
        "needs_boss": "要老板拍板",
        "stale_only": "心跳待重核",
        "active": "执行中",
        "ready": "可接单/暂不处理",
    }.get(band, band)


def _priority(row: dict) -> tuple[int, str]:
    order = {
        "blocked": 0,
        "needs_boss": 1,
        "stale_only": 2,
        "active": 3,
        "ready": 4,
    }
    return (order.get(_brief_band(row), 9), str(row.get("战场") or ""))


def _counts(rows: list[dict]) -> dict[str, int]:
    bands = [_brief_band(row) for row in rows]
    return {
        "blocked": bands.count("blocked"),
        "needs_boss": bands.count("needs_boss"),
        "stale_only": bands.count("stale_only"),
        "active": bands.count("active"),
        "ready": bands.count("ready"),
        "total": len(rows),
    }


def _boss_line(row: dict) -> str:
    team = str(row.get("战场") or "未知团队")
    band = _brief_band(row)
    blocker = str(row.get("阻塞") or "").strip()
    action = str(row.get("老板下一步") or row.get("需要老板做什么") or "").strip()
    current = str(row.get("当前动作") or row.get("当前状态") or "").strip()
    if band == "blocked":
        why = blocker if blocker and blocker != "无" else "健康红项"
        return cockpit_sync._clip(f"【红灯】{team}: {why}；下一步：{_human_action(action)}", 180)
    if band == "needs_boss":
        return cockpit_sync._clip(f"【拍板】{team}: {_human_action(action)}", 180)
    if band == "stale_only":
        return cockpit_sync._clip(
            f"【重核】{team}: 只是员工心跳/状态过期，先让 manager live health 回执；"
            "这不等同于飞书机器人或 CLI 授权损坏。",
            180,
        )
    if band == "active":
        return cockpit_sync._clip(f"【等结果】{team}: {current}；{_human_action(action)}", 180)
    return cockpit_sync._clip(f"【可接单】{team}: {current}", 180)


def _team_card(row: dict) -> dict:
    band = _brief_band(row)
    return {
        "team": str(row.get("战场") or ""),
        "source_type": str(row.get("事实类型") or "未标注"),
        "band": band,
        "band_label": _band_label(band),
        "status": str(row.get("当前状态") or ""),
        "boss_group": str(row.get("老板分组") or ""),
        "current": str(row.get("当前动作") or ""),
        "blocker": str(row.get("阻塞") or "无"),
        "boss_next": str(row.get("老板下一步") or ""),
        "next_report": str(row.get("下次汇报") or ""),
        "fact_source": str(row.get("事实来源") or ""),
    }


def _row_int(row: dict, key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _source_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        source = str(row.get("事实类型") or "未标注")
        counts[source] = counts.get(source, 0) + 1
    return counts


def _approval_action_type(row: dict) -> str:
    active_count = _row_int(row, "本机可见活跃任务数")
    waiting_review = _row_int(row, "待验收任务数")
    evidence_gaps = _row_int(row, "证据缺口数")
    blob = _blob(row)
    if waiting_review:
        return "验收优先"
    if "权限" in blob or "授权" in blob or "API" in blob or "api" in blob:
        return "权限/blocker"
    if active_count >= 5:
        return "收敛优先"
    if evidence_gaps:
        return "证据补字段"
    if _brief_band(row) == "stale_only":
        return "心跳补齐"
    if _brief_band(row) == "needs_boss":
        return "老板拍板"
    return "继续执行"


def _approval_priority(row: dict) -> tuple[int, int, str]:
    action = _approval_action_type(row)
    active_count = _row_int(row, "本机可见活跃任务数")
    waiting_review = _row_int(row, "待验收任务数")
    evidence_gaps = _row_int(row, "证据缺口数")
    order = {
        "验收优先": 0,
        "收敛优先": 1,
        "权限/blocker": 2,
        "老板拍板": 3,
        "证据补字段": 4,
        "心跳补齐": 5,
        "继续执行": 6,
    }
    weight = waiting_review * 100 + active_count * 10 + evidence_gaps
    return (order.get(action, 9), -weight, str(row.get("战场") or ""))


def _approval_task(row: dict) -> str:
    explicit = str(row.get("待验收代表任务") or row.get("证据缺口代表任务") or "").strip()
    if explicit:
        return explicit
    return str(row.get("当前动作") or row.get("当前步骤") or "当前任务").strip()


def _approval_reason(row: dict, action_type: str) -> str:
    active_count = _row_int(row, "本机可见活跃任务数")
    waiting_review = _row_int(row, "待验收任务数")
    evidence_gaps = _row_int(row, "证据缺口数")
    if action_type == "验收优先":
        return f"已有 {waiting_review} 个待验收任务，先把最具体产物验掉，避免停在内部产物。"
    if action_type == "收敛优先":
        return f"当前有 {active_count} 个未闭环任务，先收敛到一个可截图/可验收闭环。"
    if action_type == "权限/blocker":
        return "存在权限、授权或 API blocker，先确认是否继续投入。"
    if action_type == "证据补字段":
        return f"发现 {evidence_gaps} 个证据缺口，先补 artifact、截图或下一步 owner。"
    if action_type == "心跳补齐":
        return "主要是心跳/状态过期，先让 manager live health 回执，不扩新任务。"
    return _human_action(str(row.get("老板下一步") or "")) or "按当前节奏继续执行并回写状态。"


def _approval_manager(row: dict) -> str:
    team = str(row.get("战场") or "").lower()
    if "product" in team:
        return "productlab_manager"
    if "工作分身" in team or "work" in team:
        return "work_assistant_manager"
    if "todo" in team:
        return "todo002_manager"
    if "website" in team or "chuhai" in team or "出海" in team:
        return "websitechuhai_manager"
    if "traffic" in team or "流量" in team:
        return "traffic_ops_manager"
    return "manager"


def _approval_url(base_url: str, action: dict) -> str:
    if not base_url:
        return ""
    separator = "&" if "?" in base_url else "?"
    params = urlencode({
        "id": action["id"],
        "team": action["team"],
        "type": action["action_type"],
        "task": action["task"],
    })
    return f"{base_url}{separator}{params}"


def _pending_approval_actions(rows: list[dict], *, max_items: int = 3,
                              approval_base_url: str = "") -> list[dict]:
    candidates = [
        row for row in rows
        if _brief_band(row) in {"blocked", "needs_boss", "stale_only", "active"}
        or _row_int(row, "本机可见活跃任务数") >= 5
        or _row_int(row, "待验收任务数") > 0
        or _row_int(row, "证据缺口数") > 0
    ]
    actions: list[dict] = []
    for index, row in enumerate(sorted(candidates, key=_approval_priority)[:max_items], 1):
        team = str(row.get("战场") or "未知团队")
        action_type = _approval_action_type(row)
        task = cockpit_sync._clip(_approval_task(row), 120)
        manager = _approval_manager(row)
        action = {
            "id": f"A{index}",
            "status": "待批",
            "team": team,
            "manager": manager,
            "action_type": action_type,
            "task": task,
            "reason": cockpit_sync._clip(_approval_reason(row, action_type), 180),
            "approval_phrase": f"批准 A{index}",
        }
        action["instruction"] = (
            f"[老板批准] {action_type}：{team} / {task}\n"
            f"老板通过黄色观察简报批准此动作。请 {manager} "
            "在 2 小时内执行，并回写完成证据、截图/链接或真实 blocker。"
        )
        action["approve_url"] = _approval_url(approval_base_url, action)
        actions.append(action)
    return actions


def build_brief(rows: list[dict], *, now: datetime | None = None,
                max_actions: int = _STABLE_DEFAULT_MAX_ACTIONS,
                max_teams: int = _STABLE_DEFAULT_MAX_TEAMS,
                approval_base_url: str = "") -> dict:
    now = now or datetime.now(cockpit_sync._CST)
    ordered = sorted(rows, key=_priority)
    attention = [row for row in ordered if _brief_band(row) in {
        "blocked", "needs_boss", "stale_only",
    }]
    action_rows = (attention or ordered)[:max_actions]
    boss_lines = [_boss_line(row) for row in action_rows]
    if not boss_lines:
        boss_lines = ["没有需要老板盯盘的事项；让 Hermes 到点拿简报即可。"]
    return {
        "generated_at": cockpit_sync._fmt_time(now),
        "ok": not any(_brief_band(row) == "blocked" for row in rows),
        "summary": _counts(rows),
        "source_summary": _source_counts(rows),
        "boss_brief": boss_lines,
        "pending_approvals": _pending_approval_actions(
            ordered, max_items=min(3, max_actions),
            approval_base_url=approval_base_url.strip()),
        "teams": [_team_card(row) for row in ordered[:max_teams]],
    }


def render_markdown(brief: dict) -> str:
    summary = brief["summary"]
    source_summary = brief.get("source_summary") or {}
    source_line = " / ".join(
        f"{source} {count}" for source, count in sorted(source_summary.items())
    ) or "未标注"
    lines = [
        "# 老板简报",
        "",
        f"更新时间: {brief['generated_at']}",
        "",
        (
            "结论: "
            f"红灯 {summary['blocked']} / "
            f"要老板 {summary['needs_boss']} / "
            f"心跳待重核 {summary['stale_only']} / "
            f"执行中 {summary['active']} / "
            f"可接单 {summary['ready']}。"
        ),
        f"来源: {source_line}。本机/云上分开看，避免把本地员工心跳和云端部署状态混在一起。",
        "",
    ]
    approvals = list(brief.get("pending_approvals") or [])
    if approvals:
        lines.append("## 本次待批动作（最多 3 个）")
        for action in approvals:
            approve = (
                f"[批准]({action['approve_url']})"
                if action.get("approve_url") else
                f"批准口令: {action['approval_phrase']}"
            )
            lines.append(
                f"- {action['id']}｜{action['team']}｜{action['action_type']}："
                f"{action['task']}"
            )
            lines.append(f"  理由: {action['reason']}")
            lines.append(f"  {approve}")
            instruction = str(action["instruction"]).replace("\n", " ")
            lines.append(f"  执行指令: {cockpit_sync._clip(instruction, 220)}")
        lines.append("")
    lines.append("## 老板只看")
    for index, line in enumerate(brief["boss_brief"], 1):
        lines.append(f"{index}. {line}")
    lines.extend(["", "## 团队卡片"])
    for card in brief["teams"]:
        blocker = card["blocker"] or "无"
        lines.append(
            f"- [{card['source_type']}] {card['team']}｜{card['band_label']}｜{card['status']}｜"
            f"{card['boss_group'] or '无分组'}"
        )
        lines.append(
            f"  当前: {cockpit_sync._clip(card['current'] or '无当前动作', 120)}"
        )
        lines.append(
            f"  下一步: {cockpit_sync._clip(_human_action(card['boss_next']) or '暂无', 160)}"
        )
        lines.append(
            f"  阻塞: {cockpit_sync._clip(blocker, 120)}；下次汇报: "
            f"{card['next_report'] or '未登记'}"
        )
    lines.extend([
        "",
        "边界: 本简报只读生成；不写飞书、不改任务、不重启团队。"
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    include_registry = not pop_bool_flag(rest, "--no-registry")
    root_arg = pop_flag(rest, "--root")
    out_arg = pop_flag(rest, "--out")
    max_actions_arg = pop_flag(rest, "--max-actions")
    max_teams_arg = pop_flag(rest, "--max-teams")
    approval_base_url = (
        pop_flag(rest, "--approval-base-url")
        or env_str("CLAUDETEAM_APPROVAL_BASE_URL")
        or ""
    )
    registry_script_arg = pop_flag(rest, "--registry-script")
    remote_state_dir_arg = (
        pop_flag(rest, "--remote-state-dir")
        or env_str("CLAUDETEAM_COCKPIT_REMOTE_STATE_DIR")
        or ""
    )
    if (rc := reject_extra_args([a for a in rest if a.startswith("--")], USAGE)) is not None:
        return rc

    try:
        max_actions = _parse_positive_int(
            max_actions_arg, default=_STABLE_DEFAULT_MAX_ACTIONS,
            flag="--max-actions")
        max_teams = _parse_positive_int(
            max_teams_arg, default=_STABLE_DEFAULT_MAX_TEAMS,
            flag="--max-teams")
    except ValueError as exc:
        return error_exit(f"❌ {exc}")

    root, dirs, explicit = _team_dirs(root_arg, rest)
    now = datetime.now(cockpit_sync._CST)
    registry_script = (
        Path(registry_script_arg).expanduser().resolve()
        if registry_script_arg else None
    )
    remote_state_dir = (
        Path(remote_state_dir_arg).expanduser().resolve()
        if remote_state_dir_arg else None
    )
    rows = _collect_rows(
        root, dirs,
        explicit_team_dirs=explicit,
        include_registry=include_registry,
        registry_script=registry_script,
        remote_state_dir=remote_state_dir,
        now=now,
    )
    brief = build_brief(
        rows, now=now, max_actions=max_actions, max_teams=max_teams,
        approval_base_url=approval_base_url)
    rendered = (
        json.dumps(brief, ensure_ascii=False, indent=2) + "\n"
        if as_json else render_markdown(brief)
    )
    if out_arg:
        try:
            atomic_write_text(Path(out_arg).expanduser().resolve(), rendered)
        except OSError as exc:
            return error_exit(f"❌ failed to write brief: {exc}")
    if as_json:
        print_json(brief)
    else:
        print(rendered, end="")
    return 1 if not brief["ok"] else 0
