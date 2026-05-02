"""Tests for store/tasks.py — local task store."""
from __future__ import annotations

import contextlib
import os
import tempfile

from claudeteam.store import tasks


@contextlib.contextmanager
def _isolated():
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


# ── create ────────────────────────────────────────────────────────


def test_create_returns_task_id_and_persists():
    with _isolated():
        tid = tasks.create("worker", "do thing")
        assert tid == "T-1"
        rows = tasks.list_tasks()
        assert len(rows) == 1
        assert rows[0]["title"] == "do thing"
        assert rows[0]["status"] == "待处理"


def test_ids_increment_across_creates():
    with _isolated():
        a = tasks.create("x", "first")
        b = tasks.create("y", "second")
        assert a == "T-1" and b == "T-2"


def test_create_with_metadata_persists_creator_and_description():
    with _isolated():
        tid = tasks.create("worker", "fix X",
                           description="root cause is Y",
                           creator="manager")
        t = tasks.get(tid)
        assert t["creator"] == "manager"
        assert t["description"] == "root cause is Y"


def test_create_empty_title_rejects():
    with _isolated():
        try:
            tasks.create("worker", "   ")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError on empty title")


# ── update ────────────────────────────────────────────────────────


def test_update_status_advances_state():
    with _isolated():
        tid = tasks.create("w", "task")
        assert tasks.update(tid, status="进行中") is True
        assert tasks.get(tid)["status"] == "进行中"


def test_update_invalid_status_rejects():
    with _isolated():
        tid = tasks.create("w", "task")
        try:
            tasks.update(tid, status="not-a-status")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")


def test_update_missing_task_returns_false():
    with _isolated():
        assert tasks.update("T-99", status="已完成") is False


def test_update_terminal_status_sets_completed_at():
    with _isolated():
        tid = tasks.create("w", "x")
        tasks.update(tid, status="已完成")
        t = tasks.get(tid)
        assert t["completed_at"] is not None


def test_update_back_from_terminal_clears_completed_at():
    with _isolated():
        tid = tasks.create("w", "x")
        tasks.update(tid, status="已完成")
        tasks.update(tid, status="进行中")
        assert tasks.get(tid)["completed_at"] is None


def test_update_only_changes_specified_fields():
    with _isolated():
        tid = tasks.create("w1", "title-1", description="d-1", creator="c-1")
        tasks.update(tid, status="进行中")
        t = tasks.get(tid)
        # other fields untouched
        assert t["assignee"] == "w1"
        assert t["title"] == "title-1"
        assert t["description"] == "d-1"
        assert t["creator"] == "c-1"


def test_update_can_reassign_and_retitle():
    with _isolated():
        tid = tasks.create("w1", "old", description="old-d")
        tasks.update(tid, assignee="w2", title="new", description="new-d")
        t = tasks.get(tid)
        assert (t["assignee"], t["title"], t["description"]) == ("w2", "new", "new-d")


# ── list/get ──────────────────────────────────────────────────────


def test_list_filters_by_status():
    with _isolated():
        a = tasks.create("w", "a")
        b = tasks.create("w", "b")
        tasks.update(a, status="已完成")
        only_done = tasks.list_tasks(status="已完成")
        only_open = tasks.list_tasks(status="待处理")
        assert [t["id"] for t in only_done] == [a]
        assert [t["id"] for t in only_open] == [b]


def test_list_filters_by_assignee():
    with _isolated():
        tasks.create("alice", "task-a")
        tasks.create("bob", "task-b")
        tasks.create("alice", "task-a2")
        out = tasks.list_tasks(assignee="alice")
        assert {t["title"] for t in out} == {"task-a", "task-a2"}


def test_list_returns_empty_when_store_missing():
    with _isolated():
        assert tasks.list_tasks() == []


def test_get_returns_none_for_unknown_id():
    with _isolated():
        assert tasks.get("T-doesnotexist") is None


def test_list_sorted_by_id():
    with _isolated():
        for i in range(5):
            tasks.create(f"w{i}", f"task {i}")
        rows = tasks.list_tasks()
        assert [t["id"] for t in rows] == ["T-1", "T-2", "T-3", "T-4", "T-5"]
