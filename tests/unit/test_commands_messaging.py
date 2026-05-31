"""Tests for `claudeteam send / inbox / read` commands.

Goes through run_cli([...]) so we exercise the dispatch + handler
contract end-to-end (without spawning a subprocess).
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from helpers import isolated_env, run_cli
from claudeteam.runtime import manager_action_guard, paths
from claudeteam.store import local_facts, memory, tasks


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


def test_send_writes_inbox_and_prints_local_id():
    with isolated_env():
        rc, out, err = run_cli(["send", "worker", "manager", "do task X"])
        assert rc == 0, err
        assert "inbox: worker ← manager" in out
        assert "local_id=msg_" in out
        assert "task_id=T-1" in out

        rows = local_facts.list_messages("worker")
        assert len(rows) == 1
        assert rows[0]["content"] == "do task X"
        assert rows[0]["from"] == "manager"
        assert rows[0]["task_id"] == "T-1"
        task = tasks.get("T-1")
        assert task is not None
        assert task["assignee"] == "worker"
        assert task["title"] == "do task X"


def test_send_touches_sender_heartbeat():
    with isolated_env():
        run_cli(["send", "worker", "manager", "do X"])
        assert local_facts.get_heartbeat("manager") is not None


def test_send_remembers_assignment_for_both_sides():
    with isolated_env():
        run_cli(["send", "worker", "manager", "do X"])
        worker_memory = memory.list_recent("worker", limit=5)
        manager_memory = memory.list_recent("manager", limit=5)
        assert any(r["kind"] == "task_assigned" and "[T-1] do X" in r["content"]
                   for r in worker_memory)
        assert any(r["kind"] == "task_assigned"
                   and "已派给 worker (T-1): do X" in r["content"]
                   for r in manager_memory)


def test_send_assignment_memory_truncates_and_redacts_large_content():
    with isolated_env():
        long_secret = (
            "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456' "
            + "x" * 1200
        )
        rc, out, err = run_cli(["send", "worker", "manager", long_secret])
        assert rc == 0, err
        assert "inbox: worker ← manager" in out
        row = memory.list_recent("worker", limit=1)[0]
        assert "Bearer [REDACTED]" in row["content"] or "Authorization: [REDACTED]" in row["content"]
        assert "abcdefghijklmnopqrstuvwxyz123456" not in row["content"]
        assert len(row["content"]) < 900
        assert "..." in row["content"]


def test_inbox_touches_agent_heartbeat():
    with isolated_env():
        run_cli(["inbox", "worker"])
        assert local_facts.get_heartbeat("worker") is not None


def test_send_priority_param_threads_through():
    with isolated_env():
        run_cli(["send", "a", "b", "msg", "高"])
        rows = local_facts.list_messages("a")
        assert rows[0]["priority"] == "高"


def test_send_rejects_unknown_recipient_when_team_has_agents():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team):
        rc, out, err = run_cli([
            "send", "WebsiteChuhai_manager", "manager", "cross-team fake",
        ])

        assert rc == 1
        assert out == ""
        assert "unknown local recipient: WebsiteChuhai_manager" in err
        assert "cross-send" in err
        assert tasks.list_tasks() == []
        assert local_facts.list_messages("WebsiteChuhai_manager") == []


def test_send_missing_args_returns_one_with_usage_to_stderr():
    rc, out, err = run_cli(["send", "only-one-arg"])
    assert rc == 1
    assert "usage: claudeteam send" in err


def test_send_no_inject_flag_skips_pane_inject_after_R168():
    """R168: `--no-inject` opts out of the new auto-inject behaviour
    so audit-only writes (caller is parking context for later, not
    expecting recipient to act NOW) stay silent. Inbox row still
    written; recipient won't be pinged."""
    with isolated_env():
        rc, out, _ = run_cli(["send", "worker", "manager", "x", "--no-inject"])
        assert rc == 0
        assert "inbox: worker ← manager" in out
        rows = local_facts.list_messages("worker")
        assert len(rows) == 1


def test_send_normalizes_visible_newlines_in_inbox():
    with isolated_env():
        rc, _, err = run_cli([
            "send", "worker", "manager", r"第一行\n第二行", "--no-inject"])
        assert rc == 0, err
        rows = local_facts.list_messages("worker")
        assert rows[0]["content"] == "第一行\n第二行"


def test_send_no_task_flag_skips_tracker_creation():
    with isolated_env():
        rc, out, err = run_cli(["send", "worker", "manager", "just ping", "--no-task"])
        assert rc == 0, err
        assert "task_id=" not in out
        rows = local_facts.list_messages("worker")
        assert rows[0]["task_id"] == ""
        assert tasks.list_tasks() == []


def test_send_can_bind_existing_task_id():
    with isolated_env():
        tid = tasks.create("worker", "existing", creator="manager")
        rc, out, err = run_cli(
            ["send", "worker", "manager", "follow up", "--task-id", tid])
        assert rc == 0, err
        assert f"task_id={tid}" in out
        rows = local_facts.list_messages("worker")
        assert rows[0]["task_id"] == tid
        assert len(tasks.list_tasks()) == 1


def test_worker_report_auto_binds_single_open_task():
    with isolated_env():
        tasks.create("worker_cc", "existing", creator="manager")
        rc, out, err = run_cli([
            "send", "manager", "worker_cc", "progress update"
        ])
        assert rc == 0, err
        assert "task_id=T-1" in out
        rows = local_facts.list_messages("manager")
        assert rows[0]["task_id"] == "T-1"


def test_worker_report_rejects_multiple_open_tasks_without_task_id():
    with isolated_env():
        tasks.create("worker_cc", "a", creator="manager")
        tasks.create("worker_cc", "b", creator="manager")
        rc, _, err = run_cli([
            "send", "manager", "worker_cc", "progress update"
        ])
        assert rc == 1
        assert "multiple open tasks" in err


def test_worker_done_requires_artifact_and_marks_waiting_review():
    with isolated_env() as tmp:
        tasks.create("worker_cc", "existing", creator="manager")
        _write_artifact(tmp, "artifacts/T-1/result.md")
        rc, out, err = run_cli([
            "send", "manager", "worker_cc", "fix ready",
            "--done", "--artifact", "artifacts/T-1/result.md",
        ])
        assert rc == 0, err
        assert "status=待验收" in out
        rows = local_facts.list_messages("manager")
        assert rows[0]["artifact"] == "artifacts/T-1/result.md"
        assert "Artifact: artifacts/T-1/result.md" in rows[0]["content"]
        assert tasks.get("T-1")["status"] == "待验收"
        assert tasks.get("T-1")["artifact_path"] == "artifacts/T-1/result.md"


def test_worker_can_handoff_manager_owned_delegated_task():
    with isolated_env() as tmp:
        tid = tasks.create("manager", "boss question", creator="user")
        _write_artifact(tmp, "artifacts/T-1/scout.md")
        run_cli([
            "send", "worker_scout", "manager", "please investigate",
            "--task-id", tid, "--no-inject",
        ])

        rc, out, err = run_cli([
            "send", "manager", "worker_scout", "scout result",
            "--task-id", tid,
            "--artifact", "artifacts/T-1/scout.md",
            "--done",
            "--no-inject",
        ])

        assert rc == 0, err
        assert "handoff=待主管汇总" in out
        assert "status=待验收" not in out
        rows = local_facts.list_messages("manager")
        assert rows[-1]["task_id"] == tid
        assert rows[-1]["artifact"] == "artifacts/T-1/scout.md"
        assert "Status: 员工已交付，待主管汇总" in rows[-1]["content"]
        task = tasks.get(tid)
        assert task["status"] == "进行中"
        assert task["artifact_path"] == ""


def test_worker_cannot_handoff_manager_task_without_delegation():
    with isolated_env():
        tid = tasks.create("manager", "boss question", creator="user")

        rc, _, err = run_cli([
            "send", "manager", "worker_scout", "scout result",
            "--task-id", tid,
            "--artifact", "artifacts/T-1/scout.md",
            "--done",
            "--no-inject",
        ])

        assert rc == 1
        assert f"task {tid} belongs to manager, not worker_scout" in err


def test_worker_done_rejects_missing_artifact():
    with isolated_env():
        tasks.create("worker_cc", "existing", creator="manager")
        rc, _, err = run_cli([
            "send", "manager", "worker_cc", "fix ready", "--done"
        ])
        assert rc == 1
        assert "must include --artifact" in err


def test_worker_done_rejects_nonexistent_artifact_file():
    with isolated_env():
        tasks.create("worker_cc", "existing", creator="manager")
        rc, _, err = run_cli([
            "send", "manager", "worker_cc", "fix ready",
            "--done", "--artifact", "artifacts/T-1/missing.md",
        ])
        assert rc == 1
        assert "missing artifact" in err


def test_worker_done_rejects_ui_task_without_screenshot_preview_bundle():
    with isolated_env() as tmp:
        tasks.create("worker_cc", "页面还原 /dashboard", creator="manager")
        _write_ui_report(
            tmp, "artifacts/T-1/report.md", preview=True, screenshot=False)

        rc, _, err = run_cli([
            "send", "manager", "worker_cc", "UI 还原完成",
            "--done", "--artifact", "artifacts/T-1/report.md",
        ])

        assert rc == 1
        assert "UI/page restoration" in err
        assert "screenshot image" in err
        assert tasks.get("T-1")["status"] == "待处理"


def test_worker_done_accepts_ui_task_with_screenshot_and_preview():
    with isolated_env() as tmp:
        tasks.create("worker_cc", "页面还原 /dashboard", creator="manager")
        _write_ui_report(tmp, "artifacts/T-1/report.md")

        rc, out, err = run_cli([
            "send", "manager", "worker_cc", "UI 还原完成",
            "--done", "--artifact", "artifacts/T-1/report.md",
        ])

        assert rc == 0, err
        assert "status=待验收" in out
        assert tasks.get("T-1")["status"] == "待验收"


def test_worker_done_does_not_reopen_completed_task():
    with isolated_env():
        tid = tasks.create(
            "worker_cc", "existing", creator="manager",
            artifact_path="artifacts/T-1/result.md")
        tasks.update(tid, status="已完成", _force=True)
        rc, _, err = run_cli([
            "send", "manager", "worker_cc", "late duplicate",
            "--task-id", tid, "--done",
        ])
        assert rc == 1
        assert "already 已完成" in err
        assert tasks.get(tid)["status"] == "已完成"


def test_send_default_inject_best_effort_when_no_tmux():
    """Without a live tmux session, the inject step is best-effort —
    `has_window` returns False (or the wrapper raises) and the command
    still returns 0 with the inbox row landed. No noisy stderr."""
    with isolated_env():
        rc, out, err = run_cli(["send", "worker", "manager", "x"])
        assert rc == 0
        assert "inbox: worker ← manager" in out
        rows = local_facts.list_messages("worker")
        assert len(rows) == 1


def test_send_skips_wake_for_non_lazy_agent():
    """Boss-flagged 2026-05-06: 给 manager 发消息不需要等他空闲, 直接
    inject 就行 (claude pane stash input buffer 自己处理). 只 lazy 员
    工才走 wake_if_dormant. 验证: 给一个 has_window=False 的 non-lazy
    agent 发消息时, send 既不调 wake.is_ready 也不调 wake_if_dormant."""
    from helpers import attr_patch
    from claudeteam.runtime import wake, tmux
    from claudeteam.commands import send as send_mod
    calls = {"is_ready": 0, "wake_if_dormant": 0}
    def fake_is_ready(*a, **kw):
        calls["is_ready"] += 1
        return True
    def fake_wake(*a, **kw):
        calls["wake_if_dormant"] += 1
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}):
        with attr_patch(wake, is_ready=fake_is_ready,
                        wake_if_dormant=fake_wake):
            with attr_patch(tmux, has_window=lambda *a, **kw: False):
                rc, _, _ = run_cli(["send", "manager", "boss", "hi"])
    assert rc == 0
    # has_window=False 提前 return 0 → wake 调用 0 次
    assert calls["is_ready"] == 0
    assert calls["wake_if_dormant"] == 0


def test_send_calls_wake_only_for_lazy_agent():
    """Lazy agent: pane 是 placeholder shell 还没 spawn CLI, 必须 wake_
    if_dormant 否则 inject 落到 shell 不是 CLI."""
    from helpers import attr_patch
    from claudeteam.runtime import wake, tmux, lifecycle
    calls = {"is_ready": 0, "wake_if_dormant": 0}
    def fake_is_ready(*a, **kw):
        calls["is_ready"] += 1
        return False  # not ready → triggers wake
    def fake_wake(*a, **kw):
        calls["wake_if_dormant"] += 1
    with isolated_env(team={"agents": {"worker_lazy": {
            "cli": "claude-code", "lazy": True}}}):
        with attr_patch(wake, is_ready=fake_is_ready,
                        wake_if_dormant=fake_wake):
            with attr_patch(tmux,
                            has_window=lambda *a, **kw: True,
                            inject=lambda *a, **kw: None):
                with attr_patch(lifecycle,
                                lazy_spawn_cmd=lambda agent: f"X=Y fake {agent}"):
                    rc, _, _ = run_cli(
                        ["send", "worker_lazy", "manager", "hi"])
    assert rc == 0
    assert calls["is_ready"] == 1
    assert calls["wake_if_dormant"] == 1


def test_send_lazy_codex_bootstraps_project_codex_home_before_wake():
    from helpers import attr_patch
    from claudeteam.runtime import wake, tmux

    team = {"agents": {"worker_codex": {
        "cli": "codex-cli",
        "model": "gpt-5.5",
        "provider_preset": "flux-codex-dev",
        "lazy": True,
    }}}
    wake_calls: list[str] = []

    def fake_wake(*_a, spawn_cmd=None, **_kw):
        wake_calls.append(spawn_cmd or "")
        return True

    with isolated_env(team=team) as tmp:
        state = tmp / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "provider-presets.json").write_text(
            json.dumps({
                "presets": {
                    "flux-codex-dev": {
                        "ANTHROPIC_BASE_URL": "https://api.fluxincode.com/v1",
                        "ANTHROPIC_AUTH_TOKEN": "sk-flux-123",
                    }
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        with attr_patch(wake, is_ready=lambda *a, **kw: False,
                        wake_if_dormant=fake_wake):
            with attr_patch(tmux,
                            has_window=lambda *a, **kw: True,
                            inject=lambda *a, **kw: None):
                rc, _, _ = run_cli(
                    ["send", "worker_codex", "manager", "hi"])
        wake_script = Path(shlex.split(wake_calls[0])[1]).read_text(encoding="utf-8")
        auth = json.loads(paths.codex_auth_file("worker_codex").read_text(encoding="utf-8"))
        cfg = paths.codex_config_file("worker_codex").read_text(encoding="utf-8")
    assert rc == 0
    assert wake_calls
    assert wake_calls[0].startswith("bash ")
    assert f"cd {shlex.quote(str(tmp))} && " in wake_script
    assert "CODEX_HOME=" in wake_script
    assert auth == {"OPENAI_API_KEY": "sk-flux-123"}
    assert 'model = "gpt-5.5"' in cfg


def test_inbox_lists_unread_with_local_id_and_returns_zero():
    with isolated_env():
        run_cli(["send", "w", "m", "first"])
        run_cli(["send", "w", "m", "second"])
        rc, out, _ = run_cli(["inbox", "w"])
        assert rc == 0
        assert "📬 w: 2 unread" in out
        assert "T-1" in out and "T-2" in out
        assert "first" in out and "second" in out


def test_inbox_empty_prints_no_unread():
    with isolated_env():
        rc, out, _ = run_cli(["inbox", "nobody"])
        assert rc == 0
        assert "📭 nobody: no unread messages" in out


def test_read_marks_then_inbox_drops_it():
    with isolated_env():
        run_cli(["send", "w", "m", "task A"])
        msgs = local_facts.list_messages("w")
        local_id = msgs[0]["local_id"]

        rc, out, _ = run_cli(["read", local_id])
        assert rc == 0
        assert "marked read" in out

        rc, out, _ = run_cli(["inbox", "w"])
        assert rc == 0
        assert "📭 w: no unread messages" in out


def test_read_boss_message_arms_manager_action_guard():
    with isolated_env(team={"agents": {"manager": {}, "worker_visual": {}}}):
        local_id = local_facts.append_message(
            "manager", "user", "把第 4 张图重做得更正式")

        rc, out, err = run_cli(["read", local_id])
        records = manager_action_guard.list_records()

    assert rc == 0, err
    assert "marked read" in out
    assert len(records) == 1
    assert records[0]["local_id"] == local_id
    assert records[0]["route_hint"] == "visual"
    assert records[0]["closed_at"] is None


def test_manager_send_worker_closes_manager_action_guard():
    team = {"agents": {"manager": {}, "worker_visual": {}}}
    with isolated_env(team=team):
        boss_msg = local_facts.append_message(
            "manager", "user", "把第 4 张图重做得更正式")
        run_cli(["read", boss_msg])

        rc, out, err = run_cli([
            "send", "worker_visual", "manager",
            "请重做第 4 张图，3 分钟内给图片或 blocker",
            "--no-task", "--no-inject",
        ])
        records = manager_action_guard.list_records()

    assert rc == 0, err
    assert "inbox: worker_visual ← manager" in out
    assert records[0]["closure_kind"] == "delegate"
    assert records[0]["closed_by"] == "manager->worker_visual"


def test_read_remembers_agent_has_taken_over_task():
    with isolated_env():
        run_cli(["send", "worker", "manager", "task A"])
        local_id = local_facts.list_messages("worker")[0]["local_id"]
        rc, out, err = run_cli(["read", local_id])
        assert rc == 0, err
        assert "marked read" in out
        rows = memory.list_recent("worker", limit=10)
        assert any(r["kind"] == "note"
                   and "[T-1] 已接手来自 manager 的任务: task A" in r["content"]
                   for r in rows)
        assert tasks.get("T-1")["status"] == "进行中"
        status = local_facts.get_status("worker")
        assert status is not None
        assert status["status"] == "进行中"
        assert status["task"] == "T-1: task A"
        assert local_facts.get_heartbeat("worker") is not None


def test_read_takeover_memory_truncates_and_redacts_large_content():
    with isolated_env():
        long_secret = (
            "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456' "
            + "x" * 1000
        )
        local_id = local_facts.append_message("manager", "user", long_secret)
        rc, out, err = run_cli(["read", local_id])
        assert rc == 0, err
        assert "marked read" in out
        rows = memory.list_recent("manager", limit=1)
        assert rows
        content = rows[0]["content"]
        assert "Bearer [REDACTED]" in content or "Authorization: [REDACTED]" in content
        assert "abcdefghijklmnopqrstuvwxyz123456" not in content
        assert len(content) < 700
        assert "..." in content


def test_read_does_not_reopen_waiting_review_or_completed_task():
    with isolated_env():
        tid = tasks.create(
            "worker", "task A", creator="manager",
            artifact_path="artifacts/T-1/out.md")
        local_id = local_facts.append_message(
            "worker", "manager", "please check", task_id=tid)
        tasks.update(tid, status="待验收")
        rc, out, err = run_cli(["read", local_id])
        assert rc == 0, err
        assert "task T-1 -> 进行中" not in out
        assert tasks.get(tid)["status"] == "待验收"

        done_msg = local_facts.append_message(
            "worker", "manager", "late note", task_id=tid)
        tasks.update(tid, status="已完成", _force=True)
        rc, out, err = run_cli(["read", done_msg])
        assert rc == 0, err
        assert "task T-1 -> 进行中" not in out
        assert tasks.get(tid)["status"] == "已完成"


def test_read_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["read", "msg_does_not_exist"])
        assert rc == 1
        assert "no such message" in err
