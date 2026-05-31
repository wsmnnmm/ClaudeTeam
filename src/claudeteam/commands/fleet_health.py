"""`claudeteam fleet-health` — boss-readable health rollup for many teams."""
from __future__ import annotations

import contextlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from claudeteam.commands import health as health_cmd
from claudeteam.runtime import artifact_gate, tunables
from claudeteam.util import error_exit, maybe_print_help, pop_bool_flag, pop_flag, print_json


USAGE = """usage: claudeteam fleet-health [--root <dir>] [--json]
                               [--report-dir <dir>] [team-dir ...]

Examples:
  claudeteam fleet-health --root /Users/wsm/Project
  claudeteam fleet-health --root /Users/wsm/Project --report-dir runtime-health
  claudeteam fleet-health /Users/wsm/Project/product-lab /Users/wsm/Project/work-assistant-team
"""

_CST = timezone(timedelta(hours=8), name="CST")


@contextlib.contextmanager
def _temporary_env(overrides: dict[str, str]):
    old = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _discover(root: Path) -> list[Path]:
    if (root / "claudeteam.toml").exists():
        return [root]
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    return [
        child for child in children
        if child.is_dir() and (child / "claudeteam.toml").exists()
    ]


def _health_payload(team_dir: Path) -> dict:
    cfg = team_dir / "claudeteam.toml"
    if not cfg.exists():
        return {
            "team": team_dir.name,
            "path": str(team_dir),
            "ok": False,
            "bad": 1,
            "warn": 0,
            "issues": [f"❌ missing claudeteam.toml: {cfg}"],
        }
    env = {
        "CLAUDETEAM_STATE_DIR": str(team_dir / "state"),
        "CLAUDETEAM_CONFIG_FILE": str(cfg),
        "CLAUDETEAM_TEAM_FILE": str(team_dir / "team.json"),
        "CLAUDETEAM_RUNTIME_CONFIG": str(team_dir / "runtime_config.json"),
    }
    out, err = io.StringIO(), io.StringIO()
    with _temporary_env(env):
        tunables.reset_cache()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = health_cmd.main(["--json"])
    try:
        payload = json.loads(out.getvalue())
    except json.JSONDecodeError:
        return {
            "team": team_dir.name,
            "path": str(team_dir),
            "ok": False,
            "bad": 1,
            "warn": 0,
            "issues": [f"❌ health returned non-json (rc={rc})"],
        }
    task_gate_issues = _task_gate_issues(team_dir)
    issue_lines = task_gate_issues + [
        line.strip() for line in payload.get("lines", [])
        if "❌" in line or "⚠️" in line
    ]
    return {
        "team": team_dir.name,
        "path": str(team_dir),
        "ok": bool(payload.get("ok")) and not task_gate_issues,
        "bad": int(payload.get("bad", 0)) + len(task_gate_issues),
        "warn": int(payload.get("warn", 0)),
        "issues": issue_lines[:5],
    }


def _artifact_reference_exists(team_dir: Path, artifact: str) -> bool:
    return artifact_gate.existing_artifact_reference(
        artifact, base_dirs=[team_dir])


def _ui_task_gate_issue(team_dir: Path, task: dict, artifact: str) -> str:
    context = "\n".join([
        str(task.get("title") or ""),
        str(task.get("description") or ""),
    ])
    evidence = artifact_gate.ui_evidence(
        artifact,
        context_text=context,
        base_dirs=[team_dir],
    )
    if evidence.passed:
        return ""
    tid = str(task.get("id") or "?")
    status = str(task.get("status") or "")
    missing = " and ".join(evidence.missing)
    return (
        f"❌ golden gate: {tid} is {status} UI/page restoration "
        f"but lacks {missing}: {artifact}")


def _task_gate_issues(team_dir: Path) -> list[str]:
    tasks_file = team_dir / "state" / "tasks.json"
    if not tasks_file.exists():
        return []
    try:
        data = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [f"❌ golden gate unreadable tasks.json: {tasks_file}"]
    issues = []
    for task in data.get("tasks", []):
        status = str(task.get("status") or "")
        if status not in {"待验收", "已完成"}:
            continue
        tid = str(task.get("id") or "?")
        artifact = str(task.get("artifact_path") or "").strip()
        if not artifact:
            issues.append(f"❌ golden gate: {tid} is {status} but artifact_path is empty")
        elif not _artifact_reference_exists(team_dir, artifact):
            issues.append(f"❌ golden gate: {tid} is {status} but artifact is missing: {artifact}")
        else:
            ui_issue = _ui_task_gate_issue(team_dir, task, artifact)
            if ui_issue:
                issues.append(ui_issue)
    return issues[:10]


def _status(entry: dict) -> str:
    if entry["bad"]:
        return "RED"
    if entry["warn"]:
        return "YELLOW"
    return "GREEN"


def _emit_text(entries: list[dict]) -> None:
    print(f"fleet health: {len(entries)} team(s)")
    for entry in entries:
        status = _status(entry)
        print(
            f"- {status:<6} {entry['team']} "
            f"(red={entry['bad']}, warn={entry['warn']})")
        for issue in entry["issues"][:3]:
            print(f"    {issue}")
    red = sum(1 for entry in entries if entry["bad"])
    yellow = sum(1 for entry in entries if not entry["bad"] and entry["warn"])
    green = sum(1 for entry in entries if not entry["bad"] and not entry["warn"])
    print(f"\nsummary: green={green}, yellow={yellow}, red={red}")


def _counts(entries: list[dict]) -> dict[str, int]:
    return {
        "green": sum(1 for entry in entries if _status(entry) == "GREEN"),
        "yellow": sum(1 for entry in entries if _status(entry) == "YELLOW"),
        "red": sum(1 for entry in entries if _status(entry) == "RED"),
    }


def _report_time() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d %H:%M CST")


def _markdown_table(entries: list[dict]) -> str:
    rows = ["| 状态 | 团队 | red | warn | 主要问题 |", "|---|---|---:|---:|---|"]
    for entry in entries:
        issues = "; ".join(entry["issues"][:2]) if entry["issues"] else "无"
        rows.append(
            f"| {_status(entry)} | {entry['team']} | {entry['bad']} | "
            f"{entry['warn']} | {issues} |")
    return "\n".join(rows)


def _fleet_status_md(entries: list[dict]) -> str:
    counts = _counts(entries)
    return (
        "# Fleet Status\n\n"
        f"更新时间: {_report_time()}\n\n"
        f"摘要: green={counts['green']}, yellow={counts['yellow']}, red={counts['red']}\n\n"
        f"{_markdown_table(entries)}\n"
    )


def _boss_brief_md(entries: list[dict]) -> str:
    counts = _counts(entries)
    attention = [entry for entry in entries if _status(entry) != "GREEN"]
    lines = [
        "# Daily Boss Brief",
        "",
        f"更新时间: {_report_time()}",
        "",
        f"结论: {counts['red']} 个红灯, {counts['yellow']} 个黄灯, {counts['green']} 个绿灯。",
        "",
        "## 需要关注",
    ]
    if not attention:
        lines.append("- 暂无红黄灯团队。")
    else:
        for entry in attention:
            issue = entry["issues"][0] if entry["issues"] else "健康检查存在风险"
            lines.append(f"- {_status(entry)} {entry['team']}: {issue}")
    lines.extend([
        "",
        "## C1 边界",
        "- 本报告只读生成; 不发飞书, 不改任务, 不重启团队。",
        "- 红灯只代表需要人工确认或授权恢复, 不代表已经自动处理。",
        "",
    ])
    return "\n".join(lines)


def _night_shift_plan_md(entries: list[dict]) -> str:
    lines = [
        "# Night Shift Plan",
        "",
        f"更新时间: {_report_time()}",
        "",
        "夜班规则: C1 只读巡视, 不自动派活、不自动重启、不写生产系统。",
        "",
    ]
    for entry in entries:
        if _status(entry) == "RED":
            action = "保持观察并等待人工授权恢复"
        elif _status(entry) == "YELLOW":
            action = "下一轮复查同一告警是否持续"
        else:
            action = "正常巡检"
        lines.append(f"- {entry['team']}: {_status(entry)} — {action}")
    lines.append("")
    return "\n".join(lines)


def _dashboard_html(entries: list[dict]) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{escape(_status(entry))}</td>"
        f"<td>{escape(str(entry['team']))}</td>"
        f"<td>{entry['bad']}</td>"
        f"<td>{entry['warn']}</td>"
        f"<td>{escape('; '.join(entry['issues'][:2]) if entry['issues'] else '无')}</td>"
        "</tr>"
        for entry in entries
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>ClaudeTeam Fleet Status</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background: #f6f8fa; }}
</style>
<h1>ClaudeTeam Fleet Status</h1>
<p>更新时间: {escape(_report_time())}</p>
<table>
<thead><tr><th>状态</th><th>团队</th><th>red</th><th>warn</th><th>主要问题</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</html>
"""


def _write_reports(report_dir: Path, entries: list[dict]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "fleet-status.md": _fleet_status_md(entries),
        "daily-boss-brief.md": _boss_brief_md(entries),
        "night-shift-plan.md": _night_shift_plan_md(entries),
        "dashboard.html": _dashboard_html(entries),
    }
    for name, text in files.items():
        (report_dir / name).write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    root_arg = pop_flag(rest, "--root")
    report_dir_arg = pop_flag(rest, "--report-dir")
    team_dirs = [Path(item).expanduser().resolve() for item in rest]
    if not team_dirs:
        root = Path(root_arg).expanduser().resolve() if root_arg else Path.cwd()
        team_dirs = _discover(root)
    entries = [_health_payload(path) for path in team_dirs]
    if report_dir_arg:
        try:
            _write_reports(Path(report_dir_arg).expanduser().resolve(), entries)
        except OSError as exc:
            return error_exit(f"failed to write report dir: {exc}")
    if as_json:
        print_json({"ok": all(entry["ok"] for entry in entries), "teams": entries})
    else:
        _emit_text(entries)
        if report_dir_arg:
            print(f"\nreports: {Path(report_dir_arg).expanduser().resolve()}")
    return 1 if any(entry["bad"] for entry in entries) else 0
