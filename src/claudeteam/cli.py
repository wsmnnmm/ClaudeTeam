"""Single console-scripts entry point for the `claudeteam` command.

Subcommands are registered in COMMANDS as `name → handler(argv)` pairs.  Each
handler returns an int exit code or None (treated as 0).  This module owns
the top-level dispatch, usage text, and process exit; subcommand modules
own their own argv parsing and side effects.
"""
from __future__ import annotations

import sys
from typing import Callable

from claudeteam.commands import (
    api_budget, init, send, cross_send, cross_track, cross_learnings,
    inbox, learnings, read, status, log, team, workspace,
    start, hire, fire, recycle, up, down, reset, reidentify, switch,
    say, router, watchdog, task, topic, remember, recall, forget, peek,
    health, fleet_health, cockpit_sync, cockpit_brief, founder_os,
    boss_experience_audit, correction_cases, evolution_health,
    mentor_request, traffic_brief, usage, install_hooks, version,
)
from claudeteam.runtime.envfile import load_dotenv
from claudeteam.util import error_exit


CommandHandler = Callable[[list[str]], int | None]


# Subcommand registry, structured as ordered (group_label, [(name, fn)…])
# pairs so `claudeteam --help` can render commands in semantic groups
# instead of a flat 26-line wall. Adding a new command:
# write a module under claudeteam.commands with `main(argv)`, then
# append the (name, fn) pair into the appropriate group below.
_COMMAND_GROUPS: list[tuple[str, list[tuple[str, CommandHandler]]]] = [
    ("bootstrap", [
        ("init", init.main),
    ]),
    ("local store I/O", [
        ("send", send.main),
        ("cross-send", cross_send.main),
        ("cross-track", cross_track.main),
        ("cross-learnings", cross_learnings.main),
        ("inbox", inbox.main),
        ("read", read.main),
        ("status", status.main),
        ("log", log.main),
        ("team", team.main),
        ("workspace", workspace.main),
        ("peek", peek.main),
    ]),
    ("team lifecycle", [
        ("start", start.main),
        ("hire", hire.main),
        ("fire", fire.main),
        ("recycle", recycle.main),
        ("up", up.main),
        ("down", down.main),
        ("reset", reset.main),
        ("reidentify", reidentify.main),
        ("switch", switch.main),
    ]),
    ("feishu transport", [
        ("say", say.main),
        ("router", router.main),
    ]),
    ("supervision", [
        ("watchdog", watchdog.main),
        ("api-budget", api_budget.main),
        ("learnings", learnings.main),
    ]),
    ("task tracking", [
        ("task", task.main),
        ("topic", topic.main),
    ]),
    ("durable agent memory", [
        ("remember", remember.main),
        ("recall", recall.main),
        ("forget", forget.main),
    ]),
    ("operational", [
        ("health", health.main),
        ("fleet-health", fleet_health.main),
        ("cockpit-sync", cockpit_sync.main),
        ("cockpit-brief", cockpit_brief.main),
        ("founder-os", founder_os.main),
        ("boss-experience-audit", boss_experience_audit.main),
        ("correction-cases", correction_cases.main),
        ("evolution-health", evolution_health.main),
        ("mentor-request", mentor_request.main),
        ("traffic-brief", traffic_brief.main),
        ("usage", usage.main),
        ("install-hooks", install_hooks.main),
        ("version", version.main),
    ]),
]

# Flat dict for fast dispatch. Built from _COMMAND_GROUPS so the two
# views can never drift — adding a command in one place automatically
# updates the other.
COMMANDS: dict[str, CommandHandler] = {
    name: fn for _, pairs in _COMMAND_GROUPS for name, fn in pairs
}


def _usage() -> str:
    lines = [
        "usage: claudeteam <command> [args...]",
        "",
        "commands:",
    ]
    for group_label, pairs in _COMMAND_GROUPS:
        lines.append(f"  [{group_label}]")
        for name, _ in pairs:
            lines.append(f"    {name}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    cmd, rest = args[0], args[1:]
    handler = COMMANDS.get(cmd)
    if handler is None:
        return error_exit(f"unknown command: {cmd}\n\n{_usage()}")
    try:
        return int(handler(rest) or 0)
    except KeyboardInterrupt:
        # Ctrl-C from user; standard SIGINT exit code, no Python traceback
        print(file=sys.stderr)  # newline so the prompt doesn't glue to ^C
        return 130
    except Exception as e:
        # Friendly one-liner by default; full traceback when debugging.
        # Without this, every unhandled handler exception dumps a 30-line
        # traceback at the operator — useless for non-Python-fluent ops.
        import os
        if os.environ.get("CLAUDETEAM_DEBUG") == "1":
            raise
        return error_exit(
            f"❌ {cmd}: unhandled error: {type(e).__name__}: {e}\n"
            f"   set CLAUDETEAM_DEBUG=1 to see the full traceback")


if __name__ == "__main__":
    raise SystemExit(main())
