"""`claudeteam read <local_id>`

Mark a message as read by its local id.  Returns 1 if no such message.
"""
from __future__ import annotations

from claudeteam.store import local_facts, memory
from claudeteam.util import error_exit, usage_error


USAGE = "usage: claudeteam read <local_id>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    local_id = argv[0]
    row = local_facts.get_message(local_id)
    if row is None:
        return error_exit(f"❌ no such message: {local_id}")
    if not local_facts.mark_read(local_id):
        return error_exit(f"❌ no such message: {local_id}")
    agent = str(row.get("to", "") or "")
    sender = str(row.get("from", "") or "?")
    content = str(row.get("content", "") or "")
    if agent and content:
        memory.append(agent, "note",
                      f"已接手来自 {sender} 的任务: {content}", ref=local_id)
    print(f"✅ marked read: {local_id}")
    return 0
