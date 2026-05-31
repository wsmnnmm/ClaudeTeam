"""Tests for runtime/provider_failover.py."""
from __future__ import annotations

import json

from helpers import isolated_env
from claudeteam.runtime import provider_failover


TEAM = {
    "session": "S",
    "agents": {
        "manager": {"cli": "codex-cli", "model": "gpt-5.5"},
        "worker_a": {"cli": "codex-cli", "model": "gpt-5.4"},
        "worker_rescue": {"cli": "claude-code", "model": "opus", "lazy": True},
    },
}


def _write_toml(tmp, extra: str) -> None:
    (tmp / "claudeteam.toml").write_text(extra, encoding="utf-8")


def test_failover_promotes_backup_and_recycles_targets():
    with isolated_env(team=TEAM) as tmp:
        _write_toml(
            tmp,
            "\n".join([
                "[provider_failover]",
                "enabled = true",
                'primary_preset = "flux-primary"',
                'backup_preset = "zyapi-backup"',
                'targets = ["manager", "worker_a"]',
                'recycle_targets = ["manager"]',
                "trigger_threshold = 2",
                "trigger_window_s = 180",
                "cooldown_s = 600",
                'error_markers = ["auth_unavailable"]',
            ]) + "\n",
        )
        seen = {}

        def fake_capture(target, lines=160):
            return "unexpected status 503 Service Unavailable: auth_unavailable"

        def fake_has_window(target):
            return True

        result = provider_failover.sweep(
            now=lambda: 1000.0,
            capture=fake_capture,
            has_window=fake_has_window,
            apply_preset=lambda name, agents: seen.setdefault("preset", (name, agents)) and 0,
            recycle_agents=lambda agents: seen.setdefault("recycle", list(agents)) and 0,
            notify_rescue=lambda agent, message: 0,
            log=lambda *a, **kw: None,
        )
        assert result["action"] == "promoted_backup"
        assert seen["preset"] == ("zyapi-backup", ["manager", "worker_a"])
        assert seen["recycle"] == ["manager", "worker_a"]
        state = json.loads((tmp / "state" / "provider-failover.json").read_text())
        assert state["mode"] == "backup"
        assert state["active_preset"] == "zyapi-backup"
        assert state["last_action"] == "promoted_backup"


def test_failover_only_observes_below_threshold():
    with isolated_env(team=TEAM) as tmp:
        _write_toml(
            tmp,
            "\n".join([
                "[provider_failover]",
                "enabled = true",
                'backup_preset = "zyapi-backup"',
                'targets = ["manager", "worker_a"]',
                "trigger_threshold = 2",
                "trigger_window_s = 180",
                'error_markers = ["auth_unavailable"]',
            ]) + "\n",
        )
        result = provider_failover.sweep(
            now=lambda: 1000.0,
            capture=lambda target, lines=160: (
                "auth_unavailable" if str(target) == "S:manager" else "all good"
            ),
            has_window=lambda target: True,
            apply_preset=lambda name, agents: (_ for _ in ()).throw(AssertionError("should not switch")),
            recycle_agents=lambda agents: (_ for _ in ()).throw(AssertionError("should not recycle")),
            notify_rescue=lambda agent, message: 0,
            log=lambda *a, **kw: None,
        )
        assert result["action"] == "observed"
        assert result["failed_agents"] == ["manager"]


def test_failover_alerts_rescue_agent_after_backup_is_already_active():
    with isolated_env(team=TEAM) as tmp:
        _write_toml(
            tmp,
            "\n".join([
                "[provider_failover]",
                "enabled = true",
                'primary_preset = "flux-primary"',
                'backup_preset = "zyapi-backup"',
                'rescue_agent = "worker_rescue"',
                'targets = ["manager"]',
                "trigger_threshold = 1",
                "trigger_window_s = 180",
                "cooldown_s = 600",
                'error_markers = ["auth_unavailable"]',
            ]) + "\n",
        )
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-failover.json").write_text(
            json.dumps({
                "mode": "backup",
                "active_preset": "zyapi-backup",
                "cooldown_until": 0,
                "recent_incidents": [],
                "last_failed_agents": [],
                "last_markers": {},
                "last_action": "",
                "last_action_at": 0,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        seen = {}
        result = provider_failover.sweep(
            now=lambda: 1000.0,
            capture=lambda target, lines=160: "auth_unavailable",
            has_window=lambda target: True,
            apply_preset=lambda name, agents: (_ for _ in ()).throw(AssertionError("should not switch")),
            recycle_agents=lambda agents: (_ for _ in ()).throw(AssertionError("should not recycle")),
            notify_rescue=lambda agent, message: seen.setdefault("notice", (agent, message)) and 0,
            log=lambda *a, **kw: None,
        )
        assert result["action"] == "alerted_rescue"
        assert result["agent"] == "worker_rescue"
        assert seen["notice"][0] == "worker_rescue"
        assert "zyapi-backup" in seen["notice"][1]
        assert "claudeteam recycle manager" in seen["notice"][1]
        state = json.loads((tmp / "state" / "provider-failover.json").read_text())
        assert state["mode"] == "rescue_alerted"
        assert state["last_action"] == "alerted_rescue"


def test_failover_walks_multiple_backup_presets_before_rescue():
    with isolated_env(team=TEAM) as tmp:
        _write_toml(
            tmp,
            "\n".join([
                "[provider_failover]",
                "enabled = true",
                'primary_preset = "flux-primary"',
                'backup_presets = ["zyapi-backup", "onekey-backup"]',
                'rescue_agent = "worker_rescue"',
                'targets = ["manager"]',
                'recycle_targets = ["manager"]',
                "trigger_threshold = 1",
                "trigger_window_s = 180",
                "cooldown_s = 600",
                'error_markers = ["auth_unavailable"]',
            ]) + "\n",
        )
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-failover.json").write_text(
            json.dumps({
                "mode": "backup",
                "active_preset": "zyapi-backup",
                "cooldown_until": 0,
                "recent_incidents": [],
                "last_failed_agents": [],
                "last_markers": {},
                "last_action": "promoted_backup",
                "last_action_at": 900,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        seen = {}
        result = provider_failover.sweep(
            now=lambda: 1000.0,
            capture=lambda target, lines=160: "auth_unavailable",
            has_window=lambda target: True,
            apply_preset=lambda name, agents: seen.setdefault("preset", (name, agents)) and 0,
            recycle_agents=lambda agents: seen.setdefault("recycle", list(agents)) and 0,
            notify_rescue=lambda agent, message: (_ for _ in ()).throw(
                AssertionError("should not wake rescue while another backup exists")),
            log=lambda *a, **kw: None,
        )
        assert result["action"] == "promoted_backup"
        assert result["preset"] == "onekey-backup"
        assert seen["preset"] == ("onekey-backup", ["manager"])
        assert seen["recycle"] == ["manager"]
        state = json.loads((tmp / "state" / "provider-failover.json").read_text())
        assert state["mode"] == "backup"
        assert state["active_preset"] == "onekey-backup"
