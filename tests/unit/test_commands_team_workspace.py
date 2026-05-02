"""Tests for `claudeteam team` and `claudeteam workspace` (read-side)."""
from __future__ import annotations

import contextlib
import io
import os
import tempfile

from claudeteam import cli
from claudeteam.store import local_facts


@contextlib.contextmanager
def _isolated_state():
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("CLAUDETEAM_STATE_DIR")
        os.environ["CLAUDETEAM_STATE_DIR"] = tmp
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("CLAUDETEAM_STATE_DIR", None)
            else:
                os.environ["CLAUDETEAM_STATE_DIR"] = old


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


# ── team ──────────────────────────────────────────────────────────


def test_team_empty_when_no_agents():
    with _isolated_state():
        rc, out, _ = _run(["team"])
        assert rc == 0
        assert "no agents have reported status yet" in out


def test_team_lists_all_agents_sorted_by_name():
    with _isolated_state():
        _run(["status", "worker_b", "进行中", "doing b"])
        _run(["status", "worker_a", "已完成", "done a"])
        _run(["status", "worker_c", "阻塞", "stuck", "no api key"])
        rc, out, _ = _run(["team"])
        assert rc == 0
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) == 3
        # alphabetical
        assert lines[0].startswith("worker_a")
        assert lines[1].startswith("worker_b")
        assert lines[2].startswith("worker_c")
        # blocker shown for worker_c
        assert "⛔ no api key" in lines[2]


def test_team_shows_relative_age():
    with _isolated_state():
        _run(["status", "agent", "进行中", "task"])
        rc, out, _ = _run(["team"])
        assert rc == 0
        # latest write is < 1m ago
        assert "ago)" in out


# ── workspace ─────────────────────────────────────────────────────


def test_workspace_empty_returns_zero_with_message():
    with _isolated_state():
        rc, out, _ = _run(["workspace", "nobody"])
        assert rc == 0
        assert "nobody: no log entries" in out


def test_workspace_lists_recent_log_entries():
    with _isolated_state():
        _run(["log", "a", "info", "first"])
        _run(["log", "a", "task", "second", "TASK-1"])
        _run(["log", "b", "info", "should not appear"])
        rc, out, _ = _run(["workspace", "a"])
        assert rc == 0
        assert "a: last 2 log entries" in out
        assert "first" in out and "second" in out
        assert "(TASK-1)" in out
        assert "should not appear" not in out


def test_workspace_limit_caps_returned_rows():
    with _isolated_state():
        for i in range(5):
            _run(["log", "a", "info", f"entry-{i}"])
        rc, out, _ = _run(["workspace", "a", "--limit", "2"])
        assert rc == 0
        assert "last 2 log entries" in out
        assert "entry-3" in out and "entry-4" in out
        assert "entry-0" not in out


def test_workspace_invalid_limit_returns_one():
    with _isolated_state():
        rc, _, err = _run(["workspace", "a", "--limit", "abc"])
        assert rc == 1
        assert "usage:" in err


def test_workspace_zero_args_returns_one():
    rc, _, err = _run(["workspace"])
    assert rc == 1
    assert "usage:" in err


# ── store helper ───────────────────────────────────────────────────


def test_list_all_statuses_returns_sorted_rows():
    with _isolated_state():
        local_facts.upsert_status("z", "进行中", "z task")
        local_facts.upsert_status("a", "已完成", "a task")
        rows = local_facts.list_all_statuses()
        assert [r["agent"] for r in rows] == ["a", "z"]


def test_list_all_statuses_empty_when_no_writes():
    with _isolated_state():
        assert local_facts.list_all_statuses() == []
