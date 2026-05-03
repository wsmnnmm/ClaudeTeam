"""Tests for `claudeteam reidentify <agent>`."""
from __future__ import annotations

from helpers import isolated_env, run_cli, tmux_patch


_TEAM = {"session": "S", "agents": {"manager": {}, "worker_cc": {}}}


def test_reidentify_zero_args_returns_one():
    rc, _, err = run_cli(["reidentify"])
    assert rc == 1
    assert "usage:" in err


def test_reidentify_unknown_agent_returns_one():
    with isolated_env(team=_TEAM):
        rc, _, err = run_cli(["reidentify", "ghost"])
        assert rc == 1
        assert "unknown agent" in err


def test_reidentify_session_down_returns_one():
    with isolated_env(team=_TEAM), tmux_patch(has_session=lambda s: False):
        rc, _, err = run_cli(["reidentify", "manager"])
        assert rc == 1
        assert "tmux session" in err and "not running" in err


def test_reidentify_no_pane_returns_one():
    with isolated_env(team=_TEAM), tmux_patch(
            has_session=lambda s: True,
            has_window=lambda t: False):
        rc, _, err = run_cli(["reidentify", "manager"])
        assert rc == 1
        assert "no pane" in err


def test_reidentify_injects_init_prompt_into_existing_pane():
    captured = {}

    def fake_inject(target, text, **kw):
        captured["target"] = str(target)
        captured["text"] = text
        return True

    with isolated_env(team=_TEAM), tmux_patch(
            has_session=lambda s: True,
            has_window=lambda t: True,
            inject=fake_inject):
        rc, out, _ = run_cli(["reidentify", "manager"])
        assert rc == 0
        assert captured["target"] == "S:manager"
        # init_prompt body: "You are manager. Read agents/manager/identity.md"
        assert "You are manager" in captured["text"]
        assert "agents/manager/identity.md" in captured["text"]
        assert "✅" in out
