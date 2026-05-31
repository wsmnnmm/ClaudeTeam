"""`claudeteam send <to> <from> <message> [priority] [--task-id <T-id>] [--artifact <path>] [--done] [--no-task] [--no-inject]`

Append a message to the local inbox AND poke the recipient's tmux
pane so they know to read it.

Previously inbox-only with the doc claim "only the Feishu
router can do tmux inject". That broke peer messaging end-to-end —
manager sending to worker_cc wrote a row, but worker_cc had no way
to know unless it polled. Boss-flagged after the 全员报道 e2e where
manager.send → worker_cc went into a dead drop.

Now mirrors the router's apply pattern: append_message + tmux.inject
into the recipient's pane. Recipient's claude (or other CLI) sees a
prompt-style notification and processes inbox proactively. Pass
`--no-inject` to keep the old "silent dead-drop" behaviour for
audit-only writes (caller is putting context for later, not
expecting recipient to read NOW).
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.agents import adapter_for_agent, identity as _identity
from claudeteam.runtime import (
    artifact_gate, config, lifecycle, manager_action_guard, paths, team_command,
    tmux, wake,
)
from claudeteam.feishu.text import normalize_visible_escapes
from claudeteam.store import local_facts, memory, tasks
from claudeteam.util import error_exit, pop_bool_flag, pop_flag, usage_error


USAGE = (
    "usage: claudeteam send <to> <from> <message> [priority] "
    "[--task-id <T-id>] [--artifact <path>] [--done] [--no-task] [--no-inject]"
)
_ASSIGNMENT_MEMORY_MAX_CHARS = 800


def _task_title(message: str) -> str:
    line = next((ln.strip() for ln in str(message or "").splitlines() if ln.strip()), "")
    if not line:
        return "untitled task"
    return line if len(line) <= 80 else (line[:77].rstrip() + "...")


def _compact_assignment_memory(message: str) -> str:
    """Keep send→memory navigable without storing whole prompts/secrets."""
    try:
        message = memory._redact_sensitive_text(message)
    except AttributeError:
        message = str(message or "")
    compact = " ".join(str(message or "").split())
    if len(compact) <= _ASSIGNMENT_MEMORY_MAX_CHARS:
        return compact
    return compact[:_ASSIGNMENT_MEMORY_MAX_CHARS].rstrip() + "..."


def _is_worker(agent: str) -> bool:
    return bool(agent) and agent.startswith("worker")


def _worker_report_to_manager(to: str, frm: str) -> bool:
    return to == "manager" and _is_worker(frm)


def _unknown_local_recipient(to: str) -> str:
    agents = config.agent_names()
    if not agents or to in agents:
        return ""
    known = ", ".join(agents[:12])
    if len(agents) > 12:
        known += ", ..."
    return (
        f"❌ unknown local recipient: {to}. Known local agents: {known}. "
        "For another team, use `claudeteam cross-send <team> manager <from> <message>`."
    )


def _open_tasks_for(agent: str) -> list[dict]:
    return [
        t for t in tasks.list_tasks(assignee=agent)
        if t.get("status") not in tasks.TERMINAL_STATUSES
    ]


def _manager_delegated_task_to_worker(task_id: str, worker: str) -> bool:
    return any(
        str(msg.get("from") or "") == "manager"
        and str(msg.get("task_id") or "") == task_id
        for msg in local_facts.list_messages(worker)
    )


def _artifact_reference_exists(artifact: str) -> bool:
    return artifact_gate.existing_artifact_reference(
        artifact, base_dirs=[Path.cwd(), paths.state_dir().parent])


def _ui_completion_gate_error(task_id: str, artifact: str,
                              task: dict, message: str) -> str:
    context = "\n".join([
        str(task.get("title") or ""),
        str(task.get("description") or ""),
        str(message or ""),
    ])
    evidence = artifact_gate.ui_evidence(
        artifact,
        context_text=context,
        base_dirs=[Path.cwd(), paths.state_dir().parent],
    )
    if evidence.passed:
        return ""
    missing = " and ".join(evidence.missing)
    return (
        f"❌ worker completion for {task_id} looks like UI/page restoration "
        f"but lacks {missing}; provide an artifact report with a real screenshot "
        "image and a clickable http(s) preview URL")


def main(argv: list[str]) -> int:
    rest = list(argv)
    task_id = pop_flag(rest, "--task-id") or ""
    artifact = pop_flag(rest, "--artifact") or ""
    done = pop_bool_flag(rest, "--done")
    no_task = pop_bool_flag(rest, "--no-task")
    no_inject = pop_bool_flag(rest, "--no-inject")
    if len(rest) < 3:
        return usage_error(USAGE)
    if task_id and no_task:
        return error_exit("❌ --task-id and --no-task cannot be used together")
    if (artifact or done) and no_task:
        return error_exit("❌ --artifact/--done require a tracked task; remove --no-task")
    to, frm, message = rest[0], rest[1], normalize_visible_escapes(rest[2])
    priority = rest[3] if len(rest) > 3 else "中"
    if err := _unknown_local_recipient(to):
        return error_exit(err)
    local_facts.touch_heartbeat(frm)
    worker_report = _worker_report_to_manager(to, frm)
    bound_task = None
    if task_id:
        bound_task = tasks.get(task_id)
        if bound_task is None:
            return error_exit(f"❌ no such task: {task_id}")
    elif worker_report and not no_task:
        open_tasks = _open_tasks_for(frm)
        if len(open_tasks) == 1:
            bound_task = open_tasks[0]
            task_id = str(bound_task.get("id") or "")
        elif not open_tasks:
            return error_exit(
                f"❌ {frm} has no open tracked task; ask manager to派单 or use --no-task")
        else:
            task_list = ", ".join(str(t.get("id") or "?") for t in open_tasks[:5])
            return error_exit(
                f"❌ {frm} has multiple open tasks ({task_list}); send progress with --task-id <T-id>")
    elif not no_task:
        title = _task_title(message)
        desc = message if message.strip() != title else ""
        task_id = tasks.create(to, title, description=desc, creator=frm)
        bound_task = tasks.get(task_id)
    manager_owned_delegation = False
    if worker_report and bound_task is not None:
        assignee = str(bound_task.get("assignee") or "")
        if assignee != frm:
            if assignee == "manager" and _manager_delegated_task_to_worker(task_id, frm):
                manager_owned_delegation = True
            else:
                return error_exit(f"❌ task {task_id} belongs to {assignee}, not {frm}")
    effective_artifact = artifact or str((bound_task or {}).get("artifact_path") or "")
    if done and not effective_artifact:
        return error_exit(
            f"❌ worker completion for {task_id or '?'} must include --artifact <path>")
    if artifact and task_id and not manager_owned_delegation:
        tasks.update(task_id, artifact_path=artifact)
    if done and task_id:
        current = tasks.get(task_id) or bound_task or {}
        current_status = str(current.get("status") or "")
        if current_status in tasks.TERMINAL_STATUSES:
            return error_exit(
                f"❌ task {task_id} is already {current_status}; "
                "open a new task or ask manager to reopen it explicitly")
        if not _artifact_reference_exists(effective_artifact):
            return error_exit(
                f"❌ worker completion for {task_id} has missing artifact: "
                f"{effective_artifact}; write the evidence file first or pass a real URL")
        gate_error = _ui_completion_gate_error(
            task_id, effective_artifact, current, message)
        if gate_error:
            return error_exit(gate_error)
        if manager_owned_delegation:
            if current_status == tasks.DEFAULT_STATUS:
                tasks.update(task_id, status="进行中")
        else:
            tasks.update(task_id, status="待验收", artifact_path=effective_artifact)
    visible_message = message
    if artifact and artifact not in visible_message:
        visible_message = f"{visible_message}\nArtifact: {artifact}"
    if done and manager_owned_delegation and "待主管汇总" not in visible_message:
        visible_message = f"{visible_message}\nStatus: 员工已交付，待主管汇总"
    elif done and "待验收" not in visible_message:
        visible_message = f"{visible_message}\nStatus: 待验收"
    local_id = local_facts.append_message(
        to, frm, visible_message, priority=priority, task_id=task_id,
        artifact=effective_artifact)
    task_prefix = f"[{task_id}] " if task_id else ""
    memory_message = _compact_assignment_memory(visible_message)
    memory.append(to, "task_assigned", f"{task_prefix}{memory_message}", ref=local_id)
    if frm:
        memory.append(frm, "task_assigned",
                      f"已派给 {to}{f' ({task_id})' if task_id else ''}: {memory_message}",
                      ref=local_id)
    if frm == "manager" and _is_worker(to):
        manager_action_guard.mark_delegate(
            to, visible_message, task_id=task_id, ref=local_id)
    suffix = f"  [task_id={task_id}]" if task_id else ""
    if effective_artifact:
        suffix += f"  [artifact={effective_artifact}]"
    if done and manager_owned_delegation:
        suffix += "  [handoff=待主管汇总]"
    elif done:
        suffix += "  [status=待验收]"
    print(f"📥 inbox: {to} ← {frm}  [local_id={local_id}]{suffix}")
    if no_inject:
        return 0
    # Best-effort tmux inject so the recipient's pane sees a nudge to
    # read inbox. Failures here (no session, no pane, unknown adapter)
    # don't fail the command — the inbox row is still the canonical
    # record the recipient will pick up next time they re-init or
    # /clear and re-read identity.
    try:
        session = config.session_name()
        target = tmux.Target(session, to)
        if not tmux.has_window(target):
            return 0
        adapter = adapter_for_agent(to)
        # Lazy worker only: pane exists as placeholder shell, CLI hasn't
        # spawned yet. Without wake_if_dormant the inject below would land
        # in the shell, not the CLI — agent never sees the message.
        # REGRESSION 2026-05-06 host_smoke §7: lazy worker_codex received
        # a manager dispatch but pane stayed at a bare shell prompt.
        # Non-lazy agents (typically manager + active workers) are
        # ALREADY started by `claudeteam up`; injecting straight in is
        # faster than the is_ready capture-pane round-trip and matches
        # the boss preference 2026-05-06: "send 主管时不需要等待他空闲,
        # 直接往 session 里面加告诉他就行了". Claude / Codex pane stash
        # injected text into the input buffer if mid-thought; it's read
        # on the next input-accept turn.
        cfg = config.agent_config(to) if to in config.agent_names() else {}
        if cfg.get("lazy") and not wake.is_ready(target, adapter):
            from claudeteam.runtime import tunables
            wake.wake_if_dormant(
                target, adapter,
                spawn_cmd=lifecycle.lazy_spawn_cmd(to),
                init_msg=_identity.init_prompt(to),
                timeout_s=float(tunables.tunable("wake.lazy_wake_timeout_s", 30.0)),
                on_woken=lambda: local_facts.upsert_status(
                    to, "进行中", "responding to first message"),
            )
        ct = team_command.safe_cli_cmd(ensure=True)
        task_hint = (f"先 `{ct} task get {task_id}` 看任务卡；"
                     if task_id else
                     f"先 `{ct} task list --assignee {to} --active` 对账当前活跃任务；")
        if _is_worker(to):
            reply_hint = (
                f"内部回执用 `{ct} send {frm} {to} \"...\"`"
                f"{' --task-id ' + task_id if task_id else ' --task-id <T-id>'}；"
                "只有真实交付、真实 blocker、需要老板动作或老板点名要你直报时，"
                f"才用 stdin 形式 `{ct} say {to} - --to user`。"
                "对齐、待命、继续监控、无新事实不要 `say` 刷群。"
            )
        else:
            reply_hint = f"必要时用 stdin 形式 `{ct} say {to} - --to user`。"
        batch_hint = (
            "低成本提示：简单确认/内部回执可以把 inbox、task、read、status、send "
            "合并进一条 Bash；不要为每个小命令分一轮。"
        )
        nudge_message = _compact_assignment_memory(visible_message)
        nudge = (f"📥 {frm} → {to}（{local_id}"
                 f"{f' / {task_id}' if task_id else ''}）。"
                 f"消息摘要：{nudge_message}。"
                 f"{task_hint}`{ct} inbox {to}` → "
                 f"先 `{ct} read {local_id}` 接手"
                 f"{f'（会把 {task_id} 标为进行中）' if task_id else ''} → "
                 f"`{ct} status {to} 进行中 \"{task_id or local_id}\"` → "
                 f"开始处理。read/status 只是接手信号，不是完成；"
                 f"{batch_hint}{reply_hint}")
        tmux.inject(target, nudge, submit_keys=adapter.submit_keys())
    except Exception as e:
        print(f"  ⚠️ tmux inject best-effort failed for {to}: {e}")
    return 0
