"""`claudeteam recall <agent> [--limit N] [--json]`

Print an agent's durable memory entries. Symmetric to `claudeteam remember`.

Use cases:
  - operator audit: `claudeteam recall manager` to see what manager has
    been remembering across /clear cycles.
  - manager 巡视 a worker: `claudeteam recall worker_cc` from manager's
    pane to check what the worker has stored without going into worker_cc's
    tmux window.
  - debugging "agent forgot the task" — verify whether the memory entry
    was actually written.

Default output is human-readable bullets; `--json` dumps the underlying
records for piping to jq / smoke conductors.
"""
from __future__ import annotations

from claudeteam.store import memory
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, usage_error,
)


USAGE = "usage: claudeteam recall <agent> [--limit N] [--json]"

_DEFAULT_LIMIT = 20


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    raw_limit = pop_flag(rest, "--limit")
    try:
        limit = int(raw_limit) if raw_limit else _DEFAULT_LIMIT
    except ValueError:
        return error_exit(f"❌ --limit must be an integer (got {raw_limit!r})")
    if limit < 1:
        return error_exit("❌ --limit must be >= 1")
    if len(rest) < 1:
        return usage_error(USAGE)
    agent = rest[0]

    rows = memory.list_recent(agent, limit=limit)
    if as_json:
        print_json(rows)
        return 0

    if not rows:
        print(f"🧠 {agent}: no memory entries")
        return 0
    print(f"🧠 {agent}: {len(rows)} entr{'ies' if len(rows) != 1 else 'y'} "
          f"(oldest first, capped at {limit})")
    for row in rows:
        ts = fmt_time_ms(row.get("created_at", 0))
        kind = row.get("kind", "?")
        content = row.get("content", "")
        ref = row.get("ref", "")
        suffix = f"  (ref={ref})" if ref else ""
        print(f"  [{ts}] [{kind}] {content}{suffix}")
    return 0
