"""Tests for the cross-track collaboration store.

One JSON file per team: `$CLAUDETEAM_STATE_DIR/cross-track.json`
"""
from __future__ import annotations

from claudeteam.store import cross_track as ct
from helpers import isolated_env


def test_create_returns_valid_track_id_and_stores_data():
    with isolated_env():
        tid = ct.create(
            partner_team="todo002_cloud",
            partner_label="TODO002 Cloud",
            topic="Strategy Package",
            source_agent="product_lab_manager",
            target_agent="manager",
            initial_message="Please produce a strategy packet",
        )
        assert tid.startswith("XT-")
        t = ct.get(tid)
        assert t is not None
        assert t["direction"] == "outbound"
        assert t["partner_team"] == "todo002_cloud"
        assert t["partner_label"] == "TODO002 Cloud"
        assert t["topic"] == "Strategy Package"
        assert t["source_agent"] == "product_lab_manager"
        assert t["target_agent"] == "manager"
        assert t["status"] == "pending"
        assert len(t["message_history"]) == 1
        assert t["message_history"][0]["content"] == "Please produce a strategy packet"
        assert t["message_history"][0]["direction"] == "out"


def test_create_raises_without_partner_team():
    with isolated_env():
        try:
            ct.create(partner_team="", partner_label="x")
        except ValueError as e:
            assert "partner_team" in str(e)
        else:
            assert False, "expected ValueError"


def test_get_returns_none_on_miss():
    with isolated_env():
        assert ct.get("XT-nonexistent") is None


def test_transition_pending_to_accepted():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        assert ct.transition(tid, "accepted", message="Got it")
        t = ct.get(tid)
        assert t["status"] == "accepted"
        assert len(t["message_history"]) == 1
        assert t["message_history"][0]["content"] == "Got it"


def test_transition_full_cycle_to_completed():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        assert ct.transition(tid, "accepted")
        assert ct.transition(tid, "in_progress", message="Working on it")
        assert ct.transition(tid, "delivering", message="Done", artifact="outcome.json")
        assert ct.transition(tid, "completed", message="Accepted, loop closed")
        t = ct.get(tid)
        assert t["status"] == "completed"
        assert t["artifact"] == "outcome.json"
        assert t["completed_at"] is not None
        assert len(t["message_history"]) == 3


def test_transition_invalid_raises():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        try:
            ct.transition(tid, "delivering")
        except ValueError as e:
            assert "pending" in str(e)
            assert "delivering" in str(e)
        else:
            assert False, "expected ValueError"


def test_reject_from_pending():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        assert ct.transition(tid, "rejected", message="Not now")
        t = ct.get(tid)
        assert t["status"] == "rejected"
        assert t["completed_at"] is not None


def test_cancel_from_pending():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        assert ct.transition(tid, "cancelled", message="No longer needed")
        t = ct.get(tid)
        assert t["status"] == "cancelled"


def test_terminal_status_cannot_transition():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        ct.transition(tid, "rejected")
        try:
            ct.transition(tid, "accepted")
        except ValueError:
            pass
        else:
            assert False, "expected ValueError"


def test_accept_creates_inbound_entry():
    with isolated_env():
        tid = "XT-9999000000-abc123"
        result = ct.accept(
            tid,
            message="Accepted, will process",
            source_agent="manager",
            partner_task_id="T-5",
        )
        assert result == tid
        t = ct.get(tid)
        assert t is not None
        assert t["direction"] == "inbound"
        assert t["status"] == "accepted"
        assert t["source_agent"] == "manager"
        assert t["local_task_id"] == "T-5"
        assert len(t["message_history"]) == 1
        assert t["message_history"][0]["content"] == "Accepted, will process"


def test_accept_on_existing_outbound_transitions():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        result = ct.accept(tid, message="Accepted by partner", partner_task_id="T-10")
        assert result == tid
        t = ct.get(tid)
        assert t["status"] == "accepted"
        assert t["partner_task_id"] == "T-10"
        assert len(t["message_history"]) == 1
        assert t["message_history"][0]["content"] == "Accepted by partner"


def test_list_tracks_filters_by_direction():
    with isolated_env():
        out_id = ct.create(partner_team="team_a")
        in_id = "XT-8888000000-xyz789"
        ct.accept(in_id, source_agent="manager")

        out_tracks = ct.list_tracks(direction="outbound")
        in_tracks = ct.list_tracks(direction="inbound")

        assert any(t["track_id"] == out_id for t in out_tracks)
        assert any(t["track_id"] == in_id for t in in_tracks)
        assert not any(t["track_id"] == out_id for t in in_tracks)


def test_list_tracks_filters_by_status():
    with isolated_env():
        ct.create(partner_team="team_a")
        tid2 = ct.create(partner_team="team_b")
        ct.transition(tid2, "accepted")

        pending = ct.list_tracks(status="pending")
        accepted = ct.list_tracks(status="accepted")

        assert all(t["status"] == "pending" for t in pending)
        assert all(t["status"] == "accepted" for t in accepted)


def test_list_tracks_filters_by_partner_team():
    with isolated_env():
        ct.create(partner_team="Alpha")
        ct.create(partner_team="Beta")
        ct.create(partner_team="beta")  # casefold match

        results = ct.list_tracks(partner_team="beta")
        assert len(results) == 2
        for t in results:
            assert t["partner_team"].casefold() == "beta"


def test_count_active_excludes_terminal():
    with isolated_env():
        a = ct.create(partner_team="x")
        b = ct.create(partner_team="y")
        ct.transition(b, "rejected")
        c = ct.create(partner_team="z")
        ct.transition(c, "accepted")
        ct.transition(c, "in_progress")
        ct.transition(c, "delivering", artifact="x.json")
        ct.transition(c, "completed")

        assert ct.count_active() == 1  # only track a is still pending
        assert ct.count_active(direction="outbound") == 1


def test_add_message_appends_without_changing_status():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        assert ct.add_message(tid, "out", "Update: still working")
        t = ct.get(tid)
        assert t["status"] == "pending"
        assert len(t["message_history"]) == 1
        assert t["message_history"][0]["content"] == "Update: still working"


def test_add_message_returns_false_on_miss():
    with isolated_env():
        assert ct.add_message("XT-missing", "in", "x") is False


def test_partner_label_for_returns_label():
    with isolated_env():
        tid = ct.create(partner_team="team_x", partner_label="Team X Label")
        assert ct.partner_label_for(tid) == "Team X Label"


def test_partner_label_for_returns_empty_on_miss():
    with isolated_env():
        assert ct.partner_label_for("XT-missing") == ""


def test_transition_returns_false_on_miss():
    with isolated_env():
        assert ct.transition("XT-missing", "accepted") is False


def test_message_history_direction_tracks_flow():
    with isolated_env():
        tid = ct.create(partner_team="team_b", initial_message="init out")
        ct.transition(tid, "accepted", message="got it", direction="in")
        ct.transition(tid, "in_progress", message="working", direction="out")
        t = ct.get(tid)
        dirs = [m["direction"] for m in t["message_history"]]
        assert dirs == ["out", "in", "out"]
