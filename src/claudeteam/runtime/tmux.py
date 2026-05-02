"""Thin tmux wrapper: capture pane, inject text, manage windows.

Functions take an optional `run=` callable so tests can inject a fake
subprocess runner.  Production callers leave it default (subprocess.run).

Deliberately leaves out the old tmux_utils.py heavy bits (pane-diff idle
classification, `detect_unsubmitted_input_text`, `force_anyway` queue
escalation).  Those land when a concrete consumer needs them.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Target:
    session: str
    window: str

    def __str__(self) -> str:
        return f"{self.session}:{self.window}"


def _default_run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=10, **kwargs)


def has_session(session: str, *, run: Callable = _default_run) -> bool:
    r = run(["tmux", "has-session", "-t", session])
    return r.returncode == 0


def has_window(target: Target, *, run: Callable = _default_run) -> bool:
    r = run(["tmux", "has-session", "-t", str(target)])
    return r.returncode == 0


def capture_pane(target: Target, *, lines: int = 80, run: Callable = _default_run) -> str:
    r = run(["tmux", "capture-pane", "-t", str(target), "-p", "-S", f"-{lines}"])
    return r.stdout if r.returncode == 0 else ""


def new_session(session: str, *, window: str = "manager",
                detached: bool = True, run: Callable = _default_run) -> bool:
    args = ["tmux", "new-session"]
    if detached:
        args += ["-d"]
    args += ["-s", session, "-n", window]
    return run(args).returncode == 0


def new_window(target: Target, *, run: Callable = _default_run) -> bool:
    r = run(["tmux", "new-window", "-t", target.session, "-n", target.window])
    return r.returncode == 0


def send_text(target: Target, text: str, *, run: Callable = _default_run) -> bool:
    """Send literal text (no key interpretation) to a pane.

    Uses `send-keys -l` so $/`/# don't get expanded by tmux.
    """
    r = run(["tmux", "send-keys", "-l", "-t", str(target), text])
    return r.returncode == 0


def send_keys(target: Target, *keys: str, run: Callable = _default_run) -> bool:
    """Send named keys (Enter, M-Enter, C-c, ...) to a pane."""
    r = run(["tmux", "send-keys", "-t", str(target), *keys])
    return r.returncode == 0


def inject(target: Target, text: str, *, submit_keys: list[str] | None = None,
           settle_ms: int = 200, sleep: Callable = time.sleep,
           run: Callable = _default_run) -> bool:
    """Send `text` into the pane and submit it.

    Tries each key in `submit_keys` in order with a small settle pause.
    Returns False if any subprocess call fails — callers can retry or
    surface the error.
    """
    if not send_text(target, text, run=run):
        return False
    sleep(settle_ms / 1000)
    keys = submit_keys or ["Enter", "C-m", "C-j"]
    for key in keys:
        if not send_keys(target, key, run=run):
            return False
        sleep(settle_ms / 1000)
    return True


def spawn_agent(target: Target, spawn_cmd: str, *,
                run: Callable = _default_run) -> bool:
    """Drop a CLI spawn command into a pane and press Enter to start it."""
    if not send_text(target, spawn_cmd, run=run):
        return False
    return send_keys(target, "Enter", run=run)


def pane_has_marker(target: Target, markers: list[str], *,
                    lines: int = 80, run: Callable = _default_run) -> bool:
    """True if any of `markers` appears in the pane's last `lines` rows."""
    if not markers:
        return False
    text = capture_pane(target, lines=lines, run=run)
    return any(m in text for m in markers)
