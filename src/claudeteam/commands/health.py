"""`claudeteam health` — one-shot deployment-state check.

Reports, with green/red glyphs, the things that have to be true for
this team to actually deliver messages:

  - state_dir resolved (and from where: env vs default)
  - team.json + runtime_config.json present, with chat_id set
  - tmux session alive
  - per-agent: pane exists? CLI shows a ready marker?
  - router/watchdog: pid file present? process alive? cmdline matches?
  - router cursor: present? last-seen message id

Exit code: 0 if everything green, 1 if any red. Yellow (warning) does
not fail the check.
"""
from __future__ import annotations

import subprocess
import shutil
from dataclasses import dataclass, field

from claudeteam.agents import get_adapter
from claudeteam.feishu import catchup
from claudeteam.feishu import pane_state
from claudeteam.runtime import config, paths, tmux, watchdog
from claudeteam.store import local_facts
from claudeteam.util import (
    ago_ms, env_str, maybe_print_help, now_ms, pop_bool_flag, print_json,
    reject_extra_args,
)


_OK = "✅"
_BAD = "❌"
_WARN = "⚠️ "
_INFO = "ℹ️ "
_STALE_HEARTBEAT_MS = 30 * 60 * 1000


@dataclass
class HealthReport:
    """Accumulator handed to every `_check_*`. Emission and counting
    happen in one place so we don't string-search the formatted output
    later to figure out how many warnings we logged.
    """
    lines: list[str] = field(default_factory=list)
    bad: int = 0
    warn: int = 0
    warn_categories: dict[str, int] = field(default_factory=dict)

    def ok(self, msg: str) -> None:
        self.lines.append(f"  {_OK} {msg}")

    def fail(self, msg: str) -> None:
        self.lines.append(f"  {_BAD} {msg}")
        self.bad += 1

    def yellow(self, msg: str, *, category: str = "general") -> None:
        self.lines.append(f"  {_WARN}{msg}")
        self.warn += 1
        self.warn_categories[category] = self.warn_categories.get(category, 0) + 1

    def info(self, msg: str) -> None:
        self.lines.append(f"  {_INFO}{msg}")

    def note(self, msg: str) -> None:
        """Indented plain line (no glyph)."""
        self.lines.append(f"  {msg}")

    def section(self, title: str) -> None:
        """Unindented section header."""
        self.lines.append(title)

    def blank(self) -> None:
        self.lines.append("")

    def warning_footer(self) -> str:
        if not self.warn:
            return ""
        if set(self.warn_categories) == {"stale_heartbeat"}:
            return (
                f"no errors, {self.warn} warning(s): agent heartbeat stale only; "
                "not a Feishu CLI/App Secret warning"
            )
        labels = {
            "agent_runtime": "agent runtime",
            "daemon": "daemon",
            "feishu_config": "Feishu config",
            "feishu_process": "Feishu process",
            "network": "network",
            "stale_heartbeat": "stale heartbeat",
            "general": "general",
        }
        parts = [
            f"{labels.get(k, k)}={v}"
            for k, v in sorted(self.warn_categories.items())
        ]
        return f"no errors, {self.warn} warning(s) — categories: {', '.join(parts)}"


def _check_state_dir(rep: HealthReport) -> None:
    src = "env" if env_str("CLAUDETEAM_STATE_DIR") else "default (~/.claudeteam)"
    state = paths.state_dir()
    rep.note(f"state_dir: {state}  ({src})")
    expected_toml = state.parent / "claudeteam.toml"
    if env_str("CLAUDETEAM_STATE_DIR") and expected_toml.exists():
        active_toml = paths.config_file()
        if active_toml.resolve() != expected_toml.resolve():
            rep.fail(
                "config/state mismatch: "
                f"state_dir belongs to {expected_toml.parent}, "
                f"but active config is {active_toml}. "
                f"Set CLAUDETEAM_CONFIG_FILE={expected_toml}"
            )


def _check_team(rep: HealthReport) -> None:
    """Verify team is loadable and has at least one agent.

    Goes through `config.load_team()` (toml-first, json fallback) so
    deployments on either shape work. Reports red only when there's
    no usable config at all, or when the loaded team has zero agents.
    Corrupt-file detection is handled by the config layer's lenient
    parse (stderr warn) rather than tripping health.
    """
    cf = paths.config_file()
    tf = config.team_file()
    if not cf.exists() and not tf.exists():
        rep.fail(f"team config missing — expected {cf} or {tf}")
        return
    try:
        team = config.load_team()
    except Exception as e:
        rep.fail(f"team config parse error: {e}")
        return
    agents = team.get("agents", {})
    if agents:
        rep.ok(f"team config: {len(agents)} agent(s)")
    else:
        rep.fail("team config has no agents (set [team.agents.<name>] in claudeteam.toml)")


def _check_runtime_config(rep: HealthReport) -> None:
    """Verify chat_id is set + report lark_profile.

    Reads through `config.chat_id()` / `config.lark_profile()` which
    cascade env > toml > legacy json, so the check is shape-agnostic.
    """
    if chat := config.chat_id():
        rep.ok(f"chat_id: {chat}")
    else:
        rep.fail("chat_id is empty (set it in claudeteam.toml)")
    if profile := config.lark_profile():
        rep.ok(f"lark_profile: {profile}")
    else:
        rep.yellow("lark_profile blank — bot identity required for sends",
                   category="feishu_config")


def _check_session(rep: HealthReport, session: str) -> bool:
    if tmux.has_session(session):
        rep.ok(f"tmux session: {session}")
        return True
    rep.fail(f"tmux session {session} not running (run `claudeteam start`)")
    return False


def _check_agents(rep: HealthReport, session: str, agents: list[str],
                  session_alive: bool) -> None:
    heartbeats = local_facts.all_heartbeats()
    # Hoist load_team() out of the per-agent loop — each
    # `config.agent_cli` / `agent_config` would otherwise re-read
    # the team config (2-3 disk reads per agent). One read here, dict
    # probes inside the loop with `agents_dict.get(agent, {})` for
    # unknown-agent defaults.
    agents_dict = config.load_team().get("agents", {})
    for agent in agents:
        target = tmux.Target(session, agent)
        hb = heartbeats.get(agent)
        hb_suffix = f"  ♥ {ago_ms(hb)}" if hb else "  ♥ never"
        if not session_alive:
            rep.yellow(f"  {agent}: session down, skip{hb_suffix}",
                       category="agent_runtime")
            continue
        if not tmux.has_window(target):
            rep.fail(f"  {agent}: no tmux window{hb_suffix}")
            continue
        cfg = agents_dict.get(agent, {})
        cli = cfg.get("cli", "claude-code")
        try:
            # Resolve adapter from `cli` directly — not via
            # `adapter_for_agent(agent)`, which would re-read the team
            # config inside the loop.
            adapter = get_adapter(cli)
            text = tmux.capture_pane(target, lines=80)
            ready = any(m in text for m in adapter.ready_markers())
            emoji, brief = pane_state.parse(text)
            stale_hb = bool(hb and now_ms() - hb > _STALE_HEARTBEAT_MS)
            if ready and emoji in ("⚠️", "⛔", "🛑"):
                rep.yellow(
                    f"  {agent}: pane reachable but {brief} ({cli}){hb_suffix}",
                    category="agent_runtime")
            elif ready and stale_hb:
                rep.yellow(
                    f"  {agent}: pane ready ({cli}) but heartbeat is stale{hb_suffix}",
                    category="stale_heartbeat")
            elif ready:
                rep.ok(f"  {agent}: pane ready ({cli}){hb_suffix}")
            elif cfg.get("lazy"):
                rep.ok(f"  {agent}: lazy pane (CLI starts on first message){hb_suffix}")
            else:
                rep.yellow(
                    f"  {agent}: pane up but CLI not ready yet — wait a few seconds or check the pane{hb_suffix}",
                    category="agent_runtime")
        except Exception as e:
            rep.yellow(f"  {agent}: probe failed — {e}", category="agent_runtime")


def _check_daemon(rep: HealthReport, spec: watchdog.ProcessSpec) -> None:
    if not spec.pid_file.exists():
        rep.yellow(f"{spec.name}: no pid file (not running?)", category="daemon")
        return
    if watchdog.is_alive(spec):
        rep.ok(f"{spec.name}: alive ({spec.pid_file.read_text().strip()})")
        return
    rep.fail(f"{spec.name}: pid file present but process dead")


def _check_binaries(rep: HealthReport, agents: list[str]) -> None:
    """For each unique CLI process_name (claude/codex/kimi/...), verify the
    binary is on PATH. Missing binaries don't crash claudeteam, but every
    pane spawn will fail to launch its CLI."""
    # Same hoist as `_check_agents` — load team config once, look up
    # each agent's `cli` from the cached dict, get_adapter(cli)
    # skips the per-agent config bounce.
    from claudeteam.agents import get_adapter
    agents_dict = config.load_team().get("agents", {})
    seen: dict[str, list[str]] = {}
    for agent in agents:
        cli = agents_dict.get(agent, {}).get("cli", "claude-code")
        try:
            name = get_adapter(cli).process_name()
        except Exception:
            continue
        seen.setdefault(name, []).append(agent)
    for binary, used_by in sorted(seen.items()):
        users = ", ".join(used_by)
        path = shutil.which(binary)
        if path:
            rep.ok(f"{binary}: {path}  (used by {users})")
        else:
            rep.fail(f"{binary}: not on PATH  (used by {users})")


def _check_proxy_env(rep: HealthReport) -> None:
    """Report whether lark-cli will effectively bypass shell proxies.

    The wrapper accepts both the legacy env flag (`LARK_CLI_NO_PROXY=1`)
    and the newer config knob (`[feishu] no_proxy = true`).  Health used
    to inspect only the env var, so teams with the TOML knob correctly
    stripping proxies still looked risky.
    """
    proxy = env_str("HTTPS_PROXY") or env_str("HTTP_PROXY") or env_str("ALL_PROXY")
    if not proxy:
        return
    legacy = env_str("LARK_CLI_NO_PROXY").lower()
    if legacy in {"1", "true", "yes", "on"}:
        no_proxy = True
        source = "LARK_CLI_NO_PROXY"
    elif legacy in {"0", "false", "no", "off"}:
        no_proxy = False
        source = "LARK_CLI_NO_PROXY"
    else:
        from claudeteam.runtime import tunables
        no_proxy = bool(tunables.tunable("feishu.no_proxy", False))
        source = "feishu.no_proxy"
    if no_proxy:
        rep.info(f"proxy env set ({proxy}) but {source}=true — wrapper will strip")
    else:
        rep.yellow(
            f"proxy env={proxy} set without LARK_CLI_NO_PROXY=1; "
            "lark-cli requests may fail. Set `LARK_CLI_NO_PROXY=1` "
            "or `[feishu] no_proxy = true` to strip.",
            category="network")


def _lark_process_rows(run=subprocess.run) -> list[dict[str, str]]:
    """Return ps rows for lark-cli processes.

    Best-effort only: process-state visibility differs across macOS and
    Linux, and unit tests patch `run=`.  A failure to inspect processes
    should not make health unusable; it just means this extra audit is
    unavailable on that host.
    """
    try:
        result = run(
            ["ps", "-eo", "pid=,ppid=,stat=,command="],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    rows: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        if "lark-cli" not in line:
            continue
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid, ppid, stat, command = parts
        rows.append({"pid": pid, "ppid": ppid, "stat": stat, "command": command})
    return rows


def _process_matches_profile(command: str, profile: str) -> bool:
    return f"--profile {profile}" in command or f"--profile={profile}" in command


def _check_lark_processes(rep: HealthReport) -> None:
    """Detect stuck lark-cli subprocesses for this team's profile.

    macOS `ps` reports `U` for uninterruptible wait; Linux commonly uses
    `D` for the same class of uninterruptible sleep.  Either means the
    child may ignore SIGTERM/SIGKILL and keep the message transport in a
    deceptive half-alive state, so health must not stay green.
    """
    profile = config.lark_profile()
    if not profile:
        return
    rows = [
        row for row in _lark_process_rows()
        if _process_matches_profile(row["command"], profile)
    ]
    if not rows:
        return
    stuck = [row for row in rows if row["stat"][:1] in {"U", "D"}]
    orphaned = [
        row for row in rows
        if row["ppid"] == "1"
        and ("event +subscribe" in row["command"]
             or "im +chat-messages-list" in row["command"])
    ]
    if stuck:
        sample = ", ".join(row["pid"] for row in stuck[:5])
        rep.fail(
            f"lark-cli stuck process(es): {len(stuck)} for profile {profile} "
            f"(pid {sample}; uninterruptible wait, reboot/logout may be required)")
    elif orphaned:
        sample = ", ".join(row["pid"] for row in orphaned[:5])
        rep.yellow(
            f"lark-cli orphan process(es): {len(orphaned)} for profile {profile} "
            f"(pid {sample}); watchdog should reap or respawn cleanly",
            category="feishu_process")


def _check_cursor(rep: HealthReport) -> None:
    cur = catchup.read_cursor()
    if cur:
        rep.ok(f"router cursor: {cur.get('message_id', '?')} (create_time={cur.get('create_time', '?')})")
    else:
        # Empty cursor is normal until the first inbound event lands;
        # advancement only happens for events coming OFF the wire, not
        # for self-originated `say` calls. Informational, not warning.
        rep.info("router cursor: empty (advances on first inbound event)")


def _check_memory(rep: HealthReport) -> None:
    """Round-132: list agents that have written memory entries. Empty
    is normal on a fresh deploy; informational only. Surfaces
    persisted state that would otherwise need a `find facts/ -name
    memory.jsonl` to discover."""
    from claudeteam.store import memory
    agents = sorted(memory.all_agents_with_memory())
    if not agents:
        rep.info("memory: no agent has written entries yet")
        return
    # One-liner if few agents; line-per-agent if many (>5)
    if len(agents) <= 5:
        rep.info(f"memory: {len(agents)} agent(s) with entries — "
                 f"{', '.join(agents)}")
    else:
        rep.info(f"memory: {len(agents)} agent(s) with entries:")
        for a in agents:
            rep.note(f"  - {a}")


def _build_report() -> HealthReport:
    """Run every check and return the populated HealthReport. Pure
    enumeration — main() picks the renderer (text or JSON) and the
    exit code based on rep.bad."""
    rep = HealthReport()

    rep.section("paths:")
    _check_state_dir(rep)
    rep.blank()

    rep.section("config:")
    _check_team(rep)
    _check_runtime_config(rep)
    rep.blank()

    try:
        team = config.load_team()
        session = team.get("session", "ClaudeTeam")
        agents = sorted(team.get("agents", {}))
    except Exception:
        session, agents = "ClaudeTeam", []

    if agents:
        rep.section("binaries:")
        _check_binaries(rep, agents)
        rep.blank()

    rep.section("env:")
    _check_proxy_env(rep)
    rep.blank()

    rep.section("process audit:")
    _check_lark_processes(rep)
    rep.blank()

    rep.section("tmux:")
    session_alive = _check_session(rep, session)
    if agents:
        _check_agents(rep, session, agents, session_alive)
    rep.blank()

    rep.section("daemons:")
    for spec in watchdog.all_known_specs():
        _check_daemon(rep, spec)
    rep.blank()

    rep.section("router state:")
    _check_cursor(rep)
    rep.blank()

    rep.section("memory:")
    _check_memory(rep)

    return rep


def _emit_text(rep: HealthReport) -> None:
    """Default renderer: the formatted lines + a summary footer."""
    print("\n".join(rep.lines))
    if rep.bad:
        print(f"\n{_BAD} {rep.bad} red check(s) — see above")
    elif rep.warn:
        print(f"\n{_WARN}{rep.warning_footer()}")
    else:
        print(f"\n{_OK} all green")


def _emit_json(rep: HealthReport) -> None:
    """Machine-readable shape:
        {"ok": bool, "bad": int, "warn": int, "lines": [str, ...]}
    Smoke conductors / CI can branch on `ok` and inspect `lines` for
    the rendered glyphs (which still appear in `lines`, just packaged)."""
    print_json({
        "ok": rep.bad == 0,
        "bad": rep.bad,
        "warn": rep.warn,
        "warn_categories": dict(rep.warn_categories),
        "lines": list(rep.lines),
    })


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, "usage: claudeteam health [--json]"):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    if (rc := reject_extra_args(rest, "usage: claudeteam health [--json]")) is not None:
        return rc

    rep = _build_report()
    if as_json:
        _emit_json(rep)
    else:
        _emit_text(rep)
    return 1 if rep.bad else 0
