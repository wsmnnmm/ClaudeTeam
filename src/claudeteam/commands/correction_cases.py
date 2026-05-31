"""`claudeteam correction-cases` — run boss-correction regression cases."""
from __future__ import annotations

import json
from pathlib import Path

from claudeteam.runtime import boss_experience
from claudeteam.util import (
    atomic_write_text, error_exit, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, reject_extra_args,
)


USAGE = """usage: claudeteam correction-cases [--json] [--out <file>]

Examples:
  claudeteam correction-cases
  claudeteam correction-cases --json
  claudeteam correction-cases --out runtime-health/correction-cases.md
"""


def build_payload() -> dict:
    rows = boss_experience.run_correction_cases()
    passed = sum(1 for row in rows if row["passed"])
    return {
        "ok": passed == len(rows),
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "cases": rows,
    }


def render_markdown(data: dict) -> str:
    lines = [
        "# 老板纠偏案例回归",
        "",
        f"结论: {'通过' if data['ok'] else '失败'}",
        f"通过: {data['passed']} / {data['total']}",
        "",
        "| 状态 | 案例 | 检查项 | bad 命中 | good 干净 |",
        "|---|---|---|---:|---:|",
    ]
    for row in data["cases"]:
        lines.append(
            f"| {'PASS' if row['passed'] else 'FAIL'} | {row['id']} | "
            f"{row['expected']} | {row['bad_detected']} | {row['good_clean']} |"
        )
    lines.extend([
        "",
        "## 用法",
        "",
        "每次老板纠偏，都应该新增一个 bad/good 案例：bad 是团队原始错误，"
        "good 是老板期望的人类可消费输出。CI 必须保证 bad 会被抓住，good 不被误伤。",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    out_arg = pop_flag(rest, "--out")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    data = build_payload()
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
