"""Tests for runtime/manager_watch.py — manager dispatch overdue backstop."""
from __future__ import annotations

import json

from helpers import attr_patch, isolated_env
from claudeteam.runtime import (
    first_output_gate, manager_action_guard, manager_watch, paths,
)
from claudeteam.store import local_facts, tasks
from claudeteam.util import now_ms, write_json


def _team():
    return {
        "session": "S",
        "agents": {
            "manager": {"cli": "claude-code"},
            "worker_scout": {"cli": "codex-cli"},
        },
    }


def _age_task_to_epoch(assignee: str = "worker_scout") -> str:
    tid = tasks.create(assignee, "核对禅道 bug", creator="manager")
    path = paths.state_dir() / "tasks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tasks"][0]["created_at"] = 0
    data["tasks"][0]["updated_at"] = 0
    write_json(path, data)
    return tid


def test_sweep_alerts_manager_for_stale_worker_task():
    injected = []
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        notices = manager_watch.sweep(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "not at ready prompt",
            inject_manager_fn=lambda body: injected.append(body),
            overdue_s=300,
            repeat_s=900,
            public_overdue_s=300,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(notices) == 1
    assert notices[0].task_id == tid
    assert "worker_scout" in notices[0].body
    assert "bin/ct peek worker_scout 100" in notices[0].body
    assert "bin/ct peek" not in notices[0].public_body
    assert "worker_scout" not in notices[0].public_body
    assert "artifact" not in notices[0].public_body
    assert "老板动作：先不用处理内部命令" in notices[0].public_body
    assert manager_inbox[0]["from"] == "manager_watch"
    assert manager_inbox[0]["task_id"] == tid
    assert injected and tid in injected[0]
    assert "固定三选一动作" in notices[0].body


def test_sweep_suppresses_duplicate_alert_until_repeat_window():
    with isolated_env(team=_team()):
        _age_task_to_epoch()
        first = manager_watch.sweep(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=900,
        )
        second = manager_watch.sweep(
            now_ms_fn=lambda: 500_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=900,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(first) == 1
    assert second == []
    assert len(manager_inbox) == 1


def test_sweep_alerts_manager_for_waiting_review_task_privately():
    alerts = []
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        tasks.update(
            tid,
            status="待验收",
            artifact_path="artifacts/T-1/report.md",
        )
        path = paths.state_dir() / "tasks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tasks"][0]["updated_at"] = 0
        write_json(path, data)
        notices = manager_watch.sweep(
            now_ms_fn=lambda: 901_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            alert_fn=lambda notice: alerts.append(notice),
            overdue_s=300,
            repeat_s=900,
            public_overdue_s=300,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(notices) == 1
    assert notices[0].task_id == tid
    assert "待验收超时" in notices[0].title
    assert "待验收" in notices[0].body
    assert "验收通过" in notices[0].body
    assert notices[0].public_body == ""
    assert alerts == [notices[0]]
    assert len(manager_inbox) == 1
    assert manager_inbox[0]["task_id"] == tid


def test_sweep_alerts_manager_for_manager_owned_waiting_review_task():
    alerts = []
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch("manager")
        tasks.update(
            tid,
            status="待验收",
            artifact_path="artifacts/T-1/report.md",
        )
        path = paths.state_dir() / "tasks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tasks"][0]["updated_at"] = 0
        write_json(path, data)
        notices = manager_watch.sweep(
            now_ms_fn=lambda: 901_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            alert_fn=lambda notice: alerts.append(notice),
            overdue_s=300,
            repeat_s=900,
            public_overdue_s=300,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(notices) == 1
    assert notices[0].task_id == tid
    assert "待验收超时" in notices[0].title
    assert "主管验收" in notices[0].body
    assert notices[0].public_body == ""
    assert alerts == [notices[0]]
    assert len(manager_inbox) == 1
    assert manager_inbox[0]["task_id"] == tid


def test_worker_heartbeat_alone_does_not_prevent_overdue_alert():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_facts.touch_heartbeat("worker_scout")
        recent = now_ms()
        notices = manager_watch.sweep(
            now_ms_fn=lambda: recent + 1_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=900,
            max_task_age_s=0,
        )

    assert len(notices) == 1
    assert notices[0].task_id == tid


def test_recent_worker_report_prevents_overdue_alert():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_facts.append_message(
            "manager", "worker_scout", "已补真实截图，待复核",
            task_id=tid)
        recent = now_ms()
        notices = manager_watch.sweep(
            now_ms_fn=lambda: recent + 1_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=900,
        )

    assert notices == []


def test_recent_worker_read_prevents_overdue_alert_temporarily():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_id = local_facts.append_message(
            "worker_scout", "manager", "请处理 T-1", task_id=tid)
        assert local_facts.mark_read(local_id)
        read_at = local_facts.get_message(local_id)["read_at"]
        notices = manager_watch.sweep(
            now_ms_fn=lambda: read_at + 1_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=900,
        )

    assert notices == []


def test_sweep_first_output_alerts_even_after_worker_read():
    injected = []
    alerts = []
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_id = local_facts.append_message(
            "worker_scout", "manager", "请处理 T-1", task_id=tid)
        assert local_facts.mark_read(local_id)
        notices = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: injected.append(body),
            alert_fn=lambda notice: alerts.append(notice),
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(notices) == 1
    notice = notices[0]
    assert notice.task_id == tid
    assert "派工后无首产物" in notice.title
    assert "可验证首产物/真实 blocker" in notice.body
    assert "当前判定：无证据" in notice.body
    assert "3 分钟内回首产物或 blocker" in notice.body
    assert "还没有形成可验证首产物或真实 blocker" in notice.public_body
    assert "固定三选一动作" in notice.body
    assert "first_output_feedback" in notice.body
    assert alerts == [notice]
    assert injected and tid in injected[0]
    assert any(m["from"] == "manager_watch" and m["task_id"] == tid
               for m in manager_inbox)


def test_standby_closeout_task_does_not_trigger_watchers():
    with isolated_env(team=_team()):
        tid = tasks.create(
            "worker_scout",
            "T-136 已由我收口，不需要再补产物或继续回执。保持待命即可。",
            creator="manager",
        )
        tasks.update(tid, status="进行中")
        path = paths.state_dir() / "tasks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tasks"][0]["created_at"] = 0
        data["tasks"][0]["updated_at"] = 0
        write_json(path, data)

        overdue = manager_watch.sweep(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        first_output = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert overdue == []
    assert first_output == []


def test_sweep_first_output_keeps_public_quiet_when_worker_is_thinking():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        notices = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "thinking",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(notices) == 1
    assert notices[0].task_id == tid
    assert "派工后无首产物" in notices[0].title
    assert notices[0].public_body == ""


def test_sweep_first_output_keeps_public_quiet_after_manager_progress():
    with isolated_env(team=_team()) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "\n".join([
                "[manager_watch]",
                "first_output_public_after_manager_alert_s = 300",
            ]) + "\n",
            encoding="utf-8",
        )
        tid = _age_task_to_epoch()
        first = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        progress_id = local_facts.append_message(
            "worker_scout", "manager",
            "我已看过现场，继续追首产物，三分钟内不出就改派。",
            task_id=tid)
        _set_inbox_message_created(progress_id, 450_000)
        second = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 602_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(first) == 1
    assert first[0].public_body == ""
    assert len(second) == 1
    assert second[0].public_body == ""


def test_sweep_first_output_stops_internal_repeat_after_feedback_log():
    with isolated_env(team=_team()) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "\n".join([
                "[manager_watch]",
                "first_output_public_after_manager_alert_s = 300",
            ]) + "\n",
            encoding="utf-8",
        )
        tid = _age_task_to_epoch()
        first = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        log_id = local_facts.append_log(
            "manager", "first_output_feedback",
            f"task_id={tid} kind=误报 note=worker pane has concrete evidence",
            ref=tid)
        _set_log_created(log_id, 450_000)
        second = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 602_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(first) == 1
    assert first[0].public_body == ""
    assert second == []


def test_sweep_first_output_suppresses_internal_repeat_after_feedback_log():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        first = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        log_id = local_facts.append_log(
            "manager", "first_output_feedback",
            f"task_id={tid} kind=证据不符 note=worker 已回真实短卡，本条为重复 watch 误报",
            ref=tid)
        _set_log_created(log_id, 450_000)
        second = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 602_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(first) == 1
    assert second == []
    assert len(manager_inbox) == 1


def test_sweep_suppresses_internal_repeat_after_feedback_log():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        first = manager_watch.sweep(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        log_id = local_facts.append_log(
            "manager", "first_output_feedback",
            f"task_id={tid} kind=证据不符 note=worker 已回真实短卡，本条为重复 watch 误报",
            ref=tid)
        _set_log_created(log_id, 450_000)
        second = manager_watch.sweep(
            now_ms_fn=lambda: 602_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(first) == 1
    assert second == []
    assert len(manager_inbox) == 1


def test_sweep_keeps_overdue_private_until_manager_grace_elapses():
    with isolated_env(team=_team()) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "\n".join([
                "[manager_watch]",
                "public_after_manager_alert_s = 300",
            ]) + "\n",
            encoding="utf-8",
        )
        tid = _age_task_to_epoch()
        first = manager_watch.sweep(
            now_ms_fn=lambda: 1801_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=900,
            public_overdue_s=1800,
        )
        second = manager_watch.sweep(
            now_ms_fn=lambda: 2102_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=1800,
        )

    assert len(first) == 1
    assert first[0].public_body == ""
    assert len(second) == 1
    assert "主管在首次提醒后仍未完成动作收口" in second[0].public_body


def test_sweep_first_output_suppresses_after_worker_report():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_facts.append_message(
            "manager", "worker_scout",
            "blocker: 小红书后台登录态失效，已刷新失败两次，需要老板扫码，10 分钟后复查",
            task_id=tid)
        recent = now_ms()
        notices = manager_watch.sweep_first_output(
            now_ms_fn=lambda: recent + 1_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert notices == []


def test_sweep_first_output_vague_worker_report_still_alerts():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_facts.append_message(
            "manager", "worker_scout", "正在生成", task_id=tid)
        notices = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(notices) == 1
    assert "当前判定：空话" in notices[0].body


def test_sweep_first_output_rejects_unusable_url_artifact():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_facts.append_message(
            "manager", "worker_scout",
            "artifact: https://httpstat.us/404", task_id=tid)
        with attr_patch(
            first_output_gate,
            check_url=lambda url, timeout_s=2.0: (False, "http 404", "image/png"),
        ):
            notices = manager_watch.sweep_first_output(
                now_ms_fn=lambda: 301_000,
                pane_state_fn=lambda agent: "ready",
                inject_manager_fn=lambda body: None,
                overdue_s=300,
                repeat_s=300,
                public_overdue_s=300,
            )

    assert len(notices) == 1
    assert "当前判定：证据不可用" in notices[0].body


def test_sweep_first_output_accepts_usable_url_artifact():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_facts.append_message(
            "manager", "worker_scout",
            "artifact: https://example.com/report.md", task_id=tid)
        with attr_patch(
            first_output_gate,
            check_url=lambda url, timeout_s=2.0: (True, "http 200", "text/markdown"),
        ):
            notices = manager_watch.sweep_first_output(
                now_ms_fn=lambda: 301_000,
                pane_state_fn=lambda agent: "ready",
                inject_manager_fn=lambda body: None,
                overdue_s=300,
                repeat_s=300,
                public_overdue_s=300,
            )

    assert notices == []


def test_sweep_first_output_rejects_fake_tmp_path():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_facts.append_message(
            "manager", "worker_scout",
            "artifact: /tmp/fake.png", task_id=tid)
        notices = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(notices) == 1
    assert "当前判定：路径不合法" in notices[0].body


def test_sweep_first_output_accepts_existing_local_artifact():
    with isolated_env(team=_team()) as tmp:
        tid = _age_task_to_epoch()
        artifact = tmp / "artifacts" / "T-1" / "out.png"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n")
        local_facts.append_message(
            "manager", "worker_scout",
            "artifact: artifacts/T-1/out.png", task_id=tid)
        notices = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert notices == []


def test_sweep_first_output_accepts_embedded_evidence_dir_progress():
    with isolated_env(team=_team()) as tmp:
        tid = _age_task_to_epoch()
        evidence_dir = tmp / "artifacts" / "builder-daily" / "2026-06-02"
        evidence_dir.mkdir(parents=True)
        local_facts.append_message(
            "manager", "worker_scout",
            f"raw 证据目录 {evidence_dir} 已落盘，当前缺最终 docs/receipt，继续生成中。",
            task_id=tid)
        notices = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert notices == []


def test_sweep_first_output_rejects_vague_blocker():
    with isolated_env(team=_team()):
        tid = _age_task_to_epoch()
        local_facts.append_message(
            "manager", "worker_scout", "blocker: 卡住了", task_id=tid)
        notices = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(notices) == 1
    assert "当前判定：blocker 不可行动" in notices[0].body


def test_sweep_first_output_suppresses_duplicate_until_repeat_window():
    with isolated_env(team=_team()):
        _age_task_to_epoch()
        first = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
        )
        second = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 500_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(first) == 1
    assert second == []
    assert len(manager_inbox) == 1


def test_sweep_first_output_public_alert_only_once_for_unchanged_problem():
    with isolated_env(team=_team()):
        _age_task_to_epoch()
        first = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        second = manager_watch.sweep_first_output(
            now_ms_fn=lambda: 602_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(first) == 1
    assert "还没有形成可验证首产物或真实 blocker" in first[0].public_body
    assert len(second) == 1
    assert second[0].public_body == ""


def test_latest_worker_signal_ignores_other_task_log_when_worker_has_multiple_open_tasks():
    with isolated_env(team=_team()):
        tid_a = tasks.create("worker_scout", "默会晨训校准｜2026-06-02｜worker_scout", creator="manager")
        tid_b = tasks.create("worker_scout", "请生成今天的 Builder Daily。", creator="manager")
        _set_task_times(tid_a, created_at=0, updated_at=0)
        _set_task_times(tid_b, created_at=0, updated_at=0)
        log_id = local_facts.append_log(
            "worker_scout", "say",
            "请生成今天的 Builder Daily。raw 证据目录 artifacts/builder-daily/2026-06-02 已落盘")
        _set_log_created(log_id, 200_000)
        tasks_by_id = {task["id"]: task for task in tasks.list_tasks()}
        assert manager_watch._latest_worker_signal_ms(tasks_by_id[tid_a]) == 0
        assert manager_watch._latest_worker_signal_ms(tasks_by_id[tid_b]) == 200_000


def test_sweep_ignores_non_team_assignee():
    with isolated_env(team=_team()):
        _age_task_to_epoch("operator")
        notices = manager_watch.sweep(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "pane missing",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=900,
        )

    assert notices == []


def test_sweep_skips_historical_tasks_beyond_max_age_on_first_run():
    with isolated_env(team=_team()):
        _age_task_to_epoch()
        notices = manager_watch.sweep(
            now_ms_fn=lambda: 30_000_000,
            pane_state_fn=lambda agent: "pane missing",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=900,
            max_task_age_s=21_600,
        )

    assert notices == []


def _age_inbox_message_to_epoch(local_id: str) -> None:
    path = paths.state_dir() / "facts" / "inbox.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for msg in data["messages"]:
        if msg["local_id"] == local_id:
            msg["created_at"] = 0
            break
    write_json(path, data)


def _set_inbox_message_created(local_id: str, created_at: int) -> None:
    path = paths.state_dir() / "facts" / "inbox.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for msg in data["messages"]:
        if msg["local_id"] == local_id:
            msg["created_at"] = created_at
            break
    write_json(path, data)


def _set_task_times(task_id: str, *, created_at: int, updated_at: int) -> None:
    path = paths.state_dir() / "tasks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for task in data["tasks"]:
        if task["id"] == task_id:
            task["created_at"] = created_at
            task["updated_at"] = updated_at
            break
    write_json(path, data)


def _set_log_created(local_id: str, created_at: int) -> None:
    path = paths.state_dir() / "facts" / "logs.jsonl"
    rows = []
    if path.exists():
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    for row in rows:
        if row.get("local_id") == local_id:
            row["created_at"] = created_at
            break
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_sweep_boss_inbox_reinjects_stale_unread_boss_message():
    injected = []
    alerts = []
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user",
            "Stripe 注册了，但是 Webhook/keys 不知道配置到哪里",
            priority="高",
        )
        _age_inbox_message_to_epoch(local_id)
        notices = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: injected.append(body),
            alert_fn=lambda notice: alerts.append(notice),
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=False)
        manager_logs = local_facts.list_logs("manager_watch", limit=20)

    assert len(notices) == 1
    notice = notices[0]
    assert notice.task_id == local_id
    assert "老板消息未收口" in notice.title
    assert "先处理这条老板消息" in notice.body
    assert "Stripe 注册了" in notice.body
    assert "<给老板的回复>" not in notice.body
    assert "ct say" not in notice.body
    assert "/srv" not in notice.body
    assert "stdin" not in notice.body
    assert "pane" not in notice.body
    assert "不要复制本通知文字" in notice.body
    assert "已自动重投给 manager" in notice.public_body
    assert injected and local_id in injected[0]
    assert alerts == [notice]
    assert not any(m["from"] == "manager_watch" for m in manager_inbox)
    assert any(
        row["agent"] == "manager_watch"
        and row["ref"] == local_id
        and "boss_inbox_watch" in row["content"]
        for row in manager_logs
    )


def test_sweep_boss_inbox_public_alert_mentions_thinking_state():
    alerts = []
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user",
            "你现在有在认真看我发的活动安排表吗",
            priority="高",
        )
        _age_inbox_message_to_epoch(local_id)
        notices = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 601_000,
            pane_state_fn=lambda agent: "thinking",
            inject_manager_fn=lambda body: None,
            alert_fn=lambda notice: alerts.append(notice),
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(notices) == 1
    assert "主管还在处理，但没有形成老板可读结论" in notices[0].body
    assert "pane" not in notices[0].body
    assert alerts[0].public_body == ""


def test_sweep_boss_inbox_public_alert_mentions_provider_error_state():
    alerts = []
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user",
            "导师回传怎么还没到",
            priority="高",
        )
        _age_inbox_message_to_epoch(local_id)
        notices = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 601_000,
            pane_state_fn=lambda agent: "provider/api error",
            inject_manager_fn=lambda body: None,
            alert_fn=lambda notice: alerts.append(notice),
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(notices) == 1
    assert "主管回复通道不稳定" in notices[0].body
    assert "provider/api error" not in notices[0].body
    assert "主管回复通道不稳定" in alerts[0].public_body
    assert "provider/api error" not in alerts[0].public_body


def test_sweep_boss_inbox_c4_public_alert_hides_runtime_commands():
    alerts = []
    with isolated_env(team=_team()):
        for text in ("第一条还没回", "第二条也没回", "第三条继续没回"):
            local_id = local_facts.append_message(
                "manager", "user", text, priority="高")
            _age_inbox_message_to_epoch(local_id)
        notices = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 601_000,
            pane_state_fn=lambda agent: "provider/api error",
            inject_manager_fn=lambda body: None,
            alert_fn=lambda notice: alerts.append(notice),
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(notices) == 1
    notice = notices[0]
    assert "C4" in notice.title
    assert "主管回复通道不稳定" in notice.body
    assert "主管回复通道不稳定" in notice.public_body
    assert "主管回复通道不稳定" in notice.public_key
    for forbidden in (
        "provider/api error", "provider", "api", "pane", "ct ", "claudeteam",
        "/srv", "stdin", "down &&", "restart manager",
    ):
        assert forbidden not in notice.body
        assert forbidden not in notice.public_body
        assert forbidden not in notice.public_key


def test_sweep_boss_inbox_suppresses_duplicate_until_repeat_window():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "ping", priority="高")
        _age_inbox_message_to_epoch(local_id)
        first = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
        )
        second = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 500_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
        )

    assert len(first) == 1
    assert second == []


def test_sweep_boss_inbox_public_alert_only_once_for_same_message():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "团队成员还活着吗", priority="高")
        _age_inbox_message_to_epoch(local_id)
        first = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 601_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )
        second = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 902_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            public_overdue_s=300,
        )

    assert len(first) == 1
    assert "已自动重投给 manager" in first[0].public_body
    assert len(second) == 1
    assert second[0].public_body == ""


def test_sweep_boss_inbox_stops_after_max_age_even_if_alerted_before():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "ping", priority="高")
        _age_inbox_message_to_epoch(local_id)
        first = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            max_age_s=1_000,
        )
        second = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 1_001_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
            repeat_s=300,
            max_age_s=1_000,
        )
        state = json.loads(
            (paths.state_dir() / "manager-watch.json").read_text(
                encoding="utf-8"))

    assert len(first) == 1
    assert second == []
    assert local_id not in state.get("boss_inbox_alerts", {})


def test_sweep_boss_inbox_ignores_peer_messages():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "worker_scout", "内部回执", priority="中")
        _age_inbox_message_to_epoch(local_id)
        notices = manager_watch.sweep_boss_inbox(
            now_ms_fn=lambda: 301_000,
            pane_state_fn=lambda agent: "ready",
            inject_manager_fn=lambda body: None,
            overdue_s=300,
        )

    assert notices == []


def test_sweep_manager_actions_reinjects_read_boss_message_without_action():
    injected = []
    alerts = []
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "继续打磨，把第 4 张图重做得更正式",
            priority="高",
        )
        assert local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))
        path = paths.state_dir() / "manager-action-guard.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["records"][0]["created_at"] = 0
        data["records"][0]["read_at"] = 0
        write_json(path, data)

        notices = manager_watch.sweep_manager_actions(
            now_ms_fn=lambda: 181_000,
            inject_manager_fn=lambda body: injected.append(body),
            alert_fn=lambda notice: alerts.append(notice),
            overdue_s=180,
            repeat_s=300,
            public_overdue_s=180,
        )
        manager_inbox = local_facts.list_messages("manager", unread_only=True)

    assert len(notices) == 1
    notice = notices[0]
    assert notice.task_id == local_id
    assert "老板消息已读未闭环" in notice.title
    assert "继续打磨" in notice.body
    assert "三选一" in notice.body
    assert "长时间做图" in notice.body
    assert "已自动催 manager" in notice.public_body
    assert injected and local_id in injected[0]
    assert alerts == [notice]
    assert any(m["from"] == "manager_watch" and m["task_id"] == local_id
               for m in manager_inbox)
