"""`claudeteam workspace <agent> [--limit N]`

Read back the last N log entries for one agent (default 20).  Tail of the
audit log per agent.
"""
from __future__ import annotations

import sys

from claudeteam.store import local_facts
from claudeteam.util import fmt_time_ms, pop_flag


USAGE = "usage: claudeteam workspace <agent> [--limit N]"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print(USAGE, file=sys.stderr)
        return 1
    rest = list(argv)
    agent = rest.pop(0)
    raw_limit = pop_flag(rest, "--limit")
    if rest:
        print(USAGE, file=sys.stderr)
        return 1
    try:
        limit = int(raw_limit) if raw_limit is not None else 20
    except ValueError:
        print(USAGE, file=sys.stderr)
        return 1

    rows = local_facts.list_logs(agent, limit=limit)
    if not rows:
        print(f"📂 {agent}: no log entries")
        return 0
    print(f"📂 {agent}: last {len(rows)} log entries")
    for r in rows:
        ref = f"  ({r['ref']})" if r.get("ref") else ""
        print(f"── [{fmt_time_ms(r.get('created_at', 0), fmt="%m-%d %H:%M:%S")}] {r.get('type', '?')}{ref}")
        print(f"   {r.get('content', '')}")
    return 0
