"""`claudeteam team`

Show the latest status for every agent that has reported one.  Single-line
per agent: `name  status  task  [⛔ blocker]  (Nm ago)`.
"""
from __future__ import annotations

import time

from claudeteam.store import local_facts


def _ago(ms: int) -> str:
    if not ms:
        return "?"
    delta_secs = max(0, int((time.time() * 1000 - ms) / 1000))
    if delta_secs < 60:
        return f"{delta_secs}s ago"
    if delta_secs < 3600:
        return f"{delta_secs // 60}m ago"
    if delta_secs < 86400:
        return f"{delta_secs // 3600}h ago"
    return f"{delta_secs // 86400}d ago"


def main(argv: list[str]) -> int:
    rows = local_facts.list_all_statuses()
    if not rows:
        print("👥 no agents have reported status yet")
        return 0
    name_w = max(len(r["agent"]) for r in rows)
    for r in rows:
        line = (
            f"{r['agent'].ljust(name_w)}  "
            f"{r['status']}  {r['task']}"
        )
        if r.get("blocker"):
            line += f"  ⛔ {r['blocker']}"
        line += f"  ({_ago(r.get('updated_at', 0))})"
        print(line)
    return 0
