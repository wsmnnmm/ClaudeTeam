"""`claudeteam boss-experience-audit` — check boss-visible outputs."""
from __future__ import annotations

import json
from pathlib import Path

from claudeteam.runtime import boss_experience
from claudeteam.util import (
    atomic_write_text, error_exit, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, reject_extra_args,
)


USAGE = """usage: claudeteam boss-experience-audit [--root <dir>] [--json]
                                            [--out <file>] [--max-files <n>]
                                            [path ...]

Examples:
  claudeteam boss-experience-audit runtime-health/boss-brief.md
  claudeteam boss-experience-audit --root /Users/wsm/Project/ClaudeTeam --out runtime-health/boss-experience-audit.md
"""


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


def _default_paths(root: Path) -> list[Path]:
    candidates = [
        root / "runtime-health" / "boss-brief.md",
        root / "artifacts" / "cross-team",
    ]
    return [path for path in candidates if path.exists()]


def _payload(scanned: list[dict], issues: list[boss_experience.ExperienceIssue]) -> dict:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1
    return {
        "ok": not issues,
        "files_scanned": len(scanned),
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "source": issue.source,
                "message": issue.message,
                "excerpt": issue.excerpt,
            }
            for issue in issues
        ],
        "issue_counts": counts,
        "scanned": scanned,
    }


def _render_markdown(data: dict) -> str:
    lines = [
        "# 老板体验巡检报告",
        "",
        f"结论: {'通过' if data['ok'] else '发现问题'}",
        f"扫描文件: {data['files_scanned']}",
        f"问题数: {len(data['issues'])}",
        "",
        "## 问题",
    ]
    if not data["issues"]:
        lines.append("- 无")
    for issue in data["issues"]:
        lines.append(
            f"- [{issue['severity']}] {issue['code']}｜{issue['source']}｜"
            f"{issue['message']}｜{issue['excerpt']}"
        )
    lines.extend([
        "",
        "## 验收口径",
        "",
        "- 老板可见文本第一屏必须有结论、状态或下一步。",
        "- 本地路径只能做证据索引，不能做主交付。",
        "- 微信/飞书输出不能泄露多维表格字段操作。",
        "- 单导师问题不能夹带另一位导师入口或署名。",
        "- 图片、UI、页面还原必须有可见证据。",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    root_arg = pop_flag(rest, "--root")
    out_arg = pop_flag(rest, "--out")
    max_files_arg = pop_flag(rest, "--max-files")
    if (rc := reject_extra_args([a for a in rest if a.startswith("--")], USAGE)) is not None:
        return rc
    try:
        max_files = _parse_positive_int(max_files_arg, default=200, flag="--max-files")
    except ValueError as exc:
        return error_exit(f"❌ {exc}")
    root = Path(root_arg).expanduser().resolve() if root_arg else Path.cwd()
    paths = [Path(item).expanduser().resolve() for item in rest] or _default_paths(root)
    scanned, issues = boss_experience.audit_paths(paths, max_files=max_files)
    data = _payload(scanned, issues)
    rendered = json.dumps(data, ensure_ascii=False, indent=2) if as_json else _render_markdown(data)
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
