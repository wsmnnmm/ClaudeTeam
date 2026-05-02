"""`claudeteam log <agent> <kind> <content> [ref]`

Append a workspace log entry.  Append-only JSONL; agents leave a trail
that can be tailed for audit / replay.
"""
from __future__ import annotations

import sys

from claudeteam.store import local_facts


USAGE = "usage: claudeteam log <agent> <kind> <content> [ref]"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(USAGE, file=sys.stderr)
        return 1
    agent, kind, content = argv[0], argv[1], argv[2]
    ref = argv[3] if len(argv) > 3 else ""
    local_id = local_facts.append_log(agent, kind, content, ref=ref)
    print(f"📝 logged: {agent}/{kind}  [{local_id}]")
    return 0
