"""Tests for `claudeteam send / inbox / read` commands.

Goes through cli.main([...]) so we exercise the dispatch + handler
contract end-to-end (without spawning a subprocess).
"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
from pathlib import Path

from claudeteam import cli
from claudeteam.store import local_facts


@contextlib.contextmanager
def _isolated_state():
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("CLAUDETEAM_STATE_DIR")
        os.environ["CLAUDETEAM_STATE_DIR"] = tmp
        try:
            yield Path(tmp)
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


def test_send_writes_inbox_and_prints_local_id():
    with _isolated_state():
        rc, out, err = _run(["send", "worker", "manager", "do task X"])
        assert rc == 0, err
        assert "sent → worker" in out
        assert "local_id=msg_" in out

        rows = local_facts.list_messages("worker")
        assert len(rows) == 1
        assert rows[0]["content"] == "do task X"
        assert rows[0]["from"] == "manager"


def test_send_priority_param_threads_through():
    with _isolated_state():
        _run(["send", "a", "b", "msg", "高"])
        rows = local_facts.list_messages("a")
        assert rows[0]["priority"] == "高"


def test_send_missing_args_returns_one_with_usage_to_stderr():
    rc, out, err = _run(["send", "only-one-arg"])
    assert rc == 1
    assert "usage: claudeteam send" in err


def test_inbox_lists_unread_with_local_id_and_returns_zero():
    with _isolated_state():
        _run(["send", "w", "m", "first"])
        _run(["send", "w", "m", "second"])
        rc, out, _ = _run(["inbox", "w"])
        assert rc == 0
        assert "📬 w: 2 unread" in out
        assert "first" in out and "second" in out


def test_inbox_empty_prints_no_unread():
    with _isolated_state():
        rc, out, _ = _run(["inbox", "nobody"])
        assert rc == 0
        assert "📭 nobody: no unread messages" in out


def test_read_marks_then_inbox_drops_it():
    with _isolated_state():
        _run(["send", "w", "m", "task A"])
        msgs = local_facts.list_messages("w")
        local_id = msgs[0]["local_id"]

        rc, out, _ = _run(["read", local_id])
        assert rc == 0
        assert "marked read" in out

        rc, out, _ = _run(["inbox", "w"])
        assert rc == 0
        assert "📭 w: no unread messages" in out


def test_read_unknown_id_returns_one():
    with _isolated_state():
        rc, _, err = _run(["read", "msg_does_not_exist"])
        assert rc == 1
        assert "no such message" in err
