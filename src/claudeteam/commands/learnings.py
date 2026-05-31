"""`claudeteam learnings list|stats|relevant [--limit N] [--json]`

View the L5 self-evolution learning pool — incidents that the system
automatically classified into lessons and cross-team shared learnings.

Subcommands:
  list                  Show recent learnings, newest first
  stats                 Show system-wide effectiveness statistics
  relevant <task_id>    Find past learnings relevant to a task
"""
from __future__ import annotations

from claudeteam.runtime import incident_learning
from claudeteam.store import tasks
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, usage_error,
)


USAGE = (
    "usage: claudeteam learnings <list|stats|relevant> [--limit N] [--json]\n"
    "       claudeteam learnings relevant <task_id>"
)

_DEFAULT_LIMIT = 20


def _cmd_list(argv: list[str]) -> int:
    rest = list(argv)
    as_json = pop_bool_flag(rest, "--json")
    raw_limit = pop_flag(rest, "--limit")
    try:
        limit = int(raw_limit) if raw_limit else _DEFAULT_LIMIT
    except ValueError:
        return error_exit(f"--limit must be an integer (got {raw_limit!r})")
    if limit < 1:
        return error_exit("--limit must be >= 1")

    rows = incident_learning.list_learnings(limit=limit)

    if as_json:
        print_json(rows)
        return 0

    if not rows:
        print("No learnings recorded yet.")
        return 0
    print(f"{len(rows)} learning{'s' if len(rows) != 1 else ''} (newest first, capped at {limit})")
    for row in rows:
        ts = fmt_time_ms(row.get("created_at", 0))
        cat = row.get("category", "?")
        lesson = row.get("lesson", "")
        seen = row.get("seen_count", 0)
        prevented = row.get("prevented_count", 0)
        failed = row.get("failed_count", 0)
        lid = row.get("learning_id", "")
        print(f"  [{ts}] [{cat}] {lid}")
        print(f"    {lesson}")
        print(f"    seen={seen} prevented={prevented} failed={failed}")
    return 0


def _cmd_stats(argv: list[str]) -> int:
    rest = list(argv)
    as_json = pop_bool_flag(rest, "--json")
    s = incident_learning.stats()

    if as_json:
        print_json(s)
        return 0

    print("L5 自进化系统统计")
    print(f"  总学习条目: {s['total_learnings']}")
    print(f"  已应用:     {s['learnings_applied']}")
    print(f"  无效学习:   {s['learnings_ineffective']}")
    print(f"  7天内新增:  {s['recent_7d']}")
    print(f"  应用率:     {s['application_rate']}%")
    if s.get("by_category"):
        print("  按类别:")
        for cat, count in s["by_category"].items():
            print(f"    {cat}: {count}")
    if s.get("by_severity"):
        print("  按严重程度:")
        for sev, count in s["by_severity"].items():
            print(f"    {sev}: {count}")
    return 0


def _cmd_relevant(argv: list[str]) -> int:
    rest = list(argv)
    as_json = pop_bool_flag(rest, "--json")
    raw_limit = pop_flag(rest, "--limit")
    try:
        limit = int(raw_limit) if raw_limit else 5
    except ValueError:
        return error_exit(f"--limit must be an integer (got {raw_limit!r})")

    if not rest:
        return error_exit("usage: claudeteam learnings relevant <task_id>")

    task_id = rest[0]
    task = tasks.get(task_id)
    if not task:
        return error_exit(f"task {task_id} not found")

    title = str(task.get("title") or "")
    description = str(task.get("description") or "")
    assignee = str(task.get("assignee") or "")
    rows = incident_learning.find_relevant(
        task_title=title, task_description=description,
        assignee=assignee, limit=limit,
    )

    if as_json:
        print_json(rows)
        return 0

    if not rows:
        print(f"No relevant learnings found for {task_id}")
        return 0
    print(f"{len(rows)} relevant learning{'s' if len(rows) != 1 else ''} for {task_id}:")
    for i, row in enumerate(rows, 1):
        lesson = row.get("lesson", "")
        cat = row.get("category", "?")
        seen = row.get("seen_count", 0)
        prevented = row.get("prevented_count", 0)
        print(f"  {i}. [{cat}] {lesson}")
        print(f"     seen={seen} prevented={prevented}")
    return 0


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if not rest:
        return usage_error(USAGE)
    sub = rest[0]
    rest = rest[1:]

    if sub == "list":
        return _cmd_list(rest)
    if sub == "stats":
        return _cmd_stats(rest)
    if sub == "relevant":
        return _cmd_relevant(rest)
    return usage_error(USAGE)
