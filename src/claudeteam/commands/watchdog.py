"""`claudeteam watchdog`

Long-running supervisor that keeps the router (and any future daemons)
alive. Runs `runtime.watchdog.supervise` every
`watchdog.check_interval_s` seconds (claudeteam.toml; default 30) until
SIGTERM / Ctrl-C.

Self-locks via state_dir/watchdog.pid so two watchdogs can't fight.

Cooldown alerts:
- When a supervised daemon enters cooldown (max_retries respawns
  failed), the watchdog posts to Feishu chat so the boss sees the
  death without tailing the watchdog log.
- The alert is a red Feishu card with a 3-step recovery checklist
  (`claudeteam health` / read daemon log / `claudeteam down && up`
  after fix). Falls back to plain `send_text` on card schema
  rejection so the alert still lands.
- alert_fn is None when chat_id is unset — alerts are pointless
  without a delivery target; boot banner says "no chat alerts" so
  the operator knows.

Claude OAuth keep-alive:
- Bind-mounted claude .credentials.json expires during idle and
  the in-pane claude only refreshes on API call (not idle), which
  killed boss-message routing after long silences. Watchdog now
  proactively reads `expiresAt` every
  `watchdog.cred_check_interval_s` seconds (default 300); if
  the token's < `watchdog.cred_refresh_ahead_s` (default 1800) from
  expiry, run `claude -p "Return only OK"` once. That triggers
  claude to refresh the token in-place (file is bind-mounted RW so
  the new token persists back to host). All agent panes share the
  same file via per-agent symlink, so one refresh covers the whole
  team.

All alert paths are best-effort: chat send / card send failures are
swallowed at the alert_fn level (and runtime/watchdog's supervise
also try/excepts alert_fn). A broken alert path mustn't kill the
supervisor.

Boss cockpit fact flow:
- Optional `[cockpit_sync] enabled = true` makes the watchdog periodically
  run `claudeteam cockpit-sync --write`, projecting local team facts into
  the boss cockpit Base. It is off by default because writes target a real
  external Feishu table.
- Sync failures are logged but never kill the watchdog; stale cockpit rows
  are bad, but losing the router supervisor would be worse.

Topic digest flow:
- Optional `[topic_digest] enabled = true` makes the watchdog periodically
  write a local topic digest file. This keeps daily topic/task state
  recoverable without pushing routine noise into the Feishu group.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

from claudeteam.commands import cockpit_sync, topic as topic_cmd
from claudeteam.feishu import chat as _chat
from claudeteam.feishu.cards import simple_card
from claudeteam.runtime import (
    config, manager_watch, paths, pidlock, provider_failover, tunables,
    watchdog,
)
from claudeteam.util import maybe_print_help


_CRED_PATH = Path.home() / ".claude" / ".credentials.json"
# Resolves to /root/.claude/.credentials.json in Docker (HOME=/root) — same
# path the host-keychain bind-mount lands on — and to ~/.claude/... on host.
# Hardcoding /root broke host non-root deploys: Path("/root/...").exists()
# raised PermissionError (Linux /root is 700) instead of returning False
# under Python 3.10–3.12, killing `claudeteam up`.


def _make_alert_fn():
    """Build the alert callable handed to `supervise`. Captures chat_id +
    profile at construction time (cheap reads of runtime_config.json)
    so each cooldown event sends without re-reading config.

    Returns None when chat_id is unset — alerts are pointless without a
    delivery target, and a None alert_fn is the supervise default.

    Sends as a red Feishu card so the cooldown event is visually
    distinct from normal /team / /health cards. Falls back to plain
    text if send_card raises (schema mismatch on older lark builds).
    """
    chat_id = config.chat_id()
    if not chat_id:
        return None
    profile = config.lark_profile()

    def alert(name: str, failed_at: int, cooldown_secs: int) -> None:
        title = f"🚨 团队守护进程异常：{name} 暂停重试"
        body = (
            f"系统发现 **{name}** 连续 **{failed_at}** 次恢复失败，"
            f"已进入 **{cooldown_secs}s** 冷却，避免继续刷屏。\n\n"
            "当前影响：团队消息接收或后台守护可能不稳定。\n"
            "系统动作：已保留现场日志，等待操作员恢复。\n"
            "老板动作：先不用处理内部命令；如果需要你授权、登录或方向取舍，团队会单独说清楚。"
        )
        from claudeteam.runtime import tunables
        alarm_color = str(tunables.tunable("router.alarm_card_color", "red"))
        card = simple_card(title, body, color=alarm_color)
        try:
            _chat.send_card(chat_id, card, profile=profile, as_user=False)
        except Exception as e:
            # Card delivery shouldn't kill the watchdog. Fall back to
            # plain text so the alert still lands somehow; if THAT also
            # fails the supervise outer try/except logs it.
            print(f"  ⚠️ watchdog: card alert send failed ({e}); falling back to text")
            _chat.send_text(chat_id,
                            f"🚨 团队守护进程异常：{name} 连续 {failed_at} 次恢复失败，"
                            f"已进入 {cooldown_secs}s 冷却，等待操作员恢复。",
                            profile=profile, as_user=False)

    return alert


def _make_manager_watch_alert_fn():
    """Build optional Feishu alerting for overdue manager dispatches.

    The monitor always writes manager inbox + injects manager's pane. This
    chat card is the boss-visible fallback so a silent manager/worker pair
    does not stay invisible for another 15 minutes.
    """
    if not bool(tunables.tunable("manager_watch.public_chat_alert", False)):
        return None
    chat_id = config.chat_id()
    if not chat_id:
        return None
    profile = config.lark_profile()

    def alert(notice: manager_watch.OverdueNotice) -> None:
        if not (notice.public_title or notice.public_body):
            return
        color = str(tunables.tunable("manager_watch.card_color", "orange"))
        title = notice.public_title or "需要主管确认：任务长时间未收口"
        body = notice.public_body or (
            "系统发现一项主管派工需要核验。\n"
            f"任务编号：{notice.task_id}\n"
            "负责人：执行同学\n"
            "当前判断：需要主管核验执行现场后给出人话结论。\n\n"
            "主管下一步：请先确认已完成、卡住原因、改派方案和下次回报时间。"
        )
        card = simple_card(title, body, color=color)
        try:
            _chat.send_card(chat_id, card, profile=profile, as_user=False)
        except Exception as e:
            print(
                f"  ⚠️ manager_watch: card alert send failed ({e}); "
                "falling back to text")
            _chat.send_text(chat_id, body, profile=profile, as_user=False)

    return alert


def _run_manager_watch(alert_fn) -> None:
    if not bool(tunables.tunable("manager_watch.enabled", True)):
        return
    try:
        manager_watch.sweep(alert_fn=alert_fn)
    except Exception as e:
        print(f"  ⚠️ manager_watch sweep failed: {e}")
    try:
        manager_watch.sweep_first_output(alert_fn=alert_fn)
    except Exception as e:
        print(f"  ⚠️ manager_watch first output sweep failed: {e}")
    try:
        manager_watch.sweep_boss_inbox(alert_fn=alert_fn)
    except Exception as e:
        print(f"  ⚠️ manager_watch boss inbox sweep failed: {e}")
    try:
        manager_watch.sweep_manager_actions(alert_fn=alert_fn)
    except Exception as e:
        print(f"  ⚠️ manager_watch manager action sweep failed: {e}")


def _run_cockpit_sync(sync_main=cockpit_sync.main) -> int | None:
    """Run the optional boss cockpit projection once.

    Returns None when disabled, otherwise the command rc. Kept as a tiny
    wrapper instead of shelling out so tests can inject `sync_main` and the
    watchdog can keep supervising even when Feishu writes fail.
    """
    if not bool(tunables.tunable("cockpit_sync.enabled", False)):
        return None

    args = ["--write"]
    root = str(tunables.tunable("cockpit_sync.root", "") or "").strip()
    if root:
        args.extend(["--root", root])
    base_token = str(tunables.tunable("cockpit_sync.base_token", "") or "").strip()
    if base_token:
        args.extend(["--base-token", base_token])
    table_id = str(tunables.tunable("cockpit_sync.table_id", "") or "").strip()
    if table_id:
        args.extend(["--table-id", table_id])
    agent_table_id = str(tunables.tunable("cockpit_sync.agent_table_id", "") or "").strip()
    if agent_table_id:
        args.extend(["--agent-table-id", agent_table_id])
    task_table_id = str(tunables.tunable("cockpit_sync.task_table_id", "") or "").strip()
    if task_table_id:
        args.extend(["--task-table-id", task_table_id])
    remote_state_dir = str(tunables.tunable("cockpit_sync.remote_state_dir", "") or "").strip()
    if remote_state_dir:
        args.extend(["--remote-state-dir", remote_state_dir])
    profile = str(tunables.tunable("cockpit_sync.profile", "")
                  or config.lark_profile() or "").strip()
    if profile:
        args.extend(["--profile", profile])

    try:
        rc = int(sync_main(args) or 0)
    except Exception as e:
        print(f"  ⚠️ cockpit-sync sweep failed: {e}")
        return 1
    if rc != 0:
        print(f"  ⚠️ cockpit-sync exited rc={rc}")
    return rc


def _run_topic_digest(digest_writer=topic_cmd.write_digest) -> Path | None:
    """Write the optional local topic digest once.

    This is intentionally file-only: routine daily summaries are useful
    evidence, but they should not become more chat noise.
    """
    if not bool(tunables.tunable("topic_digest.enabled", False)):
        return None

    raw_out = str(
        tunables.tunable("topic_digest.out_dir", "reports/topic-digests")
        or "reports/topic-digests"
    ).strip()
    target = Path(raw_out).expanduser()
    if not target.is_absolute():
        target = paths.config_file().parent / target
    include_closed = bool(tunables.tunable("topic_digest.include_closed", False))
    try:
        path = digest_writer(target, include_closed=include_closed)
    except Exception as e:
        print(f"  ⚠️ topic digest write failed: {e}")
        return None
    print(f"  🧠 topic digest written: {path}")
    return path


def _run_network_probe(alert_fn=None) -> watchdog.NetworkStatus:
    """Probe API endpoint connectivity once.

    When the network is down, all agent processes appear alive but can't
    reach the model backend. This probe catches VPN drops and proxy failures
    before they compound into hours of silent downtime.

    Alerting: after `network_probe.min_ok_checks` consecutive failures
    (default 3, i.e. ~90s at default 30s check interval), posts a red
    Feishu card. Recovers silently after `network_probe.min_ok_checks`
    consecutive successes.
    """
    if not bool(tunables.tunable("network_probe.enabled", True)):
        return watchdog.NetworkStatus(ok=True, checked=[])

    min_ok = max(1, int(tunables.tunable("network_probe.min_ok_checks", 3)))
    state_file = paths.state_file("network-probe.json")
    try:
        prev = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    except (OSError, json.JSONDecodeError):
        prev = {}
    if not isinstance(prev, dict):
        prev = {}

    status = watchdog.check_network()
    fail_streak = int(prev.get("fail_streak") or 0)
    ok_streak = int(prev.get("ok_streak") or 0)
    last_alert_at = float(prev.get("last_alert_at") or 0)

    if status.ok:
        fail_streak = 0
        ok_streak += 1
        if ok_streak >= min_ok and prev.get("fail_streak", 0) >= min_ok:
            print(f"  🌐 network recovered after {int(prev.get('fail_streak', 0))} failed checks")
    else:
        ok_streak = 0
        fail_streak += 1
        print(f"  ⚠️ network probe failed ({fail_streak}/{min_ok}): {'; '.join(status.failures)}")
        if fail_streak >= min_ok and alert_fn is not None:
            repeat_s = int(tunables.tunable("network_probe.alert_repeat_s", 600))
            now = time.time()
            if now - last_alert_at >= repeat_s:
                try:
                    alert_fn("network", fail_streak, 0)
                except Exception as e:
                    print(f"  ⚠️ network alert failed: {e}")
                last_alert_at = now

    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({
            "fail_streak": fail_streak,
            "ok_streak": ok_streak,
            "last_alert_at": last_alert_at,
            "last_check": time.time(),
            "last_failures": status.failures,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return status


def _run_provider_failover(sweep=provider_failover.sweep) -> dict | None:
    """Run provider failover once when enabled.

    This is event-driven from real pane failures; no proactive API probes
    are made, so backup presets and rescue agents stay dormant during
    healthy periods and do not burn extra quota.
    """
    if not bool(tunables.tunable("provider_failover.enabled", False)):
        return None
    try:
        return sweep()
    except Exception as e:
        print(f"  ⚠️ provider_failover sweep failed: {e}")
        return {"action": "error", "error": str(e)}


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam watchdog"):
        return 0
    pid_file = paths.watchdog_pid_file()
    if not pidlock.acquire(pid_file, name="watchdog"):
        return 1
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    specs = watchdog.default_specs()
    states: dict = {}
    alert_fn = _make_alert_fn()
    manager_watch_alert_fn = _make_manager_watch_alert_fn()
    alert_msg = "with chat alerts" if alert_fn else "no chat alerts (chat_id unset)"
    check_interval_s = int(tunables.tunable("watchdog.check_interval_s", 30))
    cred_check_interval_s = int(tunables.tunable("watchdog.cred_check_interval_s", 300))
    manager_watch_interval_s = int(tunables.tunable("manager_watch.check_interval_s", 30))
    cockpit_sync_interval_s = int(tunables.tunable("cockpit_sync.interval_s", 120))
    topic_digest_interval_s = int(tunables.tunable("topic_digest.interval_s", 86400))
    network_probe_interval_s = int(tunables.tunable("network_probe.interval_s", 30))
    print(f"🐕 watchdog supervising {[s.name for s in specs]} every {check_interval_s}s ({alert_msg})")

    last_cred_check = 0.0
    last_manager_watch = 0.0
    last_cockpit_sync = 0.0
    last_topic_digest = 0.0
    last_network_probe = 0.0
    try:
        while True:
            watchdog.supervise(specs, states, alert_fn=alert_fn)
            if reap_agents:
                _reap_dead_agents(last_respawn, reap_cooldown_s)
            now = time.time()
            if now - last_network_probe >= network_probe_interval_s:
                _run_network_probe(alert_fn=alert_fn)
                last_network_probe = now
            if now - last_manager_watch >= manager_watch_interval_s:
                _run_manager_watch(manager_watch_alert_fn)
                last_manager_watch = now
            if now - last_cockpit_sync >= cockpit_sync_interval_s:
                _run_cockpit_sync()
                last_cockpit_sync = now
            if (topic_digest_interval_s > 0
                    and now - last_topic_digest >= topic_digest_interval_s):
                _run_topic_digest()
                last_topic_digest = now
            if now - last_cred_check >= cred_check_interval_s:
                _maybe_refresh_claude_oauth(now)
                last_cred_check = now
            _run_provider_failover()
            time.sleep(check_interval_s)
    except KeyboardInterrupt:
        print("watchdog stopped")
        return 0
    finally:
        pidlock.release(pid_file)


def _reap_dead_agents(last_respawn: dict, cooldown_s: float) -> list[str]:
    """Detect agents whose CLI exited and respawn them — mirrors `restart`
    (C-c + kill the window + provision a fresh CLI). Best-effort: the
    watchdog loop must keep running no matter what, so everything is
    swallowed. The agent_reaper is conservative (only a clear bash-prompt
    pane, never an auth screen, lazy/retired skipped, per-agent cooldown)."""
    from claudeteam.runtime import agent_reaper, config, lifecycle, tmux
    from claudeteam.store import local_facts
    try:
        session = config.session_name()
        if not tmux.has_session(session):
            return []
        agents_cfg = config.load_team().get("agents", {})
        agents = list(agents_cfg.keys())
        lazy = frozenset(n for n, c in agents_cfg.items() if c.get("lazy"))
    except Exception as e:
        print(f"  ⚠️ watchdog: could not read team for agent reap: {e}")
        return []

    def _respawn(agent: str) -> bool:
        target = tmux.Target(session, agent)
        if tmux.has_window(target):
            tmux.send_keys(target, "C-c")
            tmux.kill_window(target)
        if not tmux.new_window(target):
            return False
        outcome = lifecycle.provision_pane(agent, target)
        return outcome not in (lifecycle.SPAWN_FAILED, lifecycle.CONFIG_ERROR)

    return agent_reaper.reap(
        agents, session=session, respawn=_respawn,
        cooldown_s=cooldown_s, last_respawn=last_respawn,
        is_retired=local_facts.is_retired, lazy=lazy)


def _maybe_refresh_claude_oauth(now: float) -> None:
    """If the bind-mounted claude .credentials.json expires within
    `watchdog.cred_refresh_ahead_s` (claudeteam.toml; default 1800),
    force-refresh by spawning a brief `claude -p "Return only OK"`.
    That subprocess hits the Anthropic API which makes claude rotate
    the access token in-place. File is bind-mounted RW so the new
    token persists to host.

    Best-effort: any failure (file missing, parse error, claude crashes,
    network down) logs a warning but doesn't kill the watchdog. Worst
    case the boss still sees expired-token errors next cycle and runs
    `make creds` manually.
    """
    if not _CRED_PATH.exists():
        # Host deploy (macOS): no /root mount, claude OAuth lives in
        # keychain not file. Silent skip — printing every 5min spams
        # watchdog.log with hundreds of false alarms.
        return
    try:
        oauth = json.loads(_CRED_PATH.read_text())["claudeAiOauth"]
        expires_ms = int(oauth.get("expiresAt", 0))
    except (OSError, ValueError, KeyError) as e:
        print(f"  ⚠️ cred-refresh: read {_CRED_PATH} failed: {e}")
        return
    remaining = expires_ms / 1000 - now
    cred_refresh_ahead_s = int(tunables.tunable("watchdog.cred_refresh_ahead_s", 1800))
    if remaining > cred_refresh_ahead_s:
        return  # plenty of time; skip
    print(f"  🔑 claude token expires in {int(remaining)}s — forcing refresh")
    try:
        r = subprocess.run(
            ["claude", "-p", "Return only OK"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  ⚠️ cred-refresh: `claude -p` failed: {e}")
        return
    if r.returncode != 0:
        snippet = (r.stderr or r.stdout or "").strip()[:120]
        print(f"  ⚠️ cred-refresh: claude rc={r.returncode}: {snippet}")
        return
    print("  ✅ claude token refreshed")
