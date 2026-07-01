"""`claudeteam task <subcommand>`

  task create <assignee> <title> [--by <agent>] [--desc <text>] [--artifact <path>]
                                   [--topic <name>]
                                   [--parent <T-id>]
                                   [--stage idea|mvp|launch|scale]
                                   [--evidence <text>] [--evidence-action <text>]
                                   [--non-goal <text>] [--issue-class <type>]
                                   [--segment <segment>] [--next-window <text>]
                                   [--base-absorb-needed yes|no]
  task update <id>       [--status S] [--assignee A] [--title T] [--desc D] [--artifact <path>] [--by <agent>]
                                   [--topic <name>]
                                   [--parent <T-id>]
                                   [--stage idea|mvp|launch|scale]
                                   [--evidence <text>] [--evidence-action <text>]
                                   [--non-goal <text>] [--issue-class <type>]
                                   [--segment <segment>] [--next-window <text>]
                                   [--base-absorb-needed yes|no]
  task list              [--status S] [--assignee A] [--topic <name>] [--parent <T-id>] [--active]
  task get <id>
  task done <id>         [--artifact <path>] [--by <agent>]
  task audit             [--assignee A] [--topic <name>] [--parent <T-id>] [--all] [--json]
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.commands import founder_os
from claudeteam.runtime import artifact_gate, paths
from claudeteam.store import local_facts, tasks
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_flag, usage_error,
    pop_bool_flag, print_json, reject_extra_args,
)


USAGE = (
    "usage:\n"
    "  claudeteam task create <assignee> <title> [--by <agent>] [--desc <text>] [--artifact <path>] [--intent I-n]\n"
    "                     [--topic <name>] [--parent <T-id>] [--stage idea|mvp|launch|scale] [--evidence <text>]\n"
    "                     [--evidence-action <text>] [--non-goal <text>] [--issue-class <type>]\n"
    "                     [--segment <segment>] [--next-window <text>] [--base-absorb-needed yes|no]\n"
    "  claudeteam task update <id>  [--status S] [--assignee A] [--title T] [--desc D] [--artifact <path>] [--by <agent>]\n"
    "                     [--topic <name>] [--parent <T-id>] [--stage idea|mvp|launch|scale] [--evidence <text>]\n"
    "                     [--evidence-action <text>] [--non-goal <text>] [--issue-class <type>]\n"
    "                     [--segment <segment>] [--next-window <text>] [--base-absorb-needed yes|no]\n"
    "  claudeteam task list  [--status S] [--assignee A] [--topic <name>] [--parent <T-id>] [--active]\n"
    "  claudeteam task get <id>\n"
    "  claudeteam task done <id> [--artifact <path>] [--by <agent>]\n"
    "  claudeteam task pause <id> [--note <why>] [--to <who>] [--by <agent>]\n"
    "  claudeteam task approve <id> [--done] [--note <text>] [--artifact <path>] [--by <agent>]\n"
    "  claudeteam task reject <id> <feedback> [--cancel]\n"
    "  claudeteam task void <id> [--note <why>] [--by <agent>]\n"
    "  claudeteam task intent create <raw...> [--src <msg_id>] [--key <points>] [--by <agent>]\n"
    "  claudeteam task intent get <I-n>\n"
    "  claudeteam task audit [--assignee A] [--topic <name>] [--parent <T-id>] [--all] [--json]"
)


def _refresh_anchor(*agents: str) -> None:
    """Best-effort refresh of affected assignees' live intent anchor."""
    from claudeteam.agents import identity
    seen: set[str] = set()
    for agent in agents:
        if not agent or agent in seen:
            continue
        seen.add(agent)
        identity.refresh_native_memory(agent)
        _reidentify_stale_anchor(agent)


def _reidentify_stale_anchor(agent: str) -> None:
    """Push a fresh init prompt to non-reloading CLIs when their pane is idle."""
    try:
        from claudeteam.agents import adapter_for_agent, identity
        from claudeteam.runtime import config, pane_probe, tmux, wake
        adapter = adapter_for_agent(agent)
        if adapter.native_memory_reloads():
            return
        session = config.session_name()
        target = tmux.Target(session, agent)
        if not tmux.has_session(session) or not tmux.has_window(target):
            return
        if pane_probe.probe(target) != pane_probe.IDLE:
            return
        wake.inject_and_confirm(target, adapter, identity.init_prompt(agent))
    except Exception:
        pass


def _stage_from_cli(raw: str | None) -> str:
    if raw is None:
        return ""
    stage = founder_os._normalise_stage(raw)
    if not stage:
        raise ValueError(f"unknown founder stage: {raw}")
    return stage


def _fmt_task(t: dict) -> list[str]:
    ts = fmt_time_ms(t["created_at"])
    head = f"{t['id']}  [{t['status']}]  {t['title']}"
    body = [f"  assignee: {t.get('assignee') or '-'}"]
    if t.get("creator"):
        body.append(f"  by: {t['creator']}")
    if t.get("intent_id"):
        body.append(f"  intent: {t['intent_id']}")
    if t.get("topic"):
        body.append(f"  topic: #{t['topic']}")
    if t.get("parent_task_id"):
        body.append(f"  parent_task: {t['parent_task_id']}")
    if t.get("description"):
        body.append(f"  desc: {t['description']}")
    if t.get("artifact_path"):
        body.append(f"  artifact: {t['artifact_path']}")
    if t.get("founder_stage"):
        body.append(f"  stage: {t['founder_stage']}")
    if t.get("stage_exit_evidence"):
        body.append(f"  evidence: {t['stage_exit_evidence']}")
    if t.get("evidence_action"):
        body.append(f"  evidence_action: {t['evidence_action']}")
    if t.get("non_goal"):
        body.append(f"  non_goal: {t['non_goal']}")
    if t.get("issue_class"):
        body.append(f"  issue_class: {t['issue_class']}")
    if t.get("current_segment"):
        body.append(f"  current_segment: {t['current_segment']}")
    if t.get("next_natural_window"):
        body.append(f"  next_natural_window: {t['next_natural_window']}")
    if t.get("base_absorb_needed"):
        body.append(f"  base_absorb_needed: {t['base_absorb_needed']}")
    if t.get("reviewed_by"):
        body.append(f"  reviewed_by: {t['reviewed_by']}")
    if t.get("child_rollup"):
        body.append(f"  child_tasks: {t['child_rollup']}")
    if t.get("status") == tasks.SUSPEND_STATUS:
        body.append(f"  awaiting: {t.get('awaiting') or '-'}")
        if t.get("approval_note"):
            body.append(f"  note: {t['approval_note']}")
    body.append(f"  created: {ts}")
    return [head] + body


def _auto_memory(agent: str, kind: str, content: str, *, ref: str = "") -> None:
    """Auto-record a task-lifecycle event into the assignee's durable memory.

    The highest-value memories (assigned / done / blocked) were the ones most
    often MISSING, because memory.append's only writer is the manual
    `claudeteam remember` — nothing wrote on task transitions, so capture was
    left to the agent remembering to run the command (memory's core
    unreliability). This records them by CODE instead.

    Memory stays a best-effort *notepad* — the authoritative record is the
    task/intent store, not this. To avoid flooding (memory caps at ~200), we
    write ONE brief line per REAL state change only; content is title-capped.
    Best-effort: a memory write must never fail the task command."""
    if not agent:
        return
    try:
        from claudeteam.store import memory
        memory.append(agent, kind, content, ref=ref)
    except Exception:
        pass


def _mem_title(t: dict | None) -> str:
    """Short task label for a memory line (id + capped title)."""
    if not t:
        return ""
    title = (t.get("title") or "")[:50]
    return f"{t.get('id', '')} {title}".strip()


def _cmd_create(rest: list[str]) -> int:
    by = pop_flag(rest, "--by") or ""
    desc = pop_flag(rest, "--desc") or ""
    artifact = pop_flag(rest, "--artifact") or ""
    intent_id = pop_flag(rest, "--intent") or ""
    topic = pop_flag(rest, "--topic") or ""
    parent = pop_flag(rest, "--parent") or ""
    stage_raw = pop_flag(rest, "--stage")
    evidence = pop_flag(rest, "--evidence") or ""
    evidence_action = pop_flag(rest, "--evidence-action") or ""
    non_goal = pop_flag(rest, "--non-goal") or ""
    issue_class = pop_flag(rest, "--issue-class") or ""
    current_segment = pop_flag(rest, "--segment") or ""
    next_natural_window = pop_flag(rest, "--next-window") or ""
    base_absorb_needed = pop_flag(rest, "--base-absorb-needed") or ""
    if len(rest) < 2:
        return usage_error(USAGE)
    assignee = rest[0]
    title = " ".join(rest[1:])
    try:
        stage = _stage_from_cli(stage_raw)
        tid = tasks.create(
            assignee, title, description=desc, creator=by,
            intent_id=intent_id,
            topic=topic, parent_task_id=parent,
            artifact_path=artifact, founder_stage=stage,
            stage_exit_evidence=evidence, evidence_action=evidence_action,
            non_goal=non_goal, issue_class=issue_class,
            current_segment=current_segment,
            next_natural_window=next_natural_window,
            base_absorb_needed=base_absorb_needed)
    except ValueError as e:
        return error_exit(f"❌ {e}")
    _refresh_anchor(assignee)
    intent_note = f" (intent {intent_id})" if intent_id else ""
    _auto_memory(assignee, "task_assigned", f"{tid}{intent_note}", ref=tid)
    print(f"✅ created {tid}: {title} → {assignee}")
    return 0


def _artifact_for_close(tid: str, supplied: str) -> str:
    task = tasks.get(tid)
    if task is None:
        raise ValueError(f"no such task: {tid}")
    return supplied or str(task.get("artifact_path") or "")


def _artifact_reference_exists(artifact: str) -> bool:
    return artifact_gate.existing_artifact_reference(
        artifact, base_dirs=[Path.cwd(), paths.state_dir().parent])


def _require_artifact_file(tid: str, artifact: str) -> int | None:
    if _artifact_reference_exists(artifact):
        return None
    return error_exit(
        f"❌ task {tid} artifact does not exist: {artifact}; "
        "write the evidence file first or pass a real URL")


def _require_ui_evidence(tid: str, artifact: str,
                         task: dict, *,
                         title: str | None = None,
                         desc: str | None = None) -> int | None:
    context = "\n".join([
        str(task.get("title") or ""),
        str(task.get("description") or ""),
        str(title or ""),
        str(desc or ""),
    ])
    evidence = artifact_gate.ui_evidence(
        artifact,
        context_text=context,
        base_dirs=[Path.cwd(), paths.state_dir().parent],
    )
    if evidence.passed:
        return None
    missing = " and ".join(evidence.missing)
    return error_exit(
        f"❌ task {tid} looks like UI/page restoration but lacks {missing}; "
        "provide an artifact report with a real screenshot image and a "
        "clickable http(s) preview URL")


def _cmd_update(rest: list[str]) -> int:
    status = pop_flag(rest, "--status")
    assignee = pop_flag(rest, "--assignee")
    title = pop_flag(rest, "--title")
    desc = pop_flag(rest, "--desc")
    artifact = pop_flag(rest, "--artifact")
    topic = pop_flag(rest, "--topic")
    parent = pop_flag(rest, "--parent")
    reviewed_by = pop_flag(rest, "--by")
    stage_raw = pop_flag(rest, "--stage")
    evidence = pop_flag(rest, "--evidence")
    evidence_action = pop_flag(rest, "--evidence-action")
    non_goal = pop_flag(rest, "--non-goal")
    issue_class = pop_flag(rest, "--issue-class")
    current_segment = pop_flag(rest, "--segment")
    next_natural_window = pop_flag(rest, "--next-window")
    base_absorb_needed = pop_flag(rest, "--base-absorb-needed")
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    before = tasks.get(tid)
    if before is None:
        return error_exit(f"❌ no such task: {tid}")
    if status is not None and tasks.SUSPEND_STATUS in {
            status, str(before.get("status") or "")}:
        return error_exit(
            "❌ 需审批 transitions must use task pause/approve/reject, not update")
    if status in {"待验收", "已完成"}:
        try:
            effective_artifact = _artifact_for_close(tid, artifact or "")
        except ValueError as e:
            return error_exit(f"❌ {e}")
        if not effective_artifact:
            return error_exit(
                f"❌ task {tid} cannot be marked {status} without an artifact; "
                "pass --artifact <path> or set one first")
        missing = _require_artifact_file(tid, effective_artifact)
        if missing is not None:
            return missing
        task = tasks.get(tid) or {}
        ui_missing = _require_ui_evidence(
            tid, effective_artifact, task, title=title, desc=desc)
        if ui_missing is not None:
            return ui_missing
        artifact = effective_artifact
    try:
        founder_stage = _stage_from_cli(stage_raw) if stage_raw is not None else None
        ok = tasks.update(tid, status=status, assignee=assignee,
                          title=title, description=desc,
                          topic=topic,
                          parent_task_id=parent,
                          artifact_path=artifact, reviewed_by=reviewed_by,
                          founder_stage=founder_stage,
                          stage_exit_evidence=evidence,
                          evidence_action=evidence_action,
                          non_goal=non_goal,
                          issue_class=issue_class,
                          current_segment=current_segment,
                          next_natural_window=next_natural_window,
                          base_absorb_needed=base_absorb_needed)
    except ValueError as e:
        return error_exit(f"❌ {e}")
    after = tasks.get(tid)
    # status flips and reassignment both reshape the anchor; a reassign
    # moves it between two agents, so refresh both old and new owner.
    _refresh_anchor(before["assignee"] if before else "",
                    after["assignee"] if after else "")
    # Auto-memory only on a REAL transition INTO 已完成 (covers `task done`);
    # idempotent re-asserts (already 已完成) don't re-record.
    if (after and after.get("status") == "已完成"
            and (not before or before.get("status") != "已完成")):
        _auto_memory(after.get("assignee", ""), "task_completed",
                     f"{_mem_title(after)} 已完成", ref=tid)
    print(f"✅ updated {tid}")
    return 0


def _cmd_done(rest: list[str]) -> int:
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    artifact = pop_flag(rest, "--artifact")
    reviewed_by = pop_flag(rest, "--by") or "manager"
    args = [tid, "--status", "已完成"]
    if artifact:
        args.extend(["--artifact", artifact])
    if reviewed_by:
        args.extend(["--by", reviewed_by])
    return _cmd_update(args)


def _cmd_list(rest: list[str]) -> int:
    status = pop_flag(rest, "--status")
    assignee = pop_flag(rest, "--assignee")
    topic = pop_flag(rest, "--topic")
    parent = pop_flag(rest, "--parent")
    active = pop_bool_flag(rest, "--active")
    rows = tasks.list_tasks(
        status=status, assignee=assignee, topic=topic, parent_task_id=parent)
    if active:
        active_statuses = tasks.VALID_STATUSES - tasks.TERMINAL_STATUSES
        rows = [t for t in rows if t.get("status") in active_statuses]
    if not rows:
        print("📋 no matching tasks")
        return 0
    print(f"📋 {len(rows)} tasks")
    for t in rows:
        for line in _fmt_task(t):
            print(line)
        print()
    return 0


def _cmd_get(rest: list[str]) -> int:
    if len(rest) < 1:
        return usage_error(USAGE)
    t = tasks.get(rest[0])
    if t is None:
        return error_exit(f"❌ no such task: {rest[0]}")
    children = tasks.list_tasks(parent_task_id=str(t.get("id") or ""))
    if children:
        open_children = [
            child for child in children
            if child.get("status") not in tasks.TERMINAL_STATUSES
        ]
        t = dict(t)
        t["child_rollup"] = f"{len(children)} total / {len(open_children)} open"
    for line in _fmt_task(t):
        print(line)
    return 0


def _cmd_pause(rest: list[str]) -> int:
    note = pop_flag(rest, "--note") or ""
    awaiting = pop_flag(rest, "--to") or "user"
    by = pop_flag(rest, "--by") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    if not tasks.pause(tid, awaiting=awaiting, approval_note=note, paused_by=by):
        return error_exit(f"❌ cannot pause {tid} (missing or not 进行中)")
    task = tasks.get(tid) or {}
    assignee = str(task.get("assignee") or "")
    local_facts.append_log(
        assignee, "task_transition",
        f"{tid} 进行中→需审批 (await {awaiting}): {note}",
        ref=tid,
    )
    local_facts.append_message(
        awaiting, by or assignee, note or f"{tid} 需审批",
        priority="高", task_id=tid,
    )
    _refresh_anchor(assignee)
    _auto_memory(
        assignee, "blocker",
        f"{_mem_title(task)} 需审批(await {awaiting})" + (f": {note}" if note else ""),
        ref=tid,
    )
    print(f"⏸️  {tid} 需审批 — awaiting {awaiting}")
    return 0


def _cmd_approve(rest: list[str]) -> int:
    done = pop_bool_flag(rest, "--done")
    note = pop_flag(rest, "--note") or ""
    artifact = pop_flag(rest, "--artifact") or ""
    reviewed_by = pop_flag(rest, "--by") or "manager"
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    before = tasks.get(tid)
    if before is None:
        return error_exit(f"❌ cannot approve {tid} (not 需审批)")
    effective_artifact = ""
    if done:
        try:
            effective_artifact = _artifact_for_close(tid, artifact)
        except ValueError as e:
            return error_exit(f"❌ {e}")
        if not effective_artifact:
            return error_exit(
                f"❌ task {tid} cannot be marked 已完成 without an artifact; "
                "pass --artifact <path> or set one first")
        missing = _require_artifact_file(tid, effective_artifact)
        if missing is not None:
            return missing
        ui_missing = _require_ui_evidence(tid, effective_artifact, before)
        if ui_missing is not None:
            return ui_missing
    if not tasks.approve(tid, done=done, note=note):
        return error_exit(f"❌ cannot approve {tid} (not 需审批)")
    task = tasks.get(tid) or {}
    assignee = str(task.get("assignee") or "")
    if done:
        try:
            tasks.update(
                tid,
                artifact_path=effective_artifact,
                reviewed_by=reviewed_by,
                _force=True,
            )
            task = tasks.get(tid) or task
        except ValueError as e:
            return error_exit(f"❌ {e}")
    suffix = f": {note}" if note else ""
    local_facts.append_log(
        assignee, "task_transition",
        f"{tid} 需审批→{task.get('status')} (approved){suffix}",
        ref=tid,
    )
    local_facts.append_message(
        assignee, "user",
        f"{tid} 已批准{'并完成' if done else '·继续'}{suffix}",
        task_id=tid,
    )
    _refresh_anchor(assignee)
    if done and task.get("status") == "已完成":
        _auto_memory(assignee, "task_completed", f"{_mem_title(task)} 已批准完成", ref=tid)
    print(f"✅ approved {tid} → {task.get('status')}")
    return 0


def _cmd_reject(rest: list[str]) -> int:
    cancel = pop_bool_flag(rest, "--cancel")
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    feedback = " ".join(rest[1:])
    if not tasks.reject(tid, feedback=feedback, cancel=cancel):
        return error_exit(f"❌ cannot reject {tid} (not 需审批)")
    task = tasks.get(tid) or {}
    assignee = str(task.get("assignee") or "")
    verb = "已取消" if cancel else "打回"
    local_facts.append_log(
        assignee, "task_transition",
        f"{tid} 需审批→{task.get('status')} ({verb}): {feedback}",
        ref=tid,
    )
    local_facts.append_message(assignee, "user", f"{tid} {verb}: {feedback}", task_id=tid)
    _refresh_anchor(assignee)
    print(f"↩️  rejected {tid} → {task.get('status')}")
    return 0


def _cmd_void(rest: list[str]) -> int:
    note = pop_flag(rest, "--note") or ""
    by = pop_flag(rest, "--by") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    before = tasks.get(tid)
    if not tasks.void(tid, reason=note, voided_by=by):
        return error_exit(f"❌ cannot void {tid} (missing or already 已取消)")
    task = tasks.get(tid) or {}
    assignee = str(task.get("assignee") or "")
    prev = before["status"] if before else "?"
    suffix = f": {note}" if note else ""
    local_facts.append_log(
        assignee, "task_transition",
        f"{tid} {prev}→已取消 (void){suffix}",
        ref=tid,
    )
    _refresh_anchor(assignee)
    print(f"🗑️  voided {tid} → 已取消")
    return 0


def _cmd_intent(rest: list[str]) -> int:
    if not rest:
        return usage_error(USAGE)
    action = rest[0]
    if action == "create":
        src = pop_flag(rest, "--src") or ""
        key = pop_flag(rest, "--key") or ""
        by = pop_flag(rest, "--by") or ""
        raw = " ".join(rest[1:])
        try:
            iid = tasks.create_intent(
                raw, source_msg=src, key_points=key, creator=by or "user")
        except ValueError as e:
            return error_exit(f"❌ {e}")
        print(f"✅ intent {iid}")
        return 0
    if action == "get":
        if len(rest) < 2:
            return usage_error(USAGE)
        intent = tasks.get_intent(rest[1])
        if intent is None:
            return error_exit(f"❌ no such intent: {rest[1]}")
        print(f"{intent['id']}  by {intent['creator']}")
        print(f"  raw: {intent['raw_text']}")
        if intent.get("key_points"):
            print(f"  key: {intent['key_points']}")
        return 0
    return usage_error(USAGE)


def _render_audit(payload: dict) -> list[str]:
    lines = [
        "✅ task audit passed"
        if payload.get("ok")
        else "❌ task audit failed"
    ]
    lines[0] += (
        f" team={payload.get('team') or '-'}"
        f" scanned={payload.get('scanned_tasks') or 0}"
        f" findings={payload.get('finding_count') or 0}"
        f" active_only={'yes' if payload.get('active_only') else 'no'}"
    )
    if payload.get("ok"):
        return lines
    for finding in payload.get("findings", []):
        task_id = str(finding.get("task_id") or "?")
        title = str(finding.get("title") or "").strip()
        label = f"{task_id} {title}".strip()
        lines.append(f"- {label}: {finding.get('message') or ''}".rstrip())
    return lines


def _cmd_audit(rest: list[str]) -> int:
    assignee = pop_flag(rest, "--assignee")
    topic = pop_flag(rest, "--topic")
    parent = pop_flag(rest, "--parent")
    include_all = pop_bool_flag(rest, "--all")
    as_json = pop_bool_flag(rest, "--json")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    payload = tasks.audit_tasks(
        assignee=assignee,
        topic=topic,
        parent_task_id=parent,
        active_only=not include_all,
    )
    if as_json:
        print_json(payload)
    else:
        for line in _render_audit(payload):
            print(line)
    return 0 if payload.get("ok") else 1


SUBCOMMANDS = {
    "create":  _cmd_create,
    "update":  _cmd_update,
    "done":    _cmd_done,
    "list":    _cmd_list,
    "get":     _cmd_get,
    "pause":   _cmd_pause,
    "approve": _cmd_approve,
    "reject":  _cmd_reject,
    "void":    _cmd_void,
    "intent":  _cmd_intent,
    "audit":   _cmd_audit,
}


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    if not argv:
        # No subcommand: print usage to stdout (it IS the requested output)
        # but return 1 so scripts know the call was incomplete.
        print(USAGE)
        return 1
    sub = argv[0]
    if sub not in SUBCOMMANDS:
        return error_exit(f"unknown task subcommand: {sub}\n{USAGE}")
    return SUBCOMMANDS[sub](list(argv[1:]))
