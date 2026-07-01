"""Tests for `claudeteam task` subcommand dispatcher."""
from __future__ import annotations

import io
import json

from helpers import isolated_env, run_cli, attr_patch
from claudeteam.commands import task as task_cmd
from claudeteam.runtime import tmux as tmux_mod, wake as wake_mod
from claudeteam.runtime import pane_probe as pp_mod
from claudeteam.store import local_facts, tasks


def _write_artifact(tmp, rel: str) -> None:
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("evidence", encoding="utf-8")


def _write_ui_report(tmp, rel: str, *, preview: bool = True,
                     screenshot: bool = True) -> None:
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# UI evidence"]
    if preview:
        lines.append("Preview: http://localhost:5173/#/dashboard?visualPreview=1")
    if screenshot:
        (path.parent / "shot.png").write_bytes(b"fake image")
        lines.append("![screenshot](shot.png)")
    path.write_text("\n\n".join(lines), encoding="utf-8")


# ── create ────────────────────────────────────────────────────────


def test_task_create_minimal():
    with isolated_env():
        rc, out, _ = run_cli(["task", "create", "worker", "do task X"])
        assert rc == 0
        assert "created T-1" in out
        rows = tasks.list_tasks()
        assert rows[0]["title"] == "do task X"
        assert rows[0]["assignee"] == "worker"


def test_task_create_with_by_and_desc():
    with isolated_env():
        run_cli(["task", "create", "worker", "task name",
              "--by", "manager", "--desc", "root cause Y"])
        t = tasks.list_tasks()[0]
        assert t["creator"] == "manager"
        assert t["description"] == "root cause Y"


def test_task_create_with_artifact():
    with isolated_env():
        run_cli(["task", "create", "worker", "task name",
              "--artifact", "artifacts/T-1/out.md"])
        t = tasks.list_tasks()[0]
        assert t["artifact_path"] == "artifacts/T-1/out.md"


def test_task_create_with_topic():
    with isolated_env():
        rc, out, err = run_cli([
            "task", "create", "worker", "task name",
            "--topic", "#学测2025",
        ])
        assert rc == 0, err
        t = tasks.list_tasks()[0]
        assert t["topic"] == "学测2025"


def test_task_create_with_founder_os_fields():
    with isolated_env():
        rc, out, err = run_cli([
            "task", "create", "worker", "validate pain",
            "--stage", "MVP",
            "--evidence", "1 real user returns tomorrow",
            "--evidence-action", "observe the core workflow today",
            "--non-goal", "do not add settings",
        ])
        assert rc == 0, err
        t = tasks.list_tasks()[0]
        assert t["founder_stage"] == "mvp"
        assert t["stage_exit_evidence"] == "1 real user returns tomorrow"
        assert t["evidence_action"] == "observe the core workflow today"
        assert t["non_goal"] == "do not add settings"


def test_task_create_with_truth_surface_fields():
    with isolated_env():
        rc, out, err = run_cli([
            "task", "create", "worker", "修 Product Lab receipt 断点",
            "--issue-class", "local-business",
            "--segment", "receipt",
            "--next-window", "2026-06-03 08:45 CST",
            "--base-absorb-needed", "no",
        ])
        assert rc == 0, err
        t = tasks.list_tasks()[0]
        assert t["issue_class"] == "local-business"
        assert t["current_segment"] == "receipt"
        assert t["next_natural_window"] == "2026-06-03 08:45 CST"
        assert t["base_absorb_needed"] == "no"


def test_task_create_with_parent_task():
    with isolated_env():
        parent = tasks.create("manager", "parent battle")
        rc, out, err = run_cli([
            "task", "create", "worker", "child unit",
            "--parent", parent,
        ])
        assert rc == 0, err
        child = tasks.list_tasks()[-1]
        assert child["parent_task_id"] == parent


def test_task_create_rejects_unknown_founder_stage():
    with isolated_env():
        rc, _, err = run_cli([
            "task", "create", "worker", "validate pain", "--stage", "random"
        ])
        assert rc == 1
        assert "unknown founder stage" in err


def test_task_create_title_with_spaces():
    with isolated_env():
        run_cli(["task", "create", "worker", "fix", "the", "broken", "thing"])
        t = tasks.list_tasks()[0]
        assert t["title"] == "fix the broken thing"


def test_task_create_missing_args_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "create", "worker"])
        assert rc == 1
        assert "usage:" in err


# ── update ────────────────────────────────────────────────────────


def test_task_update_status():
    with isolated_env():
        tasks.create("w", "x")
        rc, out, _ = run_cli(["task", "update", "T-1", "--status", "进行中"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "进行中"


def test_task_update_invalid_status_returns_one():
    with isolated_env():
        tasks.create("w", "x")
        rc, _, err = run_cli(["task", "update", "T-1", "--status", "bogus"])
        assert rc == 1
        assert "invalid status" in err


def test_task_update_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "update", "T-99", "--status", "已完成"])
        assert rc == 1
        assert "no such task" in err


def test_task_update_can_reassign_and_retitle():
    with isolated_env():
        tasks.create("w1", "old")
        run_cli(["task", "update", "T-1", "--assignee", "w2", "--title", "new"])
        t = tasks.get("T-1")
        assert t["assignee"] == "w2"
        assert t["title"] == "new"


def test_task_update_can_add_artifact_and_reviewer():
    with isolated_env():
        tasks.create("w1", "old")
        rc, out, err = run_cli([
            "task", "update", "T-1",
            "--artifact", "artifacts/T-1/out.md",
            "--by", "manager",
        ])
        assert rc == 0, err
        t = tasks.get("T-1")
        assert t["artifact_path"] == "artifacts/T-1/out.md"
        assert t["reviewed_by"] == "manager"


def test_task_update_can_add_topic():
    with isolated_env():
        tasks.create("w1", "old")
        rc, out, err = run_cli([
            "task", "update", "T-1",
            "--topic", "TeamOps",
        ])
        assert rc == 0, err
        assert tasks.get("T-1")["topic"] == "TeamOps"


def test_task_update_can_add_founder_os_fields():
    with isolated_env():
        tasks.create("w1", "old")
        rc, out, err = run_cli([
            "task", "update", "T-1",
            "--stage", "launch",
            "--evidence", "weekly report runs without boss",
            "--evidence-action", "wire the next automated sync",
            "--non-goal", "do not redesign cockpit",
        ])
        assert rc == 0, err
        t = tasks.get("T-1")
        assert t["founder_stage"] == "launch"
        assert t["stage_exit_evidence"] == "weekly report runs without boss"
        assert t["evidence_action"] == "wire the next automated sync"
        assert t["non_goal"] == "do not redesign cockpit"


def test_task_update_can_add_truth_surface_fields():
    with isolated_env():
        tasks.create("w1", "old")
        rc, out, err = run_cli([
            "task", "update", "T-1",
            "--issue-class", "cross-team",
            "--segment", "boss_view",
            "--next-window", "明早 10:00 CST",
            "--base-absorb-needed", "yes",
        ])
        assert rc == 0, err
        t = tasks.get("T-1")
        assert t["issue_class"] == "cross-team"
        assert t["current_segment"] == "boss_view"
        assert t["next_natural_window"] == "明早 10:00 CST"
        assert t["base_absorb_needed"] == "yes"


def test_task_update_can_attach_parent_task():
    with isolated_env():
        parent = tasks.create("manager", "parent battle")
        tasks.create("w1", "old")
        rc, out, err = run_cli([
            "task", "update", "T-2",
            "--parent", parent,
        ])
        assert rc == 0, err
        assert tasks.get("T-2")["parent_task_id"] == parent


# ── done shortcut ────────────────────────────────────────────────


def test_task_done_marks_completed():
    with isolated_env() as tmp:
        tasks.create("w", "x")
        _write_artifact(tmp, "artifacts/T-1/out.md")
        rc, out, err = run_cli([
            "task", "done", "T-1",
            "--artifact", "artifacts/T-1/out.md",
            "--by", "manager",
        ])
        assert rc == 0, err
        t = tasks.get("T-1")
        assert t["status"] == "已完成"
        assert t["completed_at"] is not None
        assert t["artifact_path"] == "artifacts/T-1/out.md"
        assert t["reviewed_by"] == "manager"


def test_task_done_rejects_missing_artifact():
    with isolated_env():
        tasks.create("w", "x")
        rc, _, err = run_cli(["task", "done", "T-1"])
        assert rc == 1
        assert "cannot be marked 已完成 without an artifact" in err


def test_task_done_rejects_nonexistent_artifact_file():
    with isolated_env():
        tasks.create("w", "x")
        rc, _, err = run_cli([
            "task", "done", "T-1", "--artifact", "artifacts/T-1/missing.md"
        ])
        assert rc == 1
        assert "artifact does not exist" in err


def test_task_update_waiting_review_requires_existing_artifact():
    with isolated_env() as tmp:
        tasks.create("w", "x")
        rc, _, err = run_cli([
            "task", "update", "T-1", "--status", "待验收",
            "--artifact", "artifacts/T-1/out.md",
        ])
        assert rc == 1
        assert "artifact does not exist" in err

        _write_artifact(tmp, "artifacts/T-1/out.md")
        rc, out, err = run_cli([
            "task", "update", "T-1", "--status", "待验收",
            "--artifact", "artifacts/T-1/out.md",
        ])
        assert rc == 0, err
        assert tasks.get("T-1")["status"] == "待验收"


def test_task_done_rejects_ui_task_without_screenshot_preview_bundle():
    with isolated_env() as tmp:
        tasks.create("w", "页面还原 /dashboard")
        _write_ui_report(
            tmp, "artifacts/T-1/report.md", preview=False, screenshot=True)

        rc, _, err = run_cli([
            "task", "done", "T-1", "--artifact", "artifacts/T-1/report.md"
        ])

        assert rc == 1
        assert "UI/page restoration" in err
        assert "http(s) preview URL" in err
        assert tasks.get("T-1")["status"] == "待处理"


def test_task_done_accepts_ui_task_with_screenshot_and_preview():
    with isolated_env() as tmp:
        tasks.create("w", "页面还原 /dashboard")
        _write_ui_report(tmp, "artifacts/T-1/report.md")

        rc, out, err = run_cli([
            "task", "done", "T-1", "--artifact", "artifacts/T-1/report.md"
        ])

        assert rc == 0, err
        assert "updated T-1" in out
        assert tasks.get("T-1")["status"] == "已完成"
        assert tasks.get("T-1")["reviewed_by"] == "manager"


def test_task_done_uses_existing_artifact_when_present():
    with isolated_env() as tmp:
        _write_artifact(tmp, "artifacts/T-1/out.md")
        tasks.create("w", "x", artifact_path="artifacts/T-1/out.md")
        rc, out, err = run_cli(["task", "done", "T-1"])
        assert rc == 0, err
        t = tasks.get("T-1")
        assert t["status"] == "已完成"
        assert t["completed_at"] is not None
        assert t["artifact_path"] == "artifacts/T-1/out.md"
        assert t["reviewed_by"] == "manager"


def test_task_update_complete_requires_explicit_reviewer():
    with isolated_env() as tmp:
        tasks.create("w", "x")
        _write_artifact(tmp, "artifacts/T-1/out.md")
        rc, _, err = run_cli([
            "task", "update", "T-1",
            "--status", "已完成",
            "--artifact", "artifacts/T-1/out.md",
        ])
        assert rc == 1
        assert "requires reviewed_by" in err


def test_task_done_rejects_parent_with_open_child_tasks():
    with isolated_env() as tmp:
        parent = tasks.create("manager", "parent")
        tasks.create("worker", "child", parent_task_id=parent)
        _write_artifact(tmp, "artifacts/T-1/out.md")
        rc, _, err = run_cli([
            "task", "done", parent,
            "--artifact", "artifacts/T-1/out.md",
        ])
        assert rc == 1
        assert "open child tasks" in err


# ── list / get ────────────────────────────────────────────────────


def test_task_list_shows_count_and_each_row():
    with isolated_env():
        tasks.create("w", "first task")
        tasks.create("w", "second task")
        rc, out, _ = run_cli(["task", "list"])
        assert rc == 0
        assert "2 tasks" in out
        assert "first task" in out and "second task" in out


def test_task_list_filter_by_status_and_assignee():
    with isolated_env():
        tasks.create("alice", "a-task")
        tasks.create("bob", "b-task")
        tasks.create("alice", "a-done")
        tasks.update("T-3", status="已完成", artifact_path="artifacts/test.md", _force=True)

        rc, out, _ = run_cli(["task", "list", "--assignee", "alice"])
        assert rc == 0
        assert "a-task" in out and "a-done" in out
        assert "b-task" not in out

        rc, out, _ = run_cli(["task", "list", "--status", "已完成"])
        assert rc == 0
        assert "a-done" in out
        assert "a-task" not in out


def test_task_list_filter_by_topic():
    with isolated_env():
        tasks.create("alice", "a-task", topic="TeamOps")
        tasks.create("bob", "b-task", topic="学测2025")

        rc, out, _ = run_cli(["task", "list", "--topic", "teamops"])
        assert rc == 0
        assert "a-task" in out
        assert "b-task" not in out
        assert "topic: #TeamOps" in out


def test_task_list_filter_by_parent():
    with isolated_env():
        parent = tasks.create("manager", "parent")
        tasks.create("alice", "child-a", parent_task_id=parent)
        tasks.create("alice", "child-b", parent_task_id=parent)
        tasks.create("alice", "standalone")

        rc, out, _ = run_cli(["task", "list", "--parent", parent])
        assert rc == 0
        assert "child-a" in out
        assert "child-b" in out
        assert "standalone" not in out


def test_task_list_active_excludes_terminal_tasks():
    """Agents use --active in prompts so old completed/cancelled backlog
    does not bloat every inbox-processing turn."""
    with isolated_env():
        tasks.create("alice", "new task")
        tasks.create("alice", "in progress")
        tasks.create("alice", "done task", artifact_path="artifacts/T-3/out.md")
        tasks.create("alice", "cancelled task")
        tasks.update("T-2", status="进行中")
        tasks.update("T-3", status="已完成", _force=True)
        tasks.update("T-4", status="已取消")

        rc, out, _ = run_cli(["task", "list", "--assignee", "alice", "--active"])
        assert rc == 0
        assert "new task" in out
        assert "in progress" in out
        assert "done task" not in out
        assert "cancelled task" not in out


def test_task_get_existing_renders_full_card():
    with isolated_env():
        tasks.create(
            "w", "task one", description="d",
            artifact_path="artifacts/T-1/out.md",
            founder_stage="idea",
            stage_exit_evidence="human pain evidence",
            evidence_action="talk to one buyer",
            non_goal="no demo",
            issue_class="base-common",
            current_segment="sync",
            next_natural_window="2026-06-03 18:00 CST",
            base_absorb_needed="yes")
        tasks.update("T-1", reviewed_by="manager")
        rc, out, _ = run_cli(["task", "get", "T-1"])
        assert rc == 0
        assert "T-1" in out and "task one" in out
        assert "desc: d" in out
        assert "artifact: artifacts/T-1/out.md" in out
        assert "stage: idea" in out
        assert "evidence: human pain evidence" in out
        assert "evidence_action: talk to one buyer" in out
        assert "non_goal: no demo" in out
        assert "issue_class: base-common" in out
        assert "current_segment: sync" in out
        assert "next_natural_window: 2026-06-03 18:00 CST" in out
        assert "base_absorb_needed: yes" in out
        assert "reviewed_by: manager" in out


def test_task_get_shows_parent_and_child_rollup():
    with isolated_env():
        parent = tasks.create("manager", "parent battle")
        tasks.create("worker", "child one", parent_task_id=parent)
        tasks.create("worker", "child two", parent_task_id=parent)
        rc, out, _ = run_cli(["task", "get", parent])
        assert rc == 0
        assert "child_tasks: 2 total / 2 open" in out


def test_task_get_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "get", "T-99"])
        assert rc == 1
        assert "no such task" in err


def test_task_audit_reports_missing_truth_surface_fields():
    with isolated_env():
        tasks.create("worker", "broken active task")
        rc, out, err = run_cli(["task", "audit"])
        assert rc == 1
        assert err == ""
        assert "task audit failed" in out
        assert "missing truth-surface fields" in out
        assert "T-1" in out


def test_task_audit_json_dumps_machine_readable_findings():
    with isolated_env():
        parent = tasks.create(
            "manager", "parent battle",
            issue_class="local-business",
            current_segment="receipt",
            next_natural_window="2026-06-03 08:45 CST",
            base_absorb_needed="no")
        tasks.create(
            "worker", "child unit",
            parent_task_id=parent,
            issue_class="base-common",
            current_segment="artifact",
            next_natural_window="2026-06-03 08:46 CST",
            base_absorb_needed="no")
        rc, out, err = run_cli(["task", "audit", "--json"])
        assert rc == 1
        assert err == ""
        data = json.loads(out)
        assert data["ok"] is False
        assert any(
            row["finding_code"] == "parent_issue_class_mismatch"
            for row in data["findings"]
        )


def test_task_audit_passes_when_active_tasks_are_aligned():
    with isolated_env():
        parent = tasks.create(
            "manager", "parent battle",
            issue_class="local-business",
            current_segment="receipt",
            next_natural_window="2026-06-03 08:45 CST",
            base_absorb_needed="no")
        tasks.create(
            "worker", "child unit",
            parent_task_id=parent,
            issue_class="local-business",
            current_segment="artifact",
            next_natural_window="2026-06-03 08:46 CST",
            base_absorb_needed="no")
        rc, out, err = run_cli(["task", "audit"])
        assert rc == 0, err
        assert "task audit passed" in out
        assert "findings=0" in out


# ── dispatcher ───────────────────────────────────────────────────


def test_task_unknown_subcommand_returns_one():
    rc, _, err = run_cli(["task", "invent"])
    assert rc == 1
    assert "unknown task subcommand" in err


# ── intent ────────────────────────────────────────────────────────


def test_task_intent_create_and_get():
    with isolated_env():
        rc, out, _ = run_cli(["task", "intent", "create", "把首页改深色",
                              "--src", "msg_9"])
        assert rc == 0 and "I-1" in out
        rc, out, _ = run_cli(["task", "intent", "get", "I-1"])
        assert rc == 0
        assert "把首页改深色" in out


def test_task_create_with_intent_backlink():
    with isolated_env():
        tasks.create_intent("原话")            # I-1
        run_cli(["task", "create", "w", "子任务", "--intent", "I-1"])
        assert tasks.get("T-1")["intent_id"] == "I-1"


def test_task_intent_get_unknown_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "intent", "get", "I-99"])
        assert rc == 1
        assert "no such intent" in err


# ── pause / approve / reject ──────────────────────────────────────


def _make_in_progress(assignee="w"):
    tasks.create(assignee, "t")
    tasks.update("T-1", status="进行中")


def test_task_pause_suspends_and_routes_to_approver():
    with isolated_env():
        _make_in_progress()
        rc, out, _ = run_cli(["task", "pause", "T-1", "--note", "要拍板",
                              "--by", "w"])
        assert rc == 0 and "需审批" in out
        assert tasks.get("T-1")["status"] == "需审批"
        # an approval-request message lands in the boss inbox, tagged task_id
        msgs = local_facts.list_messages("user")
        assert any(m["task_id"] == "T-1" for m in msgs)


def test_task_pause_non_in_progress_returns_one():
    with isolated_env():
        tasks.create("w", "t")                # 待处理
        rc, _, err = run_cli(["task", "pause", "T-1"])
        assert rc == 1
        assert "cannot pause" in err


def test_task_approve_continue():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, out, _ = run_cli(["task", "approve", "T-1"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "进行中"
        # decision echoed back to the assignee inbox
        assert any(m["task_id"] == "T-1" for m in local_facts.list_messages("w"))


def test_task_approve_done():
    with isolated_env() as tmp:
        _make_in_progress()
        _write_artifact(tmp, "artifacts/T-1/out.md")
        run_cli(["task", "pause", "T-1"])
        rc, _, _ = run_cli([
            "task", "approve", "T-1", "--done",
            "--artifact", "artifacts/T-1/out.md",
        ])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "已完成"


def test_task_approve_note_reaches_receipt_and_audit():
    """The verdict must ride the gated channel — assignee receipt and
    audit row both carry `--note`, and the task's approval_note holds it
    for the anchor to surface."""
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1", "--note", "第三行写什么？"])
        rc, _, _ = run_cli(["task", "approve", "T-1",
                            "--note", "写鱼香肉丝"])
        assert rc == 0
        assert tasks.get("T-1")["approval_note"] == "写鱼香肉丝"
        receipts = [m for m in local_facts.list_messages("w")
                    if m["task_id"] == "T-1"]
        assert any("写鱼香肉丝" in m["content"] for m in receipts)
        logs = local_facts.list_logs("w")
        assert any("写鱼香肉丝" in r["content"] for r in logs)


def test_task_approve_non_suspended_returns_one():
    with isolated_env():
        _make_in_progress()
        rc, _, err = run_cli(["task", "approve", "T-1"])
        assert rc == 1
        assert "cannot approve" in err


def test_task_reject_rework_with_feedback():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, _ = run_cli(["task", "reject", "T-1", "方向", "错了"])
        assert rc == 0
        t = tasks.get("T-1")
        assert t["status"] == "进行中"
        assert t["approval_note"] == "方向 错了"


def test_task_reject_cancel():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, _ = run_cli(["task", "reject", "T-1", "不做了", "--cancel"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "已取消"


def test_task_update_cannot_bypass_gate_via_cli():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, err = run_cli(["task", "update", "T-1", "--status", "已完成"])
        assert rc == 1
        assert "需审批" in err
        assert tasks.get("T-1")["status"] == "需审批"


def test_task_transition_writes_audit_log():
    with isolated_env() as tmp:
        _make_in_progress()
        _write_artifact(tmp, "artifacts/T-1/out.md")
        run_cli(["task", "pause", "T-1"])
        run_cli([
            "task", "approve", "T-1", "--done",
            "--artifact", "artifacts/T-1/out.md",
        ])
        logs = local_facts.list_logs("w")
        kinds = [(l["type"], l["ref"]) for l in logs]
        assert ("task_transition", "T-1") in kinds


def test_task_reject_non_suspended_returns_one():
    with isolated_env():
        _make_in_progress()                       # 进行中, not 需审批
        rc, _, err = run_cli(["task", "reject", "T-1", "无效打回"])
        assert rc == 1
        assert "cannot reject" in err
        assert tasks.get("T-1")["status"] == "进行中"


def test_task_reject_rework_echoes_to_assignee_and_logs():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        run_cli(["task", "reject", "T-1", "方向错了"])
        # decision echoes back to the assignee inbox, tagged with task_id
        assert any(m["task_id"] == "T-1"
                   for m in local_facts.list_messages("w"))
        # and the transition is audited
        kinds = [(l["type"], l["ref"]) for l in local_facts.list_logs("w")]
        assert ("task_transition", "T-1") in kinds


def test_task_pause_routes_to_explicit_approver_via_to():
    """`--to manager` sends the approval request to that inbox (not boss),
    and records awaiting=manager on the task."""
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1", "--note", "拍板", "--to", "manager"])
        assert tasks.get("T-1")["awaiting"] == "manager"
        assert any(m["task_id"] == "T-1"
                   for m in local_facts.list_messages("manager"))
        # boss inbox should NOT receive it when routed elsewhere
        assert not any(m["task_id"] == "T-1"
                       for m in local_facts.list_messages("user"))


def test_task_intent_create_empty_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "intent", "create", "   "])
        assert rc == 1
        assert "empty" in err


# ── gate: no CLI path may bypass the 需审批 suspend ────────────────


def test_task_done_shortcut_cannot_bypass_gate():
    """`task done` is sugar for `update --status 已完成`; on a suspended task
    it must hit the same gate and refuse."""
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, err = run_cli(["task", "done", "T-1"])
        assert rc == 1
        assert "需审批" in err
        assert tasks.get("T-1")["status"] == "需审批"


def test_task_update_cannot_force_into_suspend_via_cli():
    with isolated_env():
        _make_in_progress()                       # 进行中
        rc, _, err = run_cli(["task", "update", "T-1", "--status", "需审批"])
        assert rc == 1
        assert "需审批" in err
        assert tasks.get("T-1")["status"] == "进行中"


# ── reidentify fallback (G) ───────────────────────────────────────
# _refresh_anchor calls _reidentify_stale_anchor for every affected
# assignee. For CLIs that re-read their native file mid-session
# (claude/gemini) it's a no-op; for the rest (codex/qwen/kimi) it
# pushes init_prompt into an *idle* pane so the live intent anchor
# reaches an agent that won't re-read disk. Everything is best-effort:
# no failure mode may bubble up and fail the triggering task command.


def _capture_injects():
    """Return (sink, fake_inject) recording every tmux.inject call."""
    sink: list[dict] = []

    def fake_inject(target, text, *, submit_keys=None):
        sink.append({"target": target, "text": text, "submit_keys": submit_keys})

    return sink, fake_inject


def test_reidentify_stale_anchor_skips_reloading_cli():
    """claude-code re-reads its native file after /compact, so the disk
    rewrite already reaches it — no pane inject."""
    with isolated_env(team={"agents": {"worker_cc": {"cli": "claude-code"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True), \
             attr_patch(wake_mod, is_ready=lambda t, a: True):
            task_cmd._reidentify_stale_anchor("worker_cc")
        assert sink == []


def test_reidentify_stale_anchor_injects_into_idle_non_reloading_pane():
    """codex does NOT re-read mid-session → an idle ready pane gets the
    init prompt re-injected (carrying the live anchor inline)."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True), \
             attr_patch(pp_mod, probe=lambda t: pp_mod.IDLE):
            task_cmd._reidentify_stale_anchor("worker_codex")
        assert len(sink) == 1
        assert sink[0]["target"].window == "worker_codex"
        assert sink[0]["text"]            # non-empty init prompt
        assert sink[0]["submit_keys"]     # codex submit keys threaded through


def test_reidentify_stale_anchor_skips_busy_pane():
    """A busy pane (mid-turn) must never be derailed — idle gate refuses."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True), \
             attr_patch(pp_mod, probe=lambda t: pp_mod.BUSY):
            task_cmd._reidentify_stale_anchor("worker_codex")
        assert sink == []


def test_reidentify_stale_anchor_skips_when_not_ready():
    """Dormant / dead pane (probe != IDLE) → nothing to inject into."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True), \
             attr_patch(pp_mod, probe=lambda t: pp_mod.DEAD):
            task_cmd._reidentify_stale_anchor("worker_codex")
        assert sink == []


def test_reidentify_stale_anchor_best_effort_when_no_session():
    """No tmux session yet (agents not started) → silent no-op, no raise."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: False,
                        has_window=lambda t: True), \
             attr_patch(wake_mod, is_ready=lambda t, a: True):
            task_cmd._reidentify_stale_anchor("worker_codex")
        assert sink == []


def test_reidentify_stale_anchor_swallows_unknown_agent():
    """A ghost agent (not in team.json) makes adapter_for_agent raise
    KeyError; best-effort contract swallows it so the task command that
    triggered the refresh still succeeds."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject):
            task_cmd._reidentify_stale_anchor("ghost")   # must not raise
        assert sink == []


# ── intent --by attribution ───────────────────────────────────────


def test_task_intent_create_by_attributes_creator():
    """--by stamps the real author; without it the intent stays 'user'
    (the boss-verbatim default)."""
    with isolated_env():
        run_cli(["task", "intent", "create", "manager 代记的派生需求",
                 "--by", "manager"])
        assert tasks.get_intent("I-1")["creator"] == "manager"
        run_cli(["task", "intent", "create", "老板逐字原话"])
        assert tasks.get_intent("I-2")["creator"] == "user"


# ── void ──────────────────────────────────────────────────────────


def test_task_void_retires_completed_task():
    with isolated_env() as tmp:
        run_cli(["task", "create", "w", "重复活"])
        _write_artifact(tmp, "artifacts/T-1/out.md")
        run_cli(["task", "done", "T-1", "--artifact", "artifacts/T-1/out.md"])
        rc, out, _ = run_cli(["task", "void", "T-1", "--note", "重复，作废",
                              "--by", "manager"])
        assert rc == 0 and "voided T-1" in out
        t = tasks.get("T-1")
        assert t["status"] == "已取消"
        assert t["approval_note"] == "重复，作废"


def test_task_void_unknown_or_already_cancelled_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "void", "T-404"])
        assert rc == 1 and "cannot void" in err
        run_cli(["task", "create", "w", "x"])
        run_cli(["task", "void", "T-1"])
        rc, _, err = run_cli(["task", "void", "T-1"])   # already 已取消
        assert rc == 1 and "cannot void" in err


def test_task_void_writes_audit_log():
    with isolated_env() as tmp:
        run_cli(["task", "create", "w", "x"])
        _write_artifact(tmp, "artifacts/T-1/out.md")
        run_cli(["task", "done", "T-1", "--artifact", "artifacts/T-1/out.md"])
        run_cli(["task", "void", "T-1", "--note", "作废"])
        kinds = [(l["type"], l["ref"]) for l in local_facts.list_logs("w")]
        assert ("task_transition", "T-1") in kinds


# ── auto-memory on task lifecycle ──────────────────────────────────


def test_create_auto_records_task_assigned_to_assignee():
    from claudeteam.store import memory
    with isolated_env():
        run_cli(["task", "intent", "create", "老板原话"])          # I-1
        run_cli(["task", "create", "worker_cc", "做 X", "--by", "manager", "--intent", "I-1"])
        rows = memory.list_recent("worker_cc")
    assert len(rows) == 1
    assert rows[0]["kind"] == "task_assigned"
    assert "T-1" in rows[0]["content"] and "I-1" in rows[0]["content"]
    assert "做 X" not in rows[0]["content"]
    assert rows[0]["ref"] == "T-1"


def test_done_auto_records_task_completed_once():
    from claudeteam.store import memory
    with isolated_env() as tmp:
        run_cli(["task", "create", "worker_cc", "活"])
        _write_artifact(tmp, "artifacts/T-1/out.md")
        run_cli(["task", "done", "T-1", "--artifact", "artifacts/T-1/out.md"])
        run_cli(["task", "done", "T-1", "--artifact", "artifacts/T-1/out.md"])  # idempotent re-assert
        rows = memory.list_recent("worker_cc")
    completed = [r for r in rows if r["kind"] == "task_completed"]
    assert len(completed) == 1               # NOT double-recorded
    assert "已完成" in completed[0]["content"] and completed[0]["ref"] == "T-1"


def test_pause_auto_records_blocker():
    from claudeteam.store import memory
    with isolated_env():
        run_cli(["task", "create", "worker_cc", "活"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        run_cli(["task", "pause", "T-1", "--note", "等老板拍板", "--to", "user"])
        rows = memory.list_recent("worker_cc")
    blockers = [r for r in rows if r["kind"] == "blocker"]
    assert len(blockers) == 1
    assert "需审批" in blockers[0]["content"] and "等老板拍板" in blockers[0]["content"]


def test_approve_done_auto_records_task_completed():
    from claudeteam.store import memory
    with isolated_env() as tmp:
        run_cli(["task", "create", "worker_cc", "活"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        _write_artifact(tmp, "artifacts/T-1/out.md")
        run_cli(["task", "pause", "T-1", "--note", "q"])
        run_cli([
            "task", "approve", "T-1", "--done", "--note", "ok",
            "--artifact", "artifacts/T-1/out.md",
        ])
        rows = memory.list_recent("worker_cc")
    assert any(r["kind"] == "task_completed" and r["ref"] == "T-1" for r in rows)


def test_auto_memory_is_best_effort_never_fails_command():
    """A memory write blowing up must not fail the task command — _auto_memory
    swallows. Force memory.append to raise and verify create still rc==0."""
    from claudeteam.store import memory as memory_mod

    def boom(*a, **k):
        raise RuntimeError("disk full")
    with isolated_env():
        with attr_patch(memory_mod, append=boom):
            rc, out, _ = run_cli(["task", "create", "worker_cc", "活"])
        assert rc == 0 and "created T-1" in out
        assert tasks.get("T-1") is not None        # task itself persisted
