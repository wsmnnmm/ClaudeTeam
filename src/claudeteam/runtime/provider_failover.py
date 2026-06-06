"""Provider failover: promote backup preset or wake a rescue agent.

Goal:
  - Normal mode: all Codex workers use the primary preset.
  - If the active provider starts failing for key panes, switch to a
    backup preset and recycle only the selected panes.
  - If the backup also looks unhealthy, wake a rescue agent (typically
    a lazy `claude-code + DeepSeek` worker) so a live teammate can
    repair routing without the whole team going dark.

This module deliberately avoids proactive API probes. Backups should not
burn quota during healthy periods; failover is event-driven from real
pane failures only.
"""
from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Callable

from claudeteam.runtime import config, paths, tmux, tunables
from claudeteam.util import read_json, write_json


_STATE_FILE = "provider-failover.json"
_DEFAULT_MARKERS = [
    "auth_unavailable",
    "error code: 1010",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "connection refused",
    "timed out",
    "tls handshake timeout",
    "api connection error",
]


def _state_path() -> Path:
    return paths.state_file(_STATE_FILE)


def _load_state() -> dict:
    default = {
        "mode": "primary",
        "active_preset": "",
        "cooldown_until": 0.0,
        "recent_incidents": [],
        "last_failed_agents": [],
        "last_markers": {},
        "last_action": "",
        "last_action_at": 0.0,
    }
    path = _state_path()
    try:
        data = read_json(path, default)
    except (OSError, json.JSONDecodeError):
        return dict(default)
    if not isinstance(data, dict):
        return dict(default)
    out = dict(default)
    out.update(data)
    if not isinstance(out.get("recent_incidents"), list):
        out["recent_incidents"] = []
    if not isinstance(out.get("last_failed_agents"), list):
        out["last_failed_agents"] = []
    if not isinstance(out.get("last_markers"), dict):
        out["last_markers"] = {}
    return out


def _save_state(state: dict) -> None:
    write_json(_state_path(), state)


def _config() -> dict:
    targets = tunables.tunable("provider_failover.targets", ["manager"])
    recycle_targets = tunables.tunable("provider_failover.recycle_targets", [])
    markers = tunables.tunable("provider_failover.error_markers", list(_DEFAULT_MARKERS))
    backup_presets = tunables.tunable("provider_failover.backup_presets", [])
    if not isinstance(backup_presets, list):
        backup_presets = []
    legacy_backup = str(
        tunables.tunable("provider_failover.backup_preset", "") or ""
    ).strip()
    fallback_presets: list[str] = []
    for item in [*backup_presets, legacy_backup]:
        name = str(item).strip()
        if name and name not in fallback_presets:
            fallback_presets.append(name)
    return {
        "enabled": bool(tunables.tunable("provider_failover.enabled", False)),
        "primary_preset": str(
            tunables.tunable("provider_failover.primary_preset", "") or ""
        ).strip(),
        "backup_preset": fallback_presets[0] if fallback_presets else "",
        "backup_presets": fallback_presets,
        "rescue_agent": str(
            tunables.tunable("provider_failover.rescue_agent", "") or ""
        ).strip(),
        "trigger_threshold": int(
            tunables.tunable("provider_failover.trigger_threshold", 1)
        ),
        "trigger_window_s": int(
            tunables.tunable("provider_failover.trigger_window_s", 180)
        ),
        "cooldown_s": int(
            tunables.tunable("provider_failover.cooldown_s", 900)
        ),
        "pane_lines": int(
            tunables.tunable("provider_failover.pane_lines", 160)
        ),
        "targets": [
            str(v).strip() for v in (targets if isinstance(targets, list) else [])
            if str(v).strip()
        ],
        "recycle_targets": [
            str(v).strip()
            for v in (recycle_targets if isinstance(recycle_targets, list) else [])
            if str(v).strip()
        ],
        "error_markers": [
            str(v).strip().lower()
            for v in (markers if isinstance(markers, list) else [])
            if str(v).strip()
        ] or list(_DEFAULT_MARKERS),
    }


def _detect_failures(agents: list[str], *, session: str,
                     capture=tmux.capture_pane,
                     has_window=tmux.has_window,
                     pane_lines: int = 160,
                     markers: list[str] | None = None) -> dict[str, str]:
    matched: dict[str, str] = {}
    marker_list = markers or list(_DEFAULT_MARKERS)
    for agent in agents:
        target = tmux.Target(session, agent)
        if not has_window(target):
            continue
        text = capture(target, lines=pane_lines)
        low = text.lower()
        for marker in marker_list:
            if marker in low:
                matched[agent] = marker
                break
    return matched


def _apply_backup_preset(name: str, agents: list[str]) -> int:
    """Set per-agent provider_preset overrides for only the failing agents.

    This is deliberately NOT a global service switch — one agent's
    provider failure should not reroute the whole team.  Only the
    agents that are actually seeing errors get the backup preset.
    """
    from claudeteam.runtime import providers as provider_mod
    try:
        overrides = provider_mod.load_agent_overrides()
        for agent in agents:
            entry = dict(overrides.get(agent, {}))
            entry["provider_preset"] = name
            # Team-wide service overrides resolve after agent presets. Mark
            # failing lanes to bypass that final layer so the backup preset
            # actually takes effect for just these recycled agents.
            entry["bypass_service_override"] = True
            entry["failover_managed"] = True
            overrides[agent] = entry
        provider_mod.save_agent_overrides(overrides)
        return 0
    except Exception:
        return 1


def _reset_to_primary(cfg: dict, state: dict, *, now_ts: float, log=print) -> dict:
    from claudeteam.runtime import providers as provider_mod

    known_agents = {
        *cfg["targets"],
        *cfg["recycle_targets"],
        *[str(v) for v in state.get("last_failed_agents", []) if isinstance(v, str)],
    }
    overrides = provider_mod.load_agent_overrides()
    changed = False
    for agent in sorted(known_agents):
        entry = dict(overrides.get(agent, {}))
        if entry.get("failover_managed") is not True:
            continue
        entry.pop("provider_preset", None)
        entry.pop("bypass_service_override", None)
        entry.pop("failover_managed", None)
        if entry:
            overrides[agent] = entry
        else:
            overrides.pop(agent, None)
        changed = True
    if changed:
        provider_mod.save_agent_overrides(overrides)

    state["mode"] = "primary"
    state["active_preset"] = cfg["primary_preset"]
    state["cooldown_until"] = 0.0
    state["recent_incidents"] = []
    state["last_failed_agents"] = []
    state["last_markers"] = {}
    state["last_action"] = "reset_to_primary"
    state["last_action_at"] = now_ts
    _save_state(state)
    log("  ✅ provider_failover: cleared failover-managed overrides and reset to primary")
    return {
        "action": "reset_to_primary",
        "preset": cfg["primary_preset"],
        "changed": changed,
    }


def _recycle_agents(agents: list[str]) -> int:
    from claudeteam.commands import recycle as recycle_cmd
    return int(recycle_cmd.main(list(agents)) or 0)


def _notify_rescue(agent: str, message: str) -> int:
    from claudeteam.commands import send as send_cmd
    return int(send_cmd.main([agent, "watchdog", message, "高", "--no-task"]) or 0)


def _rescue_message(cfg: dict, failed: dict[str, str]) -> str:
    pairs = ", ".join(f"{agent}({marker})" for agent, marker in sorted(failed.items()))
    failed_agents = sorted(failed)
    return (
        "【PROVIDER-FAILOVER｜救火接管】\n"
        f"团队：{paths.config_file().parent}\n"
        f"当前模式：backup\n"
        f"primary_preset={cfg['primary_preset'] or '(unset)'}\n"
        f"backup_presets={', '.join(cfg['backup_presets']) or '(unset)'}\n"
        f"失败样本：{pairs}\n"
        "请你现在接管恢复：\n"
        "1. 运行 `claudeteam switch model agent <name>` 查看各 agent 的 preset overrides；\n"
        f"2. 如 backup 已设但仍报错，先 `claudeteam recycle {' '.join(failed_agents)}` 复核；\n"
        "3. 如果主/备链都不稳，再人工判断是否切 DeepSeek/官方线路，"
        "并只恢复最关键主管/员工。\n"
        f"4. 恢复后清除 per-agent overrides："
        f"`claudeteam switch model agent <name> --clear`。\n"
        "要求：先做 live check，再给 manager / 老板真实 blocker 或恢复结论。"
    )


def sweep(*,
          now: Callable[[], float] = time.time,
          capture=tmux.capture_pane,
          has_window=tmux.has_window,
          apply_preset=_apply_backup_preset,
          recycle_agents=_recycle_agents,
          notify_rescue=_notify_rescue,
          log=print) -> dict | None:
    cfg = _config()
    if not cfg["enabled"] or not cfg["backup_presets"]:
        return None

    known = set(config.agent_names())
    targets = [agent for agent in cfg["targets"] if agent in known]
    if not targets:
        return None
    recycle_targets = [
        agent for agent in (cfg["recycle_targets"] or targets) if agent in known
    ]
    rescue_agent = cfg["rescue_agent"] if cfg["rescue_agent"] in known else ""
    session = config.session_name()
    matched = _detect_failures(
        targets,
        session=session,
        capture=capture,
        has_window=has_window,
        pane_lines=cfg["pane_lines"],
        markers=cfg["error_markers"],
    )
    state = _load_state()
    t = float(now())
    recent = [
        row for row in state.get("recent_incidents", [])
        if isinstance(row, dict)
        and t - float(row.get("ts", 0.0)) <= cfg["trigger_window_s"]
    ]
    state["recent_incidents"] = recent
    if not matched:
        if (
                state.get("mode") in {"backup", "rescue_alerted"}
                and t >= float(state.get("cooldown_until", 0.0))):
            return _reset_to_primary(cfg, state, now_ts=t, log=log)
        _save_state(state)
        return None

    recent.append({"ts": t, "agents": sorted(matched)})
    state["recent_incidents"] = recent
    unique_failed = sorted({
        agent
        for row in recent
        for agent in row.get("agents", [])
        if isinstance(agent, str)
    })
    state["last_failed_agents"] = sorted(matched)
    state["last_markers"] = dict(matched)

    if t < float(state.get("cooldown_until", 0.0)):
        _save_state(state)
        return {
            "action": "cooldown",
            "failed_agents": sorted(matched),
            "mode": state.get("mode", "primary"),
        }

    if len(unique_failed) < max(1, cfg["trigger_threshold"]):
        _save_state(state)
        return {
            "action": "observed",
            "failed_agents": sorted(matched),
            "mode": state.get("mode", "primary"),
        }

    ordered_presets = [
        name for name in [cfg["primary_preset"], *cfg["backup_presets"]]
        if name
    ]
    active_preset = str(state.get("active_preset") or "").strip()
    if not active_preset and state.get("mode", "primary") == "primary":
        active_preset = cfg["primary_preset"]
    try:
        active_index = ordered_presets.index(active_preset)
    except ValueError:
        active_index = 0 if state.get("mode") == "primary" else -1
    next_preset = ""
    for candidate in ordered_presets[active_index + 1:]:
        if candidate:
            next_preset = candidate
            break

    if next_preset:
        failed_agents = sorted(matched)
        rc = apply_preset(next_preset, failed_agents)
        if rc == 0:
            rc = recycle_agents(failed_agents)
        state["mode"] = "backup"
        state["active_preset"] = next_preset
        state["cooldown_until"] = t + cfg["cooldown_s"]
        state["recent_incidents"] = []
        state["last_action"] = "promoted_backup" if rc == 0 else "backup_switch_failed"
        state["last_action_at"] = t
        _save_state(state)
        if rc == 0:
            log(
                "  🔁 provider_failover: set per-agent override "
                f"{next_preset} for {', '.join(failed_agents)} and recycled them"
            )
            return {
                "action": "promoted_backup",
                "failed_agents": failed_agents,
                "preset": next_preset,
            }
        log(
            "  ⚠️ provider_failover: failed to promote "
            f"{next_preset} (rc={rc})"
        )
        return {
            "action": "backup_switch_failed",
            "failed_agents": failed_agents,
            "preset": next_preset,
        }

    state["cooldown_until"] = t + cfg["cooldown_s"]
    state["recent_incidents"] = []
    if rescue_agent:
        message = _rescue_message(cfg, matched)
        rc = notify_rescue(rescue_agent, message)
        state["mode"] = "rescue_alerted"
        state["last_action"] = "alerted_rescue"
        state["last_action_at"] = t
        _save_state(state)
        if rc == 0:
            log(
                "  🛟 provider_failover: backup still unhealthy; "
                f"woke rescue agent {rescue_agent}"
            )
        else:
            log(
                "  ⚠️ provider_failover: failed to wake rescue agent "
                f"{rescue_agent} (rc={rc})"
            )
        return {
            "action": "alerted_rescue",
            "failed_agents": sorted(matched),
            "agent": rescue_agent,
        }

    state["last_action"] = "backup_failed"
    state["last_action_at"] = t
    _save_state(state)
    log("  ⚠️ provider_failover: backup preset unhealthy and no rescue agent configured")
    return {
        "action": "backup_failed",
        "failed_agents": sorted(matched),
    }
