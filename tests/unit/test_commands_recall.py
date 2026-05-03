"""Tests for `claudeteam recall <agent>` — memory inspection."""
from __future__ import annotations

import json

from helpers import isolated_env, run_cli
from claudeteam.store import memory


def test_recall_empty_memory_prints_friendly_message():
    with isolated_env():
        rc, out, _ = run_cli(["recall", "manager"])
        assert rc == 0
        assert "no memory entries" in out


def test_recall_lists_entries_oldest_first_with_kind_and_ref():
    """Bullets render `[timestamp] [kind] content (ref=X)`. Order is
    chronological (oldest first), matching memory.list_recent semantics."""
    with isolated_env():
        memory.append("worker_cc", "task_assigned", "fix login", ref="om_1")
        memory.append("worker_cc", "task_completed", "fix login", ref="om_1")
        rc, out, _ = run_cli(["recall", "worker_cc"])
        assert rc == 0
        assert "🧠 worker_cc: 2 entries" in out
        assert "[task_assigned] fix login  (ref=om_1)" in out
        assert "[task_completed] fix login  (ref=om_1)" in out
        # Order: assigned line appears before completed
        assert out.index("task_assigned") < out.index("task_completed")


def test_recall_limit_default_is_twenty():
    """When no --limit, show up to 20 entries (memory's default cap window)."""
    with isolated_env():
        for i in range(30):
            memory.append("w", "note", f"i={i}")
        rc, out, _ = run_cli(["recall", "w"])
        assert rc == 0
        # 20 newest, oldest-first → i=10 to i=29 in body
        assert "i=10" in out
        assert "i=29" in out
        assert "i=9" not in out


def test_recall_respects_explicit_limit():
    with isolated_env():
        for i in range(10):
            memory.append("w", "note", f"i={i}")
        rc, out, _ = run_cli(["recall", "w", "--limit", "3"])
        assert rc == 0
        # last 3 → i=7, 8, 9
        for i in (7, 8, 9):
            assert f"i={i}" in out
        for i in (0, 1, 2, 3, 4, 5, 6):
            assert f"i={i}" not in out


def test_recall_json_dumps_records_machine_readable():
    """--json emits the raw record list — for jq / smoke conductors."""
    with isolated_env():
        memory.append("w", "learning", "auth uses bcrypt")
        rc, out, _ = run_cli(["recall", "w", "--json"])
        assert rc == 0
        rows = json.loads(out)
        assert len(rows) == 1
        assert rows[0]["kind"] == "learning"
        assert rows[0]["content"] == "auth uses bcrypt"


def test_recall_invalid_limit_returns_error():
    rc, _, err = run_cli(["recall", "w", "--limit", "abc"])
    assert rc == 1
    assert "must be an integer" in err


def test_recall_zero_limit_returns_error():
    rc, _, err = run_cli(["recall", "w", "--limit", "0"])
    assert rc == 1
    assert ">= 1" in err


def test_recall_zero_args_returns_usage():
    rc, _, err = run_cli(["recall"])
    assert rc == 1
    assert "usage:" in err


def test_recall_help_flag():
    rc, out, _ = run_cli(["recall", "--help"])
    assert rc == 0
    assert "usage: claudeteam recall" in out


def test_recall_registered_in_cli():
    from claudeteam.cli import COMMANDS
    assert "recall" in COMMANDS
