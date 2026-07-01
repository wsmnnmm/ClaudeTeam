"""`claudeteam down` — opposite of `up`: stop daemons + tear down tmux.

Order matters: kill daemons first (so the watchdog doesn't respawn the
router we just killed), then kill tmux. Pid files get unlinked once the
process is confirmed dead.

Always best-effort — a missing pid file or already-dead process does
not raise. Returns 0 unless something we expected to be alive refused
to die.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from claudeteam.runtime import config, paths, pidlock, tmux, watchdog
from claudeteam.util import error_exit, maybe_print_help, warn


def _stop_pid(name: str, pid: int, *, pid_file: Path | None = None) -> int:
    def _cleanup_pid_file():
        if pid_file is not None:
            pid_file.unlink(missing_ok=True)

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"⏭  {name}: pid {pid} already dead")
        _cleanup_pid_file()
        return 0
    except PermissionError as e:
        return error_exit(f"❌ {name}: not allowed to kill pid {pid}: {e}")

    # SIGTERM grace, then escalate to SIGKILL. 3s wasn't enough for
    # router/watchdog mid-lark-cli to flush — 10s catches the slow
    # path; SIGKILL fallback guarantees `compose down` doesn't punt to
    # the operator.
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"🛑 {name}: pid {pid} stopped")
            _cleanup_pid_file()
            return 0
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        print(f"🛑 {name}: pid {pid} stopped")
        _cleanup_pid_file()
        return 0
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"🛑 {name}: pid {pid} stopped (after SIGKILL)")
            _cleanup_pid_file()
            return 0
        time.sleep(0.1)
    return error_exit(
        f"⚠️  {name}: pid {pid} still alive after 12s SIGTERM+SIGKILL — investigate manually")


def _read_proc_environ(pid: int) -> dict[str, str]:
    path = f"/proc/{pid}/environ"
    out: dict[str, str] = {}
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return out
    for entry in raw.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        out[key.decode("utf-8", errors="ignore")] = value.decode(
            "utf-8", errors="ignore")
    return out


def _matches_daemon_command(command: str, name: str) -> bool:
    command = str(command or "")
    return (
        f"claudeteam.cli {name}" in command
        or f"claudeteam {name}" in command
    )


def _matching_orphan_daemon_pids(name: str, *,
                                 run=subprocess.run,
                                 readlink=os.readlink,
                                 read_environ=_read_proc_environ) -> list[int]:
    state_dir = str(paths.state_dir().resolve())
    config_file = str(paths.config_file().resolve())
    cwd = str(Path.cwd().resolve())
    me = os.getpid()
    try:
        result = run(["ps", "-eo", "pid,command"],
                     capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, AttributeError):
        return []
    if result is None or result.returncode != 0:
        return []

    out: list[int] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == me:
            continue
        command = parts[1]
        if not _matches_daemon_command(command, name):
            continue
        env = read_environ(pid)
        if env.get("CLAUDETEAM_STATE_DIR", "").strip() == state_dir:
            out.append(pid)
            continue
        if env.get("CLAUDETEAM_CONFIG_FILE", "").strip() == config_file:
            out.append(pid)
            continue
        try:
            proc_cwd = str(Path(readlink(f"/proc/{pid}/cwd")).resolve())
        except OSError:
            proc_cwd = ""
        if proc_cwd == cwd:
            out.append(pid)
    return out


def _kill_orphan_daemon_processes(name: str) -> int:
    rc = 0
    pids = _matching_orphan_daemon_pids(name)
    for pid in pids:
        rc |= _stop_pid(f"{name} orphan", pid)
    return rc


def _kill_pid_file(name: str, pid_file) -> int:
    if not pid_file.exists():
        rc = _kill_orphan_daemon_processes(name)
        if rc == 0 and not _matching_orphan_daemon_pids(name):
            print(f"⏭  {name}: no pid file")
        return rc
    pid = pidlock.read_pid(pid_file)
    if pid is None:
        print(f"⏭  {name}: corrupt pid file, removing")
        pid_file.unlink(missing_ok=True)
        return _kill_orphan_daemon_processes(name)
    rc = _stop_pid(name, pid, pid_file=pid_file)
    if rc == 0:
        rc |= _kill_orphan_daemon_processes(name)
    return rc


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam down"):
        return 0

    rc = 0
    # Kill in reverse-of-startup order so the watchdog can't respawn
    # the router we just killed. all_known_specs is router-then-watchdog;
    # reversed → watchdog first.
    for spec in reversed(watchdog.all_known_specs()):
        rc |= _kill_pid_file(spec.name, spec.pid_file)

    session = config.session_name()
    if tmux.has_session(session):
        if tmux.kill_session(session):
            print(f"🛑 tmux session {session} killed")
        else:
            warn(f"⚠️  failed to kill tmux session {session}")
            rc |= 1
    else:
        print(f"⏭  tmux session {session} not running")

    print("✅ team down" if rc == 0 else "⚠️  team down with warnings")
    return rc
