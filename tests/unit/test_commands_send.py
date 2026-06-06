from __future__ import annotations

from helpers import attr_patch, isolated_env, run_cli, tmux_patch
from claudeteam.commands import say as say_cmd
from claudeteam.store import tasks
from claudeteam.runtime import incident_learning as il


def test_send_worker_nudge_routes_internal_replies_via_send():
    injected: list[str] = []

    def fake_inject(target, text, *, submit_keys=None):
        injected.append(text)
        return True

    team = {
        "session": "test-team",
        "agents": {
            "manager": {"cli": "codex-cli", "role": "主管"},
            "worker_cc": {"cli": "codex-cli", "role": "员工"},
        },
    }
    with isolated_env(team=team, runtime_config={"chat_id": "oc_test"}), \
            tmux_patch(has_window=lambda target: True, inject=fake_inject):
        rc, out, err = run_cli([
            "send", "worker_cc", "manager", "T-161 对齐一下", "--no-task",
        ])

    assert rc == 0, err
    assert "inbox: worker_cc ← manager" in out
    assert len(injected) == 1
    assert "消息摘要：T-161 对齐一下" in injected[0]
    assert "先 `" in injected[0] and "bin/ct read" in injected[0]
    assert "bin/ct status worker_cc 进行中" in injected[0]
    assert "read/status 只是接手信号，不是完成" in injected[0]
    assert "低成本提示" in injected[0]
    assert "内部回执用 `" in injected[0] and "bin/ct send manager worker_cc" in injected[0]
    assert "bin/ct say worker_cc - --to user" in injected[0]
    assert "群播报三分类" in injected[0]
    assert "一定发（真实交付/真实 blocker/需要老板动作" in injected[0]
    assert "可发可不发（如已接手/排查中/复现中" in injected[0]
    assert "禁止发（收到/对齐/待命/继续监控/无新事实" in injected[0]
    assert "[chat.publish.worker_progress]" in injected[0]


def test_send_includes_relevant_learning_context_for_new_task():
    injected: list[str] = []

    def fake_inject(target, text, *, submit_keys=None):
        injected.append(text)
        return True

    team = {
        "session": "test-team",
        "agents": {
            "manager": {"cli": "codex-cli", "role": "主管"},
            "worker_cc": {"cli": "codex-cli", "role": "员工"},
        },
    }
    with isolated_env(team=team, runtime_config={"chat_id": "oc_test"}), \
            tmux_patch(has_window=lambda target: True, inject=fake_inject):
        il.capture(il.Incident(
            "first_output_failure", "worker_cc", "空话",
            "artifact evidence URL screenshot",
        ), now_ms_fn=lambda: 1_000_000)
        rc, out, err = run_cli([
            "send", "worker_cc", "manager", "artifact evidence verification",
        ])

    assert rc == 0, err
    assert "task_id=T-1" in out
    assert len(injected) == 1
    assert "历史相关教训" in injected[0]
    assert "artifact" in injected[0] or "首产物不合格" in injected[0]


def test_send_broadcasts_first_optional_worker_progress_only_once():
    broadcasts: list[list[str]] = []

    def fake_say_main(argv):
        broadcasts.append(list(argv))
        return 0

    team = {
        "session": "test-team",
        "agents": {
            "manager": {"cli": "codex-cli", "role": "主管"},
            "worker_cc": {"cli": "codex-cli", "role": "员工"},
        },
    }
    with isolated_env(team=team, runtime_config={"chat_id": "oc_test"}), \
            attr_patch(say_cmd, main=fake_say_main):
        tid = tasks.create("worker_cc", "修云上 receipt")
        rc1, out1, err1 = run_cli([
            "send", "manager", "worker_cc",
            "已接手，处理中，先看云上链路现状。",
            "--task-id", tid, "--no-inject",
        ])
        rc2, out2, err2 = run_cli([
            "send", "manager", "worker_cc",
            "处理中，正在补下一轮对账。",
            "--task-id", tid, "--no-inject",
        ])

    assert rc1 == 0, err1
    assert rc2 == 0, err2
    assert "task_id=T-1" in out1
    assert "task_id=T-1" in out2
    assert len(broadcasts) == 1
    assert broadcasts[0][0] == "worker_cc"
    assert broadcasts[0][-2:] == ["--to", "user"]
