from __future__ import annotations

from helpers import isolated_env, run_cli, tmux_patch


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
    assert "真实交付、真实 blocker、需要老板动作" in injected[0]
    assert "对齐、待命、继续监控、无新事实不要 `say` 刷群" in injected[0]
