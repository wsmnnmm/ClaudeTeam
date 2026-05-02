"""Tests for `claudeteam status` (set+show) and `claudeteam log` (append)."""
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


# ── status ─────────────────────────────────────────────────────────


def test_status_set_writes_store_and_prints_summary():
    with _isolated_state():
        rc, out, _ = _run(["status", "worker", "进行中", "do task X"])
        assert rc == 0
        assert "worker → 进行中: do task X" in out
        snap = local_facts.get_status("worker")
        assert snap is not None
        assert snap["status"] == "进行中"
        assert snap["task"] == "do task X"
        assert snap["blocker"] == ""


def test_status_set_with_blocker_appends_marker():
    with _isolated_state():
        rc, out, _ = _run(["status", "worker", "阻塞", "stuck", "missing API key"])
        assert rc == 0
        assert "⛔ missing API key" in out
        snap = local_facts.get_status("worker")
        assert snap["blocker"] == "missing API key"


def test_status_show_when_unrecorded():
    with _isolated_state():
        rc, out, _ = _run(["status", "noone"])
        assert rc == 0
        assert "noone: no status recorded" in out


def test_status_show_after_set():
    with _isolated_state():
        _run(["status", "a", "进行中", "task"])
        rc, out, _ = _run(["status", "a"])
        assert rc == 0
        assert "a: 进行中 | task" in out


def test_status_set_idempotent_overwrites_previous():
    with _isolated_state():
        _run(["status", "a", "进行中", "first"])
        _run(["status", "a", "已完成", "second"])
        snap = local_facts.get_status("a")
        assert snap["status"] == "已完成"
        assert snap["task"] == "second"


def test_status_zero_args_returns_one_with_usage():
    rc, _, err = _run(["status"])
    assert rc == 1
    assert "usage:" in err


def test_status_set_missing_state_or_task_returns_one():
    with _isolated_state():
        rc, _, err = _run(["status", "agent", "进行中"])
        assert rc == 1
        assert "usage:" in err


# ── log ────────────────────────────────────────────────────────────


def test_log_appends_to_jsonl_and_prints_id():
    with _isolated_state():
        rc, out, _ = _run(["log", "worker", "info", "checkpoint reached"])
        assert rc == 0
        assert "logged: worker/info" in out
        rows = local_facts.list_logs("worker")
        assert len(rows) == 1
        assert rows[0]["content"] == "checkpoint reached"
        assert rows[0]["ref"] == ""


def test_log_with_ref():
    with _isolated_state():
        _run(["log", "worker", "task", "did the thing", "TASK-7"])
        rows = local_facts.list_logs("worker")
        assert rows[0]["ref"] == "TASK-7"


def test_log_appends_multiple_in_order():
    with _isolated_state():
        _run(["log", "a", "info", "first"])
        _run(["log", "a", "info", "second"])
        _run(["log", "b", "info", "other"])
        a_rows = local_facts.list_logs("a")
        b_rows = local_facts.list_logs("b")
        assert [r["content"] for r in a_rows] == ["first", "second"]
        assert len(b_rows) == 1


def test_log_missing_args_returns_one():
    rc, _, err = _run(["log", "agent", "info"])
    assert rc == 1
    assert "usage: claudeteam log" in err
