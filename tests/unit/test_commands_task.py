"""Tests for `claudeteam task` subcommand dispatcher."""
from __future__ import annotations

import io

from helpers import isolated_env, run_cli
from claudeteam.store import tasks


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


# ── list / get ────────────────────────────────────────────────────


def test_task_list_empty():
    with isolated_env():
        rc, out, _ = run_cli(["task", "list"])
        assert rc == 0
        assert "no matching tasks" in out


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
            non_goal="no demo")
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
        assert "reviewed_by: manager" in out


def test_task_get_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "get", "T-99"])
        assert rc == 1
        assert "no such task" in err


# ── dispatcher ───────────────────────────────────────────────────


def test_task_no_args_prints_usage():
    rc, out, _ = run_cli(["task"])
    # treated as "show usage"; behaviour-wise rc==1 since no subcmd
    assert "usage:" in out
    assert rc == 1


def test_task_unknown_subcommand_returns_one():
    rc, _, err = run_cli(["task", "invent"])
    assert rc == 1
    assert "unknown task subcommand" in err
