"""Single-instance pid file lock for daemon commands.

Both `claudeteam router` and `claudeteam watchdog` need to:
  - claim a per-daemon pid file under $CLAUDETEAM_STATE_DIR
  - refuse to start if another live process already holds it
  - silently overwrite a stale lock left by a crashed previous run
  - clean up the lock on graceful exit (only if we still own it)

Watchdog already had this; router was just unconditionally overwriting,
which let two routers race their inserts into Feishu's event stream.
Hoisting unifies behavior and removes ~30 lines of dup.
"""
from __future__ import annotations

import os
from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.util import warn


def read_pid(pid_file: Path) -> int | None:
    """Parse `pid_file` as an integer. Returns None when the file is
    missing, unreadable, or contains non-int content.

    Used wherever code needs "the pid that owns this file, if any" —
    `acquire` here, `watchdog.is_alive`, `commands/down._kill_pid_file`,
    `commands/health._check_daemon`. Centralised so any future tweak
    (e.g. trimming a pid+timestamp format) lands in one place.
    """
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """True if `pid` exists and we can signal it (kill 0).

    OSError covers ProcessLookupError (no such pid), PermissionError
    (not ours — but daemons here are always owned by the same user
    so this rarely fires), and other variants. Either way: not usable.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire(pid_file: Path, *, name: str = "") -> bool:
    """Claim `pid_file` for the current process.

    Returns True on success. Returns False if another **live** process
    already owns the file — prints to stderr in that case. Stale locks
    (pid file present but the recorded pid is dead) are quietly
    overwritten on the assumption a previous run crashed.
    """
    if pid_file.exists():
        old = read_pid(pid_file)
        if old is not None and pid_alive(old):
            warn(f"❌ another {name or 'instance'} already running (pid {old})")
            return False
        # else: missing-or-corrupt pid file, or stale lock from a dead
        # previous run — quietly overwrite below.
    paths.ensure_state_dir()
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release(pid_file: Path) -> None:
    """Remove `pid_file` if it currently records our pid. Best-effort —
    swallows any I/O exception since this runs in a `finally` clause."""
    try:
        if (pid_file.exists()
                and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())):
            pid_file.unlink()
    except Exception:
        pass
