"""Tests for runtime/wake.py — lazy wake of dormant CLI panes."""
from __future__ import annotations

from helpers import attr_patch
from claudeteam.runtime import wake, tmux


class _ClaudeFake:
    """Minimal CliAdapter stand-in for tests."""
    def ready_markers(self):
        return ["bypass permissions on", "? for shortcuts"]

    def busy_markers(self):
        return []

    def process_name(self):
        return "claude"


class _CodexFake:
    """Minimal Codex adapter stand-in for tests."""
    def ready_markers(self):
        return [" high · "]

    def busy_markers(self):
        return ["Starting MCP servers"]

    def process_name(self):
        return "codex"


class _NoRenudgeFake(_ClaudeFake):
    def resubmit_on_idle(self):
        return False


def _capturer(text_per_call: list[str]):
    """Return a capture_pane fake that yields one text per call."""
    iterator = iter(text_per_call)

    def fake(target, lines=80):
        try:
            return next(iterator)
        except StopIteration:
            return ""
    return fake


# ── is_ready ─────────────────────────────────────────────────────


def test_is_ready_true_when_pane_shows_marker():
    target = tmux.Target("S", "manager")
    capture = _capturer(["welcome\nbypass permissions on\n>"])
    assert wake.is_ready(target, _ClaudeFake(), capture=capture) is True


# ── inject_and_confirm ────────────────


def test_inject_and_confirm_returns_when_pane_is_moving():
    """If the pane is MOVING after the inject (streaming a reply = submitted),
    confirm without re-nudging. Motion, not a busy-marker string."""
    injects = []
    sends = []
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _ClaudeFake(), "hello",
        inject=lambda t, text, *, submit_keys=None: injects.append(text) or True,
        send_keys=lambda t, *k: sends.append(k),
        # settle sees a static pane first, THEN the confirm sees motion
        capture=_capturer(["calm", "calm", "frame-a", "frame-b"]),
        sleep=lambda s: None,
    )
    assert ok is True
    assert injects == ["hello"]   # injected once
    assert sends == []            # confirmed by motion; never re-nudged


def test_inject_and_confirm_renudges_then_confirms_motion():
    """Static after the inject (submit dropped) → re-send the primary key,
    then the pane starts moving = submitted."""
    sends = []
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _ClaudeFake(), "hi",
        inject=lambda t, text, *, submit_keys=None: True,
        send_keys=lambda t, *k: sends.append(k),
        # settle (calm, calm); 1st check: static (unsubmitted); after re-nudge: moving
        capture=_capturer(["calm", "calm", "idle", "idle", "resp-1", "resp-2"]),
        sleep=lambda s: None,
    )
    assert ok is True
    assert sends == [("Enter",)]   # re-nudged once with submit_keys[0]


def test_inject_and_confirm_not_fooled_by_startup_banner_motion():
    """Regression (codex first-wake): a pane still animating its startup banner
    must NOT be read as 'submitted'. The pre-inject settle absorbs the banner
    motion; the post-inject static then correctly triggers a re-nudge — instead
    of the banner redraw faking a successful submit and leaving the prompt
    unsent (the '♥ never / initializing' bug)."""
    sends = []
    captures = [
        "banner-1", "banner-2",   # settle: banner still animating...
        "stable",   "stable",     # settle: quiesced → safe to inject
        "stable",   "stable",     # post-inject confirm: static → submit was eaten
        "resp-1",   "resp-2",     # after the re-nudge: moving = submitted
    ]
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _ClaudeFake(), "hi",
        inject=lambda t, text, *, submit_keys=None: True,
        send_keys=lambda t, *k: sends.append(k),
        capture=_capturer(captures),
        sleep=lambda s: None,
    )
    assert ok is True
    assert sends == [("Enter",)]   # re-nudged once — not fooled by banner motion


def test_inject_and_confirm_optout_adapter_injects_once_no_renudge():
    """A CLI that opts out (resubmit_on_idle False, e.g. kimi) gets a plain
    single inject — never a re-nudge keypress (the re-sent key would be
    misread as an interrupt)."""
    injects = []
    sends = []
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _NoRenudgeFake(), "hi",
        inject=lambda t, text, *, submit_keys=None: injects.append(text) or True,
        send_keys=lambda t, *k: sends.append(k),
        capture=_capturer(["x", "x"]),
        sleep=lambda s: None,
    )
    assert ok is True
    assert injects == ["hi"]   # injected exactly once
    assert sends == []         # NEVER re-nudged (no interrupt risk)


def test_inject_and_confirm_gives_up_after_attempts():
    """Pane never moves (nothing submitted) → escalate `attempts` times then
    return False. The text was still injected, so no worse than a plain
    inject."""
    sends = []
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _ClaudeFake(), "hi",
        attempts=2,
        inject=lambda t, text, *, submit_keys=None: True,
        send_keys=lambda t, *k: sends.append(k),
        capture=_capturer(["static"] * 8),   # never moves → never confirms
        sleep=lambda s: None,
    )
    assert ok is False
    assert len(sends) == 2   # escalated exactly `attempts` times


def test_is_ready_false_when_ready_marker_is_still_busy():
    target = tmux.Target("S", "worker_codex")
    capture = _capturer(["Starting MCP servers (0/2)\n\n  gpt-5.2 high · /work"])
    assert wake.is_ready(target, _CodexFake(), capture=capture) is False


# ── wake_if_dormant ──────────────────────────────────────────────


def test_wake_returns_true_when_already_ready_no_spawn():
    target = tmux.Target("S", "manager")
    capture = _capturer(["bypass permissions on\n>"])
    spawn_calls = []
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude --foo",
        capture=capture,
        spawn=lambda t, c: spawn_calls.append((str(t), c)) or True,
        is_retired=lambda t: False,
        sleep=lambda s: None,
    )
    assert ok is True
    assert spawn_calls == []


def test_wake_spawns_and_polls_until_ready():
    target = tmux.Target("S", "worker")
    # First check: dormant. Second check (post-spawn): still loading.
    # Third check: ready.
    captures = ["$ ", "$ loading...", "bypass permissions on\n>"]
    capture = _capturer(captures)
    spawn_calls = []
    sleeps = []
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: spawn_calls.append(c) or True,
        is_retired=lambda t: False,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert spawn_calls == ["claude"]
    assert len(sleeps) == 2  # slept twice while polling


def test_wake_returns_false_when_spawn_fails():
    target = tmux.Target("S", "worker")
    capture = _capturer(["$ "])
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: False,
        is_retired=lambda t: False,
        sleep=lambda s: None,
    )
    assert ok is False


# ── wait_until_ready (no spawn — pure polling) ────────────────────


def test_wait_until_ready_returns_true_immediately_when_already_ready():
    """No-spawn poll variant: if the marker is already there on first
    capture, no sleep happens — the loop checks then exits."""
    target = tmux.Target("S", "manager")
    capture = _capturer(["bypass permissions on\n>"])
    sleeps = []
    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert sleeps == []  # ready on first check, no sleep needed


def test_wait_until_ready_polls_with_sleep_then_returns_true():
    """When the marker appears on the second capture, exactly one sleep
    fires between the two checks."""
    target = tmux.Target("S", "manager")
    capture = _capturer(["$ ", "bypass permissions on\n>"])
    sleeps = []
    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert len(sleeps) == 1


def test_wait_until_ready_ignores_codex_status_line_while_mcp_boots():
    target = tmux.Target("S", "worker_codex")
    capture = _capturer([
        "Starting MCP servers (0/2)\n\n  gpt-5.2 high · /work",
        "  gpt-5.2 high · /work",
    ])
    sleeps = []
    ok = wake.wait_until_ready(
        target, _CodexFake(), capture=capture,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert len(sleeps) == 1


def test_wait_until_ready_returns_false_on_timeout():
    """Marker never appears — function returns False after the deadline.
    Uses a fake clock so the test doesn't actually sleep through 20s."""
    target = tmux.Target("S", "manager")
    capture = lambda t, lines=80: "$ "  # always dormant
    clock = {"t": 0.0}

    def now():
        clock["t"] += 0.5
        return clock["t"]

    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: None, now=now,
        timeout_s=1.0, poll_interval_s=0.1,
    )
    assert ok is False


def test_wake_returns_false_on_timeout():
    target = tmux.Target("S", "worker")
    # always dormant
    capture = lambda t, lines=80: "$ "
    # fake clock: each call advances by 0.5s; deadline is 1.0s.
    clock = {"t": 0.0}

    def now():
        clock["t"] += 0.5
        return clock["t"]

    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: True,
        is_retired=lambda t: False,
        sleep=lambda s: None,
        now=now,
        timeout_s=1.0, poll_interval_s=0.1,
    )
    assert ok is False


def test_wait_until_ready_accepts_bypass_warning_with_literal_two():
    target = tmux.Target("S", "manager")
    capture = _capturer([
        "WARNING...\n1. No, exit\n2. Yes, I accept\nEnter to confirm · Esc to cancel",
        "bypass permissions on\n>",
    ])
    sent_text = []
    sent_keys = []
    with __import__("contextlib").ExitStack() as stack:
        from helpers import attr_patch
        stack.enter_context(attr_patch(
            tmux,
            send_text=lambda t, text, run=None: sent_text.append((str(t), text)) or True,
            send_keys=lambda t, *keys, run=None: sent_keys.append((str(t), keys)) or True,
        ))
        ok = wake.wait_until_ready(
            target, _ClaudeFake(), capture=capture,
            sleep=lambda s: None,
            timeout_s=5.0, poll_interval_s=0.1,
        )
    assert ok is True
    assert sent_text == [("S:manager", "2")]
    assert sent_keys == []


def test_wait_until_ready_accepts_focus_cycled_bypass_warning():
    target = tmux.Target("S", "manager")
    capture = _capturer([
        "Bypass Permissions mode\nYes, I accept\nEnter to confirm\n"
        "shift+tab to cycle",
        "bypass permissions on\n>",
    ])
    sent_text = []
    sent_keys = []
    with __import__("contextlib").ExitStack() as stack:
        from helpers import attr_patch
        stack.enter_context(attr_patch(
            tmux,
            send_text=lambda t, text, run=None: sent_text.append((str(t), text)) or True,
            send_keys=lambda t, *keys, run=None: sent_keys.append((str(t), keys)) or True,
        ))
        ok = wake.wait_until_ready(
            target, _ClaudeFake(), capture=capture,
            sleep=lambda s: None,
            timeout_s=5.0, poll_interval_s=0.1,
        )
    assert ok is True
    assert sent_text == []
    assert sent_keys == [("S:manager", ("BTab", "Enter"))]


def test_wait_until_ready_keeps_existing_codex_model_on_upgrade_prompt():
    target = tmux.Target("S", "worker_frontend")
    capture = _capturer([
        "Introducing GPT-5.4\n"
        "› 1. Try new model\n"
        "  2. Use existing model\n",
        "gpt-5.3-codex high · ~/Project/work-assistant-team",
    ])
    sent_text = []
    sent_keys = []
    with __import__("contextlib").ExitStack() as stack:
        from helpers import attr_patch
        stack.enter_context(attr_patch(
            tmux,
            send_text=lambda t, text, run=None: sent_text.append((str(t), text)) or True,
            send_keys=lambda t, *keys, run=None: sent_keys.append((str(t), keys)) or True,
        ))
        ok = wake.wait_until_ready(
            target, _CodexFake(), capture=capture,
            sleep=lambda s: None,
            timeout_s=5.0, poll_interval_s=0.1,
        )
    assert ok is True
    assert sent_text == []
    assert sent_keys == [("S:worker_frontend", ("Down", "Enter"))]


def test_wake_bootstraps_claude_home_before_first_lazy_spawn():
    target = tmux.Target("S", "worker_research")
    capture = _capturer(["$ ", "bypass permissions on\n>"])
    spawn_calls = []
    bootstrapped = []
    with attr_patch(
        __import__("claudeteam.runtime.lifecycle", fromlist=["_ensure_claude_agent_home"]),
        _ensure_claude_agent_home=lambda agent: bootstrapped.append(agent),
    ):
        ok = wake.wake_if_dormant(
            target, _ClaudeFake(), spawn_cmd="claude",
            capture=capture,
            spawn=lambda t, c: spawn_calls.append(c) or True,
            sleep=lambda s: None,
            timeout_s=5.0, poll_interval_s=0.1,
        )
    assert ok is True
    assert bootstrapped == ["worker_research"]
    assert spawn_calls == ["claude"]
