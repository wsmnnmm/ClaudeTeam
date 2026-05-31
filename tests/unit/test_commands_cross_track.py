"""Tests for `claudeteam cross-track` command.

Stubs cross-send side effects so we can test the state machine in isolation.
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.commands import cross_track as cmd
from claudeteam.store import cross_track as ct
from helpers import isolated_env, attr_patch


# ── stubs ────────────────────────────────────────────────────────────

def _stub_cross_send_main(argv):
    """Return success without actually calling cross-send."""
    return 0


def _stub_send_ack(*args, **kwargs):
    """No-op the return-path side effect."""
    pass


def _stub_cross_send_resolve_target(team_ref, *, root, registry_script, remote_state_dir):
    """Return a minimal TargetTeam that won't trigger real dispatch."""
    # Return None so _send_ack falls through gracefully
    return None


def _stub_resolve_target_with_label(team_ref, *, root, registry_script, remote_state_dir):
    """Return a stub target with a label."""
    from claudeteam.commands.cross_send import TargetTeam
    return TargetTeam(
        key=team_ref,
        label=team_ref.upper(),
        config_path=Path("/tmp/stub.toml"),
        team_dir=Path("/tmp"),
        remote_meta={},
    )


def _dispatch_stubs():
    return attr_patch(
        cmd._cross_send, main=_stub_cross_send_main,
        _resolve_target=_stub_resolve_target_with_label,
    )


def _noack_stubs():
    return attr_patch(cmd, _send_ack=_stub_send_ack)


# ── dispatch ─────────────────────────────────────────────────────────


def test_dispatch_creates_outbound_track_and_prints_track_id():
    with isolated_env(), _dispatch_stubs(), _noack_stubs():
        rc, out, err = _run(["dispatch", "todo002_cloud", "manager",
                             "product_lab_manager", "strategy package"])
        assert rc == 0, err
        assert "✅ Dispatched XT-" in out
        tids = ct.list_tracks(direction="outbound")
        assert len(tids) == 1
        t = tids[0]
        assert t["partner_team"] == "todo002_cloud"
        assert t["status"] == "pending"
        assert t["source_agent"] == "product_lab_manager"
        assert t["target_agent"] == "manager"


def test_dispatch_fails_with_insufficient_args():
    with isolated_env(), _dispatch_stubs(), _noack_stubs():
        rc, out, err = _run(["dispatch", "team"])
        assert rc != 0


# ── accept ───────────────────────────────────────────────────────────


def test_accept_creates_inbound_for_unknown_track():
    with isolated_env(), _noack_stubs():
        tid = "XT-9999000000-abc123"
        rc, out, err = _run(["accept", tid, "--message", "Got it, starting"])
        assert rc == 0, err
        t = ct.get(tid)
        assert t is not None
        assert t["direction"] == "inbound"
        assert t["status"] == "accepted"


def test_accept_transitions_existing_outbound():
    with isolated_env(), _noack_stubs():
        tid = ct.create(partner_team="team_b")
        rc, out, err = _run(["accept", tid])
        assert rc == 0, err
        t = ct.get(tid)
        assert t["status"] == "accepted"


def test_accept_fails_without_track_id():
    with isolated_env(), _noack_stubs():
        rc, out, err = _run(["accept"])
        assert rc != 0


# ── progress ─────────────────────────────────────────────────────────


def test_progress_transitions_to_in_progress():
    with isolated_env(), _noack_stubs():
        tid = ct.create(partner_team="team_b")
        ct.transition(tid, "accepted")
        rc, out, err = _run(["progress", tid, "--message", "Working on it"])
        assert rc == 0, err
        t = ct.get(tid)
        assert t["status"] == "in_progress"


def test_progress_on_missing_track_fails():
    with isolated_env(), _noack_stubs():
        rc, out, err = _run(["progress", "XT-missing"])
        assert rc != 0


# ── deliver ──────────────────────────────────────────────────────────


def test_deliver_with_artifact_transitions():
    with isolated_env(), _noack_stubs():
        tid = ct.create(partner_team="team_b")
        ct.transition(tid, "accepted")
        ct.transition(tid, "in_progress")
        rc, out, err = _run(["deliver", tid, "--artifact", "outcome.json",
                             "--message", "Here is the result"])
        assert rc == 0, err
        t = ct.get(tid)
        assert t["status"] == "delivering"
        assert t["artifact"] == "outcome.json"


def test_deliver_without_artifact_fails():
    with isolated_env(), _noack_stubs():
        tid = ct.create(partner_team="team_b")
        ct.transition(tid, "accepted")
        rc, out, err = _run(["deliver", tid])
        assert rc != 0
        assert "--artifact" in err or "--artifact" in out


def test_deliver_on_missing_track_fails():
    with isolated_env(), _noack_stubs():
        rc, out, err = _run(["deliver", "XT-missing", "--artifact", "x.json"])
        assert rc != 0


# ── ack ──────────────────────────────────────────────────────────────


def test_ack_completes_delivering_track():
    with isolated_env(), _noack_stubs():
        tid = ct.create(partner_team="team_b")
        ct.transition(tid, "accepted")
        ct.transition(tid, "in_progress")
        ct.transition(tid, "delivering", artifact="x.json")
        rc, out, err = _run(["ack", tid])
        assert rc == 0, err
        t = ct.get(tid)
        assert t["status"] == "completed"


def test_ack_on_non_delivering_fails():
    with isolated_env(), _noack_stubs():
        tid = ct.create(partner_team="team_b")
        rc, out, err = _run(["ack", tid])
        assert rc != 0
        assert "delivering" in err or "delivering" in out


def test_ack_on_missing_track_fails():
    with isolated_env(), _noack_stubs():
        rc, out, err = _run(["ack", "XT-missing"])
        assert rc != 0


# ── reject ───────────────────────────────────────────────────────────


def test_reject_with_reason_transitions():
    with isolated_env(), _noack_stubs():
        tid = ct.create(partner_team="team_b")
        rc, out, err = _run(["reject", tid, "--reason", "Out of scope"])
        assert rc == 0, err
        t = ct.get(tid)
        assert t["status"] == "rejected"


def test_reject_without_reason_fails():
    with isolated_env(), _noack_stubs():
        tid = ct.create(partner_team="team_b")
        rc, out, err = _run(["reject", tid])
        assert rc != 0
        assert "--reason" in err or "--reason" in out


def test_reject_on_missing_track_fails():
    with isolated_env(), _noack_stubs():
        rc, out, err = _run(["reject", "XT-missing", "--reason", "N/A"])
        assert rc != 0


# ── list ─────────────────────────────────────────────────────────────


def test_list_shows_tracks():
    with isolated_env():
        ct.create(partner_team="alpha", topic="Strategy Package")
        rc, out, err = _run(["list"])
        assert rc == 0, err
        assert "pending" in out
        assert "Strategy Package" in out


def test_list_empty_shows_placeholder():
    with isolated_env():
        rc, out, err = _run(["list"])
        assert rc == 0, err
        assert "(no cross-track entries)" in out


def test_list_filters_by_direction():
    with isolated_env():
        ct.create(partner_team="team_a")
        in_id = "XT-7777000000-inbound"
        ct.accept(in_id, source_agent="manager")
        rc_out, out, err = _run(["list", "--direction", "outbound"])
        assert rc_out == 0, err
        assert in_id not in out
        rc_in, out_in, _ = _run(["list", "--direction", "inbound"])
        assert rc_in == 0
        assert in_id in out_in


# ── show ─────────────────────────────────────────────────────────────


def test_show_displays_track_details():
    with isolated_env():
        tid = ct.create(
            partner_team="todo002_cloud", partner_label="TODO002 Cloud",
            topic="Strategy", source_agent="product_lab_manager",
            target_agent="manager", initial_message="Please produce",
        )
        rc, out, err = _run(["show", tid])
        assert rc == 0, err
        assert tid in out
        assert "outbound" in out
        assert "TODO002 Cloud" in out
        assert "Strategy" in out


def test_show_missing_track_fails():
    with isolated_env():
        rc, out, err = _run(["show", "XT-missing"])
        assert rc != 0


# ── status ───────────────────────────────────────────────────────────


def test_status_shows_active_counts():
    with isolated_env():
        a = ct.create(partner_team="x")
        b = ct.create(partner_team="y")
        ct.transition(b, "accepted")
        ct.transition(b, "in_progress")
        ct.transition(b, "delivering", artifact="x.json")
        ct.transition(b, "completed")
        rc, out, err = _run(["status"])
        assert rc == 0, err
        assert "1 active" in out
        assert "outbound" in out


def test_status_all_terminal_shows_zero_active():
    with isolated_env():
        tid = ct.create(partner_team="x")
        ct.transition(tid, "rejected")
        rc, out, err = _run(["status"])
        assert rc == 0, err
        assert "0 active" in out


# ── unknown action ───────────────────────────────────────────────────


def test_unknown_action_returns_usage_error():
    with isolated_env():
        rc, out, err = _run(["nonexistent"])
        assert rc != 0


# ── _apply_remote_action ─────────────────────────────────────────────


def test_apply_remote_action_accept_creates_inbound():
    with isolated_env():
        tid = "XT-1234000000-remote"
        cmd._apply_remote_action(tid, "accept", "Got it")
        t = ct.get(tid)
        assert t is not None
        assert t["status"] == "accepted"


def test_apply_remote_action_progress_transitions():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        ct.transition(tid, "accepted")
        cmd._apply_remote_action(tid, "progress", "Working")
        t = ct.get(tid)
        assert t["status"] == "in_progress"


def test_apply_remote_action_deliver_completes_outbound_cycle():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        ct.transition(tid, "accepted")
        ct.transition(tid, "in_progress")
        ct.transition(tid, "delivering", artifact="outcome.json")
        cmd._apply_remote_action(tid, "ack", "All good")
        t = ct.get(tid)
        assert t["status"] == "completed"


def test_apply_remote_action_reject_transitions():
    with isolated_env():
        tid = ct.create(partner_team="team_b")
        cmd._apply_remote_action(tid, "reject", "Not now")
        t = ct.get(tid)
        assert t["status"] == "rejected"


def test_apply_remote_action_strips_cross_track_markers():
    with isolated_env():
        tid = "XT-5678000000-markers"
        marked_msg = f"[cross-track: {tid}] [action: accept]\nCleaned message"
        cmd._apply_remote_action(tid, "accept", marked_msg)
        t = ct.get(tid)
        assert t is not None
        assert "Cleaned message" in t["message_history"][0]["content"]
        assert "[cross-track:" not in t["message_history"][0]["content"]


def test_apply_remote_action_unknown_action_is_noop():
    with isolated_env():
        tid = "XT-noop"
        cmd._apply_remote_action(tid, "unknown_action", "ignored")
        assert ct.get(tid) is None


# ── helper ───────────────────────────────────────────────────────────

def _run(argv: list[str]) -> tuple[int, str, str]:
    import io, contextlib
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cmd.main(argv) or 0
    return rc, out.getvalue(), err.getvalue()
