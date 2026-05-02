"""`claudeteam up` — bring the whole team alive in one shot.

Composes existing primitives:
  1. `start` — tmux session + per-agent windows + CLI spawn (or lazy)
  2. `router` (detached) — long-running event subscriber
  3. `watchdog` (detached) — supervisor that re-spawns router if it dies

Skip steps where the resource is already alive (idempotent restart).
Returns 0 if everything ends up alive, 1 if any required step failed.
"""
from __future__ import annotations

import os
import subprocess
import time

from claudeteam.commands import start as _start
from claudeteam.runtime import config, paths, tmux
from claudeteam.runtime.watchdog import is_alive, ProcessSpec
from claudeteam.util import error_exit, help_requested


def _ensure_started() -> int:
    session = config.session_name()
    if tmux.has_session(session):
        print(f"⏭  tmux session {session} already running, skipping start")
        return 0
    return _start.main([])


def _ensure_daemon(name: str, pid_file_path, spawn_argv: list[str]) -> int:
    spec = ProcessSpec(name=name, pid_file=pid_file_path,
                       expected_cmdline="claudeteam", spawn_cmd=spawn_argv)
    if is_alive(spec):
        print(f"⏭  {name} already alive, skipping")
        return 0
    try:
        subprocess.Popen(spawn_argv, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=os.environ.copy())
    except OSError as e:
        return error_exit(f"❌ failed to spawn {name}: {e}")
    # Give the daemon a beat to write its pid file
    for _ in range(20):
        if pid_file_path.exists():
            print(f"🚀 {name} launched (pid {pid_file_path.read_text().strip()})")
            return 0
        time.sleep(0.1)
    print(f"⚠️  {name} launched but no pid file yet; check `claudeteam health`")
    return 0


def main(argv: list[str]) -> int:
    if help_requested(argv):
        print("usage: claudeteam up")
        return 0

    rc = _ensure_started()
    if rc != 0:
        return rc

    rc |= _ensure_daemon("router",
                         paths.router_pid_file(),
                         ["claudeteam", "router"])
    rc |= _ensure_daemon("watchdog",
                         paths.watchdog_pid_file(),
                         ["claudeteam", "watchdog"])

    print("✅ team up — run `claudeteam health` to verify")
    return 0 if rc == 0 else 1
