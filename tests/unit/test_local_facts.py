"""Tests for the local-facts store (inbox / status / log).

Each test runs inside `isolated_env()` so the state dir is fresh per test.
"""
from __future__ import annotations

import time

from claudeteam.store import local_facts
from helpers import isolated_env


def test_append_then_list_messages():
    with isolated_env():
        mid = local_facts.append_message("worker", "manager", "hello", priority="高")
        rows = local_facts.list_messages("worker")
        assert len(rows) == 1
        assert rows[0]["local_id"] == mid
        assert rows[0]["content"] == "hello"
        assert rows[0]["priority"] == "高"
        assert rows[0]["read"] is False


def test_list_filters_by_agent_and_unread_only():
    with isolated_env():
        local_facts.append_message("a", "manager", "to a")
        local_facts.append_message("b", "manager", "to b")
        mid_unread = local_facts.append_message("a", "manager", "still unread")
        # mark first message read; second remains unread
        first_a = local_facts.list_messages("a")[0]
        local_facts.mark_read(first_a["local_id"])

        unread_a = local_facts.list_messages("a", unread_only=True)
        assert len(unread_a) == 1
        assert unread_a[0]["local_id"] == mid_unread

        all_b = local_facts.list_messages("b")
        assert len(all_b) == 1
        assert all_b[0]["content"] == "to b"


def test_high_priority_messages_sort_before_normal_backlog():
    with isolated_env():
        low = local_facts.append_message("manager", "worker", "normal", priority="中")
        high = local_facts.append_message("manager", "user", "boss", priority="高")

        rows = local_facts.list_messages("manager")

        assert [r["local_id"] for r in rows] == [high, low]


def test_mark_read_sets_flag_and_returns_false_on_miss():
    with isolated_env():
        mid = local_facts.append_message("a", "b", "x")
        assert local_facts.mark_read(mid) is True
        assert local_facts.list_messages("a", unread_only=True) == []
        assert local_facts.mark_read(mid) is True  # idempotent
        assert local_facts.mark_read("local_does_not_exist") is False


def test_get_message_returns_row_by_local_id():
    with isolated_env():
        mid = local_facts.append_message("worker", "manager", "ship it")
        row = local_facts.get_message(mid)
        assert row is not None
        assert row["local_id"] == mid
        assert row["to"] == "worker"
        assert row["from"] == "manager"
        assert row["content"] == "ship it"


def test_get_message_returns_none_on_miss():
    with isolated_env():
        assert local_facts.get_message("msg_missing") is None


def test_mark_first_response_stores_contract_without_reading_message():
    with isolated_env():
        mid = local_facts.append_message("manager", "user", "查一下速度为什么慢", priority="高")
        ok = local_facts.mark_first_response(
            mid,
            response_message_id="om_first",
            elapsed_ms=5300,
            response_contract={"type": "verification", "next_step": "补链路耗时证据"},
        )
        row = local_facts.get_message(mid)

    assert ok is True
    assert row["read"] is False
    assert row["first_response_message_id"] == "om_first"
    assert row["first_response_elapsed_ms"] == 5300
    assert row["first_response_contract"] == {
        "type": "verification",
        "next_step": "补链路耗时证据",
    }


def test_latest_unfulfilled_response_contract_then_mark_fulfilled():
    with isolated_env():
        old = local_facts.append_message("manager", "user", "旧问题")
        new = local_facts.append_message("manager", "user", "新问题")
        local_facts.mark_first_response(
            old,
            response_contract={"type": "quick_answer", "next_step": "直接判断"},
        )
        local_facts.mark_first_response(
            new,
            response_contract={"type": "research", "next_step": "补资料依据"},
        )

        row = local_facts.latest_unfulfilled_response_contract("manager")
        marked = local_facts.mark_response_contract_fulfilled(
            new,
            ok=True,
            note="matched",
            response_message_id="om_final",
        )
        next_row = local_facts.latest_unfulfilled_response_contract("manager")
        stored = local_facts.get_message(new)

    assert row["local_id"] == new
    assert marked is True
    assert next_row["local_id"] == old
    assert stored["first_response_contract_fulfilled_ok"] is True
    assert stored["first_response_contract_fulfilled_note"] == "matched"
    assert stored["first_response_contract_fulfilled_message_id"] == "om_final"


def test_status_upsert_then_get():
    with isolated_env():
        assert local_facts.get_status("a") is None
        local_facts.upsert_status("a", "进行中", "do thing")
        snap = local_facts.get_status("a")
        assert snap is not None
        assert snap["status"] == "进行中"
        assert snap["task"] == "do thing"
        assert snap["blocker"] == ""

        # update overwrites
        local_facts.upsert_status("a", "已完成", "done", blocker="")
        snap = local_facts.get_status("a")
        assert snap["status"] == "已完成"


def test_log_append_then_list():
    with isolated_env():
        local_facts.append_log("a", "info", "first")
        local_facts.append_log("a", "info", "second", ref="REF-1")
        local_facts.append_log("b", "info", "other agent")
        rows = local_facts.list_logs("a")
        assert len(rows) == 2
        assert rows[0]["content"] == "first"
        assert rows[1]["content"] == "second"
        assert rows[1]["ref"] == "REF-1"


def test_log_returns_empty_when_no_log_file():
    with isolated_env():
        # never appended → no log file
        assert local_facts.list_logs("a") == []


def test_facts_dir_uses_state_dir_env():
    with isolated_env() as tmp:
        facts_dir = tmp / "state" / "facts"
        local_facts.append_message("a", "b", "x")
        assert facts_dir.exists()
        assert (facts_dir / "inbox.json").exists()


# ── heartbeat ────────────────────────────────────────────────────


def test_touch_heartbeat_records_now_for_agent():
    with isolated_env():
        local_facts.touch_heartbeat("worker")
        ts = local_facts.get_heartbeat("worker")
        assert ts is not None and ts > 0


def test_touch_heartbeat_overwrites_previous_timestamp():
    with isolated_env():
        local_facts.touch_heartbeat("w")
        first = local_facts.get_heartbeat("w")
        time.sleep(0.01)
        local_facts.touch_heartbeat("w")
        second = local_facts.get_heartbeat("w")
        assert second >= first


def test_touch_heartbeat_skips_blank_agent():
    with isolated_env():
        local_facts.touch_heartbeat("")
        assert local_facts.all_heartbeats() == {}


def test_all_heartbeats_returns_each_recorded_agent():
    with isolated_env():
        local_facts.touch_heartbeat("alice")
        local_facts.touch_heartbeat("bob")
        beats = local_facts.all_heartbeats()
        assert set(beats) == {"alice", "bob"}


def test_get_heartbeat_returns_none_for_unknown_agent():
    with isolated_env():
        assert local_facts.get_heartbeat("ghost") is None


def test_touch_heartbeat_swallows_oserror_so_callers_dont_die():
    """REGRESSION: touch_heartbeat is called early in send/inbox/log/say/
    status. A disk-full OSError there shouldn't kill those commands —
    heartbeat is auxiliary, the underlying message/log/status update is
    the actual user intent. Verify the swallow path."""
    import io
    import contextlib
    from helpers import attr_patch
    from claudeteam.store import local_facts as lf

    def boom(*a, **kw):
        raise OSError("[Errno 28] No space left on device")

    err = io.StringIO()
    with isolated_env(), attr_patch(lf, write_json=boom), \
            contextlib.redirect_stderr(err):
        # Should NOT raise — caller continues
        local_facts.touch_heartbeat("alice")

    # Warning was logged so the operator knows heartbeat is broken
    assert "heartbeat write failed" in err.getvalue()
    assert "alice" in err.getvalue()
