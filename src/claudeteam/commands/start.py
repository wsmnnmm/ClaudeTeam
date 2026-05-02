"""`claudeteam start`

Bring up the whole team described in team.json: one tmux session, one
window per agent, each running its configured CLI.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from claudeteam.agents import adapter_for_agent, identity
from claudeteam.agents.codex_cli import ensure_workdir_trusted
from claudeteam.runtime import config, paths, tmux
from claudeteam.store import local_facts
from claudeteam.util import env_str, error_exit, help_requested, warn


# env vars to propagate from the operator's shell into every spawned pane
# so worker agents' shell-out calls (via Bash tool) see the deployment's
# state dir instead of falling back to ~/.claudeteam.
_PROPAGATED_ENV = (
    "LARK_CLI_PROFILE",
    "LARK_CLI_NO_PROXY",
    "CLAUDETEAM_LARK_SEND_AS",
    "CLAUDETEAM_TEAM_FILE",
    "CLAUDETEAM_RUNTIME_CONFIG",
    "CLAUDETEAM_DEFAULT_MODEL",
)


def pane_env_prefix() -> str:
    """Build a shell env prefix that, prepended to a spawn_cmd, makes the
    spawned process inherit CLAUDETEAM_STATE_DIR and the Feishu env so
    worker agents calling `claudeteam say` write to the project state
    dir, not `~/.claudeteam`.
    """
    parts = [f"CLAUDETEAM_STATE_DIR={shlex.quote(str(paths.state_dir()))}"]
    for var in _PROPAGATED_ENV:
        val = env_str(var)
        if val:
            parts.append(f"{var}={shlex.quote(val)}")
    return " ".join(parts)


def main(argv: list[str]) -> int:
    if help_requested(argv):
        print("usage: claudeteam start")
        return 0

    team = config.load_team()
    agents = team.get("agents", {})
    if not agents:
        return error_exit("❌ team.json has no agents")

    session = team.get("session", "ClaudeTeam")
    agent_list = sorted(agents)
    first = agent_list[0]

    if tmux.has_session(session):
        print(f"⚠️  session {session} already running; refusing to start over")
        return 1

    if not tmux.new_session(session, window=first):
        return error_exit(f"❌ failed to create tmux session {session}")
    print(f"🚀 created tmux session {session} (initial window: {first})")

    for agent in agent_list:
        target = tmux.Target(session, agent)
        if agent != first:
            if not tmux.new_window(target):
                warn(f"⚠️  failed to create window for {agent}, skipping")
                continue
        cfg = config.agent_config(agent)
        cli = cfg.get("cli", "claude-code")
        identity.write(agent)
        if cfg.get("lazy"):
            local_facts.upsert_status(agent, "待命", "lazy: CLI starts on first message")
            print(f"  → {agent} ({cli}) lazy-pane ready")
            continue
        if cli == "codex-cli":
            ensure_workdir_trusted(Path.cwd())
        adapter = adapter_for_agent(agent)
        cmd = f"{pane_env_prefix()} {adapter.spawn_cmd(agent, config.agent_model(agent))}"
        if not tmux.spawn_agent(target, cmd):
            warn(f"⚠️  failed to spawn CLI in {agent} pane")
            continue
        local_facts.upsert_status(agent, "进行中", "initializing")
        print(f"  → {agent} ({cli}) spawned")

    print(f"✅ team {session} started ({len(agent_list)} agents)")
    return 0
