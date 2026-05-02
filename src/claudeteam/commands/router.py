"""`claudeteam router`

Long-running event subscriber: spawns `npx @larksuite/cli event +subscribe`
and feeds each NDJSON line into the routing loop.

Stops on Ctrl-C or when lark-cli exits.  Writes its PID to
state_dir/router.pid so the watchdog can supervise.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

from claudeteam.feishu import catchup, lark
from claudeteam.feishu.deliver import apply as _deliver_apply
from claudeteam.feishu.subscribe import process_lines
from claudeteam.runtime import config, paths, pidlock, wake
from claudeteam.util import error_exit, help_requested, warn


def _build_subscribe_cmd(profile: str) -> list[str]:
    return [
        "npx", "@larksuite/cli",
        *(["--profile", profile] if profile else []),
        "event", "+subscribe",
        "--event-types", "im.message.receive_v1",
        "--compact", "--quiet", "--force",
        "--as", "bot",
    ]


def _apply_with_wake(decision):
    """Production deliver wrapper: lazy-wake panes before injecting."""
    return _deliver_apply(decision, wake_fn=wake.wake_if_dormant)


def _terminate_subscribe_group(proc: subprocess.Popen) -> None:
    """Kill the entire subscribe process group (npx + node + lark-cli).

    Round 7 D2: router's plain proc.terminate() only signaled npx; the
    lark-cli grandchild lived on as an orphan after each up/down cycle.
    Putting the subprocess in its own session (start_new_session=True at
    Popen time) means we can take the whole group out with one killpg.
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _on_progress(decision, stats):
    """After each handled event, advance the catchup cursor."""
    catchup.record_decision(decision)


def main(argv: list[str]) -> int:
    if help_requested(argv):
        print("usage: claudeteam router")
        return 0

    chat = config.chat_id()
    if not chat:
        return error_exit("❌ chat_id not set in runtime_config.json")

    agents = config.agent_names()
    if not agents:
        return error_exit("❌ team.json has no agents")

    pid_file = paths.router_pid_file()
    if not pidlock.acquire(pid_file, name="router"):
        return 1

    profile = config.lark_profile()
    cmd = _build_subscribe_cmd(profile)
    print(f"🚀 router subscribing on chat {chat} (profile={profile or '<default>'})")

    try:
        # Two precautions on the subscribe child:
        # - env=lark.subprocess_env() strips HTTPS_PROXY under LARK_CLI_NO_PROXY=1
        #   (round 6 D-class bug — lark-cli long-poll dies behind a proxy).
        # - start_new_session=True puts the npx → node → lark-cli chain in its
        #   own process group so SIGTERMing the router can kill the whole tree
        #   in one killpg call (round 7 D2 — orphaned grandchildren).
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
            env=lark.subprocess_env(),
            start_new_session=True,
        )
    except FileNotFoundError:
        pidlock.release(pid_file)
        return error_exit("❌ npx / lark-cli not found in PATH")

    # Now that proc exists, install a SIGTERM handler that reaps the
    # subscribe group before exiting. (Plain sys.exit propagates SystemExit
    # past the except blocks, never running proc.terminate.)
    def _on_sigterm(*_):
        _terminate_subscribe_group(proc)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        if proc.stdout is None:
            return error_exit("❌ lark-cli started without stdout pipe")

        loop_kwargs = dict(
            team_agents=agents,
            chat_id=chat,
            default_target="manager",
            apply_fn=_apply_with_wake,
            on_progress=_on_progress,
        )

        # Catchup: replay anything newer than the cursor before going live
        try:
            pending = catchup.pending_lines(chat, profile=profile)
        except Exception as e:
            warn(f"⚠️  catchup fetch failed: {e}")
            pending = []
        if pending:
            print(f"📥 catching up {len(pending)} missed message(s)")
            process_lines(iter(pending), **loop_kwargs)

        stats = process_lines(proc.stdout, **loop_kwargs)
        print(f"router exited: handled={stats.handled} dropped={stats.dropped}")
        return 0 if proc.wait() == 0 else 1
    except KeyboardInterrupt:
        print("router stopped (Ctrl-C)")
        return 0
    finally:
        # Reap the subscribe tree on EVERY exit path so we don't leak a
        # node + lark-cli pair per up/down cycle.
        _terminate_subscribe_group(proc)
        pidlock.release(pid_file)
