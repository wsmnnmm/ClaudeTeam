"""`claudeteam read <local_id>`

Mark a message as read by its local id.  Returns 1 if no such message.
"""
from __future__ import annotations

from claudeteam.runtime import manager_action_guard
from claudeteam.store import local_facts, memory, tasks
from claudeteam.util import error_exit, usage_error


USAGE = "usage: claudeteam read <local_id>"
_TAKEOVER_MEMORY_MAX_CHARS = 600


def _compact_takeover_memory(content: str) -> str:
    """Keep read→memory useful without storing giant prompts or secrets."""
    try:
        content = memory._redact_sensitive_text(content)
    except AttributeError:
        content = str(content or "")
    compact = " ".join(str(content or "").split())
    if len(compact) <= _TAKEOVER_MEMORY_MAX_CHARS:
        return compact
    return compact[:_TAKEOVER_MEMORY_MAX_CHARS].rstrip() + "..."


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    local_id = argv[0]
    row = local_facts.get_message(local_id)
    if row is None:
        return error_exit(f"❌ no such message: {local_id}")
    if not local_facts.mark_read(local_id):
        return error_exit(f"❌ no such message: {local_id}")
    read_row = local_facts.get_message(local_id) or row
    manager_action_guard.record_boss_read(read_row)
    agent = str(row.get("to", "") or "")
    sender = str(row.get("from", "") or "?")
    content = str(row.get("content", "") or "")
    task_id = str(row.get("task_id", "") or "")
    artifact = str(row.get("artifact", "") or "")
    if agent and content:
        prefix = f"[{task_id}] " if task_id else ""
        artifact_note = f" artifact={artifact}" if artifact else ""
        content = _compact_takeover_memory(content)
        memory.append(agent, "note",
                      f"{prefix}已接手来自 {sender} 的任务: {content}{artifact_note}",
                      ref=local_id)
    task_note = ""
    if task_id:
        task = tasks.get(task_id)
        if task is not None and str(task.get("assignee") or "") == agent:
            status = str(task.get("status") or "")
            if status == tasks.DEFAULT_STATUS:
                tasks.update(task_id, status="进行中")
                task_note = f" (task {task_id} -> 进行中)"
            if status in {tasks.DEFAULT_STATUS, "进行中"}:
                title = str(task.get("title") or "").strip() or "任务处理中"
                local_facts.upsert_status(agent, "进行中", f"{task_id}: {title}")
    if agent:
        local_facts.touch_heartbeat(agent)
    print(f"✅ marked read: {local_id}{task_note}")
    return 0
