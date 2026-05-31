"""`claudeteam evolution-health` — AI-team self-learning health report."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from claudeteam.runtime import boss_experience
from claudeteam.util import (
    atomic_write_text, error_exit, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, reject_extra_args,
)


USAGE = """usage: claudeteam evolution-health [--root <dir>] [--json]
                                      [--out <file>] [--audit-path <path> ...]

Examples:
  claudeteam evolution-health --root /Users/wsm/Project/ClaudeTeam
  claudeteam evolution-health --root /Users/wsm/Project/ClaudeTeam --out runtime-health/evolution-health.md
"""


_GUARDRAILS = (
    ("path_only_delivery_gate", "src/claudeteam/commands/say.py", "path_only_delivery"),
    ("mentor_request_gateway", "src/claudeteam/commands/mentor_request.py", "--image-caption"),
    ("boss_brief_replyable_cards", "src/claudeteam/commands/cockpit_brief.py", "老板简报"),
    ("boss_experience_audit", "src/claudeteam/commands/boss_experience_audit.py", "boss-experience-audit"),
    ("correction_case_regression", "src/claudeteam/commands/correction_cases.py", "correction-cases"),
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _correction_entries(root: Path) -> int:
    text = "\n".join([
        _read_text(root / ".learnings" / "ERRORS.md"),
        _read_text(root / ".learnings" / "LEARNINGS.md"),
    ])
    markers = ("Source: user_feedback", "Source: correction", "ERR-", "用户", "老板")
    return sum(text.count(marker) for marker in markers)


def _guardrail_status(root: Path) -> list[dict]:
    rows = []
    for name, rel, marker in _GUARDRAILS:
        path = root / rel
        text = _read_text(path)
        rows.append({
            "name": name,
            "path": str(path),
            "present": path.exists() and marker in text,
        })
    return rows


def _audit(root: Path, audit_paths: list[Path]) -> dict:
    paths = audit_paths or [
        root / "runtime-health" / "boss-brief.md",
        root / "artifacts" / "cross-team" / "20260523-ai-team-self-learning-liuxiaopai",
    ]
    paths = [path for path in paths if path.exists()]
    scanned, issues = boss_experience.audit_paths(paths, max_files=80)
    return {
        "files_scanned": len(scanned),
        "issues": len(issues),
        "issue_counts": {
            code: sum(1 for issue in issues if issue.code == code)
            for code in sorted({issue.code for issue in issues})
        },
    }


def build_payload(root: Path, audit_paths: list[Path] | None = None) -> dict:
    cases = boss_experience.run_correction_cases()
    case_passed = sum(1 for row in cases if row["passed"])
    guardrails = _guardrail_status(root)
    guardrail_count = sum(1 for row in guardrails if row["present"])
    audit = _audit(root, audit_paths or [])
    corrections = _correction_entries(root)
    proactive_discovery_rate = 0.0
    if audit["issues"] or corrections:
        proactive_discovery_rate = round(audit["issues"] / max(1, audit["issues"] + corrections), 2)
    if case_passed == len(cases) and guardrail_count >= 4 and proactive_discovery_rate >= 0.25:
        level = "strong"
        diagnosis = "有主动巡检雏形，开始接近自学习系统"
    elif case_passed == len(cases) and guardrail_count >= 4:
        level = "medium_patch_system"
        diagnosis = "中等：纠偏能快速沉淀为门禁，但主动发现问题还弱"
    else:
        level = "weak"
        diagnosis = "偏弱：纠偏案例或关键门禁还没有机器化"
    return {
        "ok": case_passed == len(cases),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "diagnosis": diagnosis,
        "metrics": {
            "correction_case_pass_rate": round(case_passed / max(1, len(cases)), 2),
            "correction_cases_total": len(cases),
            "guardrails_present": guardrail_count,
            "guardrails_total": len(guardrails),
            "boss_correction_entries": corrections,
            "active_audit_findings": audit["issues"],
            "proactive_discovery_rate_proxy": proactive_discovery_rate,
        },
        "guardrails": guardrails,
        "audit": audit,
        "next_actions": [
            {
                "owner": "WorkAssistant",
                "action": "每天跑 boss-experience-audit，模拟老板检查飞书、微信、导师卡是否人话",
                "evidence": "runtime-health/boss-experience-audit.md",
                "metric": "一周内非人话问题下降 50%",
                "non_goal": "不等老板截图纠偏才修",
            },
            {
                "owner": "Product Lab",
                "action": "每次老板纠偏新增 correction-cases bad/good 回归样本",
                "evidence": "claudeteam correction-cases 全部通过",
                "metric": "历史纠偏案例回归通过率 100%",
                "non_goal": "不只写规则文档",
            },
            {
                "owner": "ClaudeTeam",
                "action": "每周生成 evolution-health 趋势，盯老板纠偏频率和主动发现率",
                "evidence": "runtime-health/evolution-health.md",
                "metric": "连续两周老板纠偏频率下降、主动发现率上升",
                "non_goal": "不把文件数量当进化指标",
            },
        ],
    }


def render_markdown(data: dict) -> str:
    metrics = data["metrics"]
    lines = [
        "# AI 团队进化健康度",
        "",
        f"生成时间: {data['generated_at']}",
        f"判断: {data['diagnosis']}",
        "",
        "## 指标",
        "",
        f"- 纠偏案例通过率: {metrics['correction_case_pass_rate']} ({metrics['correction_cases_total']} cases)",
        f"- 机器门禁: {metrics['guardrails_present']} / {metrics['guardrails_total']}",
        f"- 老板纠偏记录: {metrics['boss_correction_entries']}",
        f"- 主动巡检发现: {metrics['active_audit_findings']}",
        f"- 主动发现率代理值: {metrics['proactive_discovery_rate_proxy']}",
        "",
        "## 门禁",
    ]
    for row in data["guardrails"]:
        lines.append(f"- {'OK' if row['present'] else 'MISS'} {row['name']}")
    lines.extend(["", "## 下一轮动作"])
    for item in data["next_actions"]:
        lines.append(
            f"- {item['owner']}: {item['action']}；证据：{item['evidence']}；"
            f"验收：{item['metric']}；不做：{item['non_goal']}"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    root_arg = pop_flag(rest, "--root")
    out_arg = pop_flag(rest, "--out")
    audit_paths: list[Path] = []
    while "--audit-path" in rest:
        value = pop_flag(rest, "--audit-path")
        if value:
            audit_paths.append(Path(value).expanduser().resolve())
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    root = Path(root_arg).expanduser().resolve() if root_arg else Path.cwd()
    data = build_payload(root, audit_paths)
    rendered = json.dumps(data, ensure_ascii=False, indent=2) if as_json else render_markdown(data)
    if out_arg:
        try:
            atomic_write_text(Path(out_arg).expanduser().resolve(), rendered + "\n")
        except OSError as exc:
            return error_exit(f"❌ failed to write report: {exc}")
    if as_json:
        print_json(data)
    else:
        print(rendered)
    return 0 if data["ok"] else 1
