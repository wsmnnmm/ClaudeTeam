"""`claudeteam send <to> <from> <message> [priority]`

Append a message to the local inbox.  Pure local — no Feishu, no tmux.
"""
from __future__ import annotations

import sys

from claudeteam.store import local_facts


USAGE = "usage: claudeteam send <to> <from> <message> [priority]"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(USAGE, file=sys.stderr)
        return 1
    to, frm, message = argv[0], argv[1], argv[2]
    priority = argv[3] if len(argv) > 3 else "中"
    local_facts.touch_heartbeat(frm)
    local_id = local_facts.append_message(to, frm, message, priority=priority)
    print(f"sent → {to}  [local_id={local_id}]")
    return 0
