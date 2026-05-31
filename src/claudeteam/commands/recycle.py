"""`claudeteam recycle <agent> [<agent> ...]`

Restart one or more agent panes in-place so they pick up the latest
provider/model routing without tearing down the whole tmux session.

Typical use:

    claudeteam switch model preset --use zyapi-backup
    claudeteam recycle manager worker_frontend

That keeps the rest of the team running while the selected panes are
recreated under the new preset.
"""
from __future__ import annotations

from claudeteam.runtime import config, lifecycle, tmux
from claudeteam.util import error_exit, maybe_print_help, usage_error, warn


USAGE = "usage: claudeteam recycle <agent> [<agent> ...]"


def _render_outcome(agent: str, cli: str, target: tmux.Target, outcome: str) -> int:
    if outcome == lifecycle.LAZY:
        print(f"♻️  recycled (lazy): {agent} ({cli}) → {target}")
        return 0
    if outcome == lifecycle.CONFIG_ERROR:
        return error_exit(
            f"❌ {agent}: bad cli config in team.json (see warning above); "
            "recycle aborted, fix team.json and retry")
    if outcome == lifecycle.SPAWN_FAILED:
        return error_exit(f"❌ failed to spawn CLI in {agent} pane")
    if outcome == lifecycle.READY_NO_INIT:
        warn(
            f"⚠️  {agent} CLI didn't show ready marker in time; "
            "identity init prompt skipped")
        print(f"♻️  recycled: {agent} ({cli}) → {target} (no init)")
        return 0
    print(f"♻️  recycled: {agent} ({cli}) → {target}")
    return 0


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    if not argv:
        return usage_error(USAGE)

    session = config.session_name()
    if not tmux.has_session(session):
        return error_exit(
            f"❌ tmux session {session} not running; run `claudeteam start` first")

    rc = 0
    seen: set[str] = set()
    for agent in argv:
        if agent in seen:
            continue
        seen.add(agent)
        try:
            cfg = config.agent_config(agent)
        except KeyError:
            rc |= error_exit(f"❌ unknown agent: {agent} (not in team.json)")
            continue
        cli = cfg.get("cli", "claude-code")
        target = tmux.Target(session, agent)
        if tmux.has_window(target):
            tmux.send_keys(target, "C-c")
            if not tmux.kill_window(target):
                rc |= error_exit(f"❌ failed to kill pane for {agent}")
                continue
        else:
            print(f"⏭  {agent}: no existing pane, creating fresh window")
        if not tmux.new_window(target):
            rc |= error_exit(f"❌ failed to create window for {agent}")
            continue
        outcome = lifecycle.provision_pane(agent, target)
        rc |= _render_outcome(agent, cli, target, outcome)
    return rc
