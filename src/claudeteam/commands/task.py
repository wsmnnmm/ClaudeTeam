"""`claudeteam task <subcommand>`

  task create <assignee> <title> [--by <agent>] [--desc <text>] [--artifact <path>]
                                   [--topic <name>]
                                   [--stage idea|mvp|launch|scale]
                                   [--evidence <text>] [--evidence-action <text>]
                                   [--non-goal <text>]
  task update <id>       [--status S] [--assignee A] [--title T] [--desc D] [--artifact <path>] [--by <agent>]
                                   [--topic <name>]
                                   [--stage idea|mvp|launch|scale]
                                   [--evidence <text>] [--evidence-action <text>]
                                   [--non-goal <text>]
  task list              [--status S] [--assignee A] [--topic <name>] [--active]
  task get <id>
  task done <id>         [--artifact <path>] [--by <agent>]
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.commands import founder_os
from claudeteam.runtime import artifact_gate, paths
from claudeteam.store import tasks
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_flag, usage_error,
    pop_bool_flag,
)


USAGE = (
    "usage:\n"
    "  claudeteam task create <assignee> <title> [--by <agent>] [--desc <text>] [--artifact <path>]\n"
    "                     [--topic <name>] [--stage idea|mvp|launch|scale] [--evidence <text>]\n"
    "                     [--evidence-action <text>] [--non-goal <text>]\n"
    "  claudeteam task update <id>  [--status S] [--assignee A] [--title T] [--desc D] [--artifact <path>] [--by <agent>]\n"
    "                     [--topic <name>] [--stage idea|mvp|launch|scale] [--evidence <text>]\n"
    "                     [--evidence-action <text>] [--non-goal <text>]\n"
    "  claudeteam task list  [--status S] [--assignee A] [--topic <name>] [--active]\n"
    "  claudeteam task get <id>\n"
    "  claudeteam task done <id> [--artifact <path>] [--by <agent>]"
)


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
    if t.get("topic"):
        body.append(f"  topic: #{t['topic']}")
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
    if t.get("reviewed_by"):
        body.append(f"  reviewed_by: {t['reviewed_by']}")
    body.append(f"  created: {ts}")
    return [head] + body


def _cmd_create(rest: list[str]) -> int:
    by = pop_flag(rest, "--by") or ""
    desc = pop_flag(rest, "--desc") or ""
    artifact = pop_flag(rest, "--artifact") or ""
    topic = pop_flag(rest, "--topic") or ""
    stage_raw = pop_flag(rest, "--stage")
    evidence = pop_flag(rest, "--evidence") or ""
    evidence_action = pop_flag(rest, "--evidence-action") or ""
    non_goal = pop_flag(rest, "--non-goal") or ""
    if len(rest) < 2:
        return usage_error(USAGE)
    assignee = rest[0]
    title = " ".join(rest[1:])
    try:
        stage = _stage_from_cli(stage_raw)
        tid = tasks.create(
            assignee, title, description=desc, creator=by,
            topic=topic, artifact_path=artifact, founder_stage=stage,
            stage_exit_evidence=evidence, evidence_action=evidence_action,
            non_goal=non_goal)
    except ValueError as e:
        return error_exit(f"❌ {e}")
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
    reviewed_by = pop_flag(rest, "--by")
    stage_raw = pop_flag(rest, "--stage")
    evidence = pop_flag(rest, "--evidence")
    evidence_action = pop_flag(rest, "--evidence-action")
    non_goal = pop_flag(rest, "--non-goal")
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
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
                          artifact_path=artifact, reviewed_by=reviewed_by,
                          founder_stage=founder_stage,
                          stage_exit_evidence=evidence,
                          evidence_action=evidence_action,
                          non_goal=non_goal)
    except ValueError as e:
        return error_exit(f"❌ {e}")
    if not ok:
        return error_exit(f"❌ no such task: {tid}")
    print(f"✅ updated {tid}")
    return 0


def _cmd_done(rest: list[str]) -> int:
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    artifact = pop_flag(rest, "--artifact")
    reviewed_by = pop_flag(rest, "--by")
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
    active = pop_bool_flag(rest, "--active")
    rows = tasks.list_tasks(status=status, assignee=assignee, topic=topic)
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
    for line in _fmt_task(t):
        print(line)
    return 0


SUBCOMMANDS = {
    "create": _cmd_create,
    "update": _cmd_update,
    "done":   _cmd_done,
    "list":   _cmd_list,
    "get":    _cmd_get,
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
