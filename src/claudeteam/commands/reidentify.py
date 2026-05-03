"""`claudeteam reidentify <agent>`

Re-inject the identity init prompt into a running agent's pane. Useful
when an agent has just `/compact`'d its context and forgot who it is, or
when an operator just edited `team.json` and wants the agent to pick up
its new role/model.

Does NOT spawn a new pane or restart the CLI — only sends the init
prompt as a fresh user message. The agent re-reads `identity.md` and
re-introduces itself in chat.
"""
from __future__ import annotations

from claudeteam.agents import adapter_for_agent, identity
from claudeteam.runtime import config, tmux
from claudeteam.util import error_exit, usage_error


USAGE = "usage: claudeteam reidentify <agent>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]

    try:
        config.agent_config(agent)
    except KeyError:
        return error_exit(f"❌ unknown agent: {agent} (not in team.json)")

    session = config.session_name()
    target = tmux.Target(session, agent)
    if not tmux.has_session(session):
        return error_exit(
            f"❌ tmux session {session} not running; run `claudeteam up` first")
    if not tmux.has_window(target):
        return error_exit(
            f"❌ {agent} has no pane in session {session} "
            f"(was it fired? try `claudeteam hire {agent}`)")

    adapter = adapter_for_agent(agent)
    if not tmux.inject(target, identity.init_prompt(agent),
                       submit_keys=adapter.submit_keys()):
        return error_exit(f"❌ failed to inject identity init into {agent}")
    print(f"✅ re-injected identity init into {agent} (pane: {target})")
    return 0
