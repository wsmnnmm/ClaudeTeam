"""Tests for `claudeteam router` daemon entry.

The Popen + signal-handler + endless-loop machinery in main() can't be
sanely unit-tested — it's plumbing around process_lines (separately
covered by test_feishu_subscribe + the in-process integration suite).
What CAN and SHOULD be tested:
  - _build_subscribe_cmd: the argv we hand to lark-cli
  - main() early-validation paths: missing chat_id, empty team,
    pidlock already held — all should exit non-zero with a clear
    stderr message before any subprocess is spawned.
"""
from __future__ import annotations

from helpers import isolated_env, run_cli
from claudeteam.commands.router import _build_subscribe_cmd


# ── _build_subscribe_cmd ──────────────────────────────────────────


def test_build_cmd_with_profile_inserts_profile_flag():
    cmd = _build_subscribe_cmd("test-live-a")
    assert cmd[0:2] == ["npx", "@larksuite/cli"]
    assert "--profile" in cmd and "test-live-a" in cmd
    # --profile must come BEFORE the "event" subcommand (lark-cli
    # parses global flags before subcommand args)
    profile_idx = cmd.index("--profile")
    event_idx = cmd.index("event")
    assert profile_idx < event_idx


def test_build_cmd_without_profile_omits_profile_flag():
    """No profile passed → no --profile in the argv (lark-cli falls back
    to its default profile)."""
    cmd = _build_subscribe_cmd("")
    assert "--profile" not in cmd


def test_build_cmd_filters_to_im_message_receive():
    """Only inbound text-style chat events; lark-cli has many other event
    types we don't want firing the router."""
    cmd = _build_subscribe_cmd("")
    assert "--event-types" in cmd
    et_idx = cmd.index("--event-types")
    assert cmd[et_idx + 1] == "im.message.receive_v1"


def test_build_cmd_uses_compact_quiet_force_bot_identity():
    """REGRESSION: --compact gets the JSON shape we parse;
    --quiet drops banner noise; --force suppresses the auth-confirm
    prompt; --as bot uses the app's im:message scope rather than user
    OAuth (which expires)."""
    cmd = _build_subscribe_cmd("")
    for flag in ("--compact", "--quiet", "--force"):
        assert flag in cmd, f"missing {flag}"
    as_idx = cmd.index("--as")
    assert cmd[as_idx + 1] == "bot"


# ── main() early validations ─────────────────────────────────────


def test_main_returns_one_when_chat_id_missing():
    """Empty chat_id in runtime_config → main exits before spawning
    lark-cli with a clear error."""
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    rc_cfg = {"chat_id": "", "lark_profile": "test"}  # explicit empty
    with isolated_env(team=team, runtime_config=rc_cfg):
        rc, _, err = run_cli(["router"])
    assert rc == 1
    assert "chat_id" in err
    assert "runtime_config.json" in err


def test_main_returns_one_when_team_has_no_agents():
    """An empty team.json `agents` map means there's nothing to route
    TO — the daemon would just drop everything."""
    team = {"agents": {}}
    rc_cfg = {"chat_id": "oc_x", "lark_profile": "test"}
    with isolated_env(team=team, runtime_config=rc_cfg):
        rc, _, err = run_cli(["router"])
    assert rc == 1
    assert "no agents" in err


# ── help ────────────────────────────────────────────────────────


def test_main_help_returns_zero():
    rc, out, _ = run_cli(["router", "--help"])
    assert rc == 0
    assert "usage: claudeteam router" in out
