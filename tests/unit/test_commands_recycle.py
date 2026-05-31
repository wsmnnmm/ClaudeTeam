"""Tests for `claudeteam recycle` — in-place pane restarts."""
from __future__ import annotations

import contextlib

from helpers import attr_patch, isolated_env, run_cli, tmux_patch
from claudeteam.commands import recycle as recycle_cmd
from claudeteam.runtime import lifecycle


@contextlib.contextmanager
def _fake_tmux():
    state = {"session_exists": set(), "windows": set(), "calls": []}

    def has_session(name):
        state["calls"].append(("has_session", name))
        return name in state["session_exists"]

    def has_window(target):
        state["calls"].append(("has_window", str(target)))
        return str(target) in state["windows"]

    def new_window(target):
        state["calls"].append(("new_window", str(target)))
        state["windows"].add(str(target))
        return True

    def kill_window(target):
        state["calls"].append(("kill_window", str(target)))
        state["windows"].discard(str(target))
        return True

    def send_keys(target, *keys):
        state["calls"].append(("send_keys", str(target), *keys))
        return True

    with tmux_patch(
        has_session=has_session,
        has_window=has_window,
        new_window=new_window,
        kill_window=kill_window,
        send_keys=send_keys,
    ):
        yield state


def test_recycle_help_prints_usage():
    rc, out, _ = run_cli(["recycle", "--help"])
    assert rc == 0
    assert "usage: claudeteam recycle <agent> [<agent> ...]" in out


def test_recycle_requires_running_session():
    team = {"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}
    with isolated_env(team=team), _fake_tmux():
        rc, _, err = run_cli(["recycle", "manager"])
    assert rc == 1
    assert "tmux session S not running" in err


def test_recycle_unknown_agent_returns_error():
    team = {"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}
    with isolated_env(team=team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, _, err = run_cli(["recycle", "missing"])
    assert rc == 1
    assert "unknown agent: missing" in err


def test_recycle_existing_pane_restarts_manager():
    team = {"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}
    with isolated_env(team=team), _fake_tmux() as fake, \
            attr_patch(recycle_cmd.lifecycle,
                       provision_pane=lambda agent, target: lifecycle.READY):
        fake["session_exists"].add("S")
        fake["windows"].add("S:manager")
        rc, out, _ = run_cli(["recycle", "manager"])
    assert rc == 0
    assert "recycled: manager (codex-cli) → S:manager" in out
    ops = [
        call for call in fake["calls"]
        if call[0] in {"send_keys", "kill_window", "new_window"}
    ]
    assert ops[0] == ("send_keys", "S:manager", "C-c")
    assert ("kill_window", "S:manager") in fake["calls"]
    assert ("new_window", "S:manager") in fake["calls"]


def test_recycle_missing_pane_creates_fresh_window():
    team = {
        "session": "S",
        "agents": {"worker_lazy": {"cli": "claude-code", "lazy": True}},
    }
    with isolated_env(team=team), _fake_tmux() as fake, \
            attr_patch(recycle_cmd.lifecycle,
                       provision_pane=lambda agent, target: lifecycle.LAZY):
        fake["session_exists"].add("S")
        rc, out, _ = run_cli(["recycle", "worker_lazy"])
    assert rc == 0
    assert "no existing pane, creating fresh window" in out
    assert "recycled (lazy): worker_lazy (claude-code) → S:worker_lazy" in out
    assert ("new_window", "S:worker_lazy") in fake["calls"]
