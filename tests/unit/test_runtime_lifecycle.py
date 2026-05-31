"""Tests for runtime/lifecycle.py — pane_env_prefix + provision_pane.

Both helpers were extracted in round-16 from `commands/start.py` /
`commands/hire.py` but never got their own unit test (CLAUDE.md rule:
every new module ships its own unit test). The behaviour was covered
transitively through start/hire integration tests; this file pins
provision_pane directly for each of its four outcomes (LAZY / READY /
READY_NO_INIT / SPAWN_FAILED).
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from helpers import attr_patch, env_patch, isolated_env, tmux_patch
from claudeteam.agents import get_adapter
from claudeteam.runtime import lifecycle, paths, tmux, wake
from claudeteam.runtime.lifecycle import (
    LAZY, READY, READY_NO_INIT, SPAWN_FAILED, CONFIG_ERROR,
    _migrate_codex_wire_api, lazy_spawn_cmd, pane_env_prefix,
    provision_pane,
)
from claudeteam.store import local_facts


# ── pane_env_prefix ───────────────────────────────────────────────


def _spawn_script_text(cmd: str) -> str:
    return Path(shlex.split(cmd)[1]).read_text(encoding="utf-8")


def test_pane_env_prefix_always_includes_state_dir():
    """Even with no other env set, STATE_DIR is always emitted so the
    spawned pane never falls back to ~/.claudeteam."""
    with isolated_env(team={"agents": {"a": {}}}):
        prefix = pane_env_prefix()
    assert prefix.startswith("CLAUDETEAM_STATE_DIR=")


def test_pane_env_prefix_always_includes_config_file():
    """Spawned panes must not inherit another team's CLAUDETEAM_CONFIG_FILE."""
    with isolated_env(team={"agents": {"a": {}}}) as tmp:
        prefix = pane_env_prefix()
        expected = shlex.quote(str(tmp / "claudeteam.toml"))
    assert f"CLAUDETEAM_CONFIG_FILE={expected}" in prefix


def test_pane_env_prefix_propagates_lark_profile_when_set():
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE="prod"):
        prefix = pane_env_prefix()
    assert "LARK_CLI_PROFILE=prod" in prefix


def test_pane_env_prefix_prefers_toml_lark_profile_over_stale_env():
    with isolated_env(team={"agents": {"a": {}}}) as tmp, env_patch(
            LARK_CLI_PROFILE="todo002-study-coach"):
        (tmp / "claudeteam.toml").write_text(
            'lark_profile = "product-lab"\n',
            encoding="utf-8",
        )
        prefix = pane_env_prefix()
    assert "LARK_CLI_PROFILE=product-lab" in prefix
    assert "todo002-study-coach" not in prefix


def test_pane_env_prefix_uses_project_codex_home_even_when_host_env_set():
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            CODEX_HOME="/tmp/project codex"):
        prefix = pane_env_prefix()
        expected = shlex.quote(str(paths.codex_home_dir()))
    assert f"CODEX_HOME={expected}" in prefix
    assert "/tmp/project codex" not in prefix


def test_pane_env_prefix_uses_agent_specific_codex_home_for_codex_agent():
    team = {"agents": {"worker_codex": {"cli": "codex-cli", "model": "gpt-5.5"}}}
    with isolated_env(team=team):
        prefix = pane_env_prefix("worker_codex")
        expected = shlex.quote(str(paths.codex_home_dir("worker_codex")))
    assert f"CODEX_HOME={expected}" in prefix


def test_pane_env_prefix_injects_venv_path_and_pythonpath_when_set():
    with isolated_env(team={"agents": {"a": {}}}), attr_patch(
            lifecycle, _venv_path_prefix=lambda: "/tmp/ct/bin:$PATH"), env_patch(
            PYTHONPATH="/tmp/src"):
        prefix = pane_env_prefix()
    assert "PATH=" in prefix and ":$PATH" in prefix
    assert "PYTHONPATH=/tmp/src" in prefix


def test_pane_env_prefix_skips_unset_vars():
    """Vars not present in the operator shell don't pollute the prefix."""
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE=None,
            LARK_CLI_NO_PROXY=None,
            CLAUDETEAM_LARK_SEND_AS=None,
            CLAUDETEAM_DEFAULT_MODEL=None):
        prefix = pane_env_prefix()
    # Only state_dir survives (team_file/runtime_config are set by isolated_env)
    assert "LARK_CLI_PROFILE=" not in prefix
    assert "LARK_CLI_NO_PROXY=" not in prefix


def test_pane_env_prefix_propagates_feishu_app_credentials():
    """Bringup B5: tmux server started by an earlier checkout had its
    own global env without FEISHU_APP_*; new panes inherited that env
    and tenant_token_from_env() returned None → fell back to the saved
    lark-cli profile (an OLD app) → HTTP 400 on every claudeteam say.
    Embedding the creds in the spawn-cmd prefix sidesteps the
    tmux-server-env quirk."""
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            FEISHU_APP_ID="cli_NEW",
            FEISHU_APP_SECRET="newSecret123",
            LARKSUITE_CLI_APP_ID="cli_NEW",
            LARKSUITE_CLI_APP_SECRET="newSecret123"):
        prefix = pane_env_prefix()
    assert "FEISHU_APP_ID=cli_NEW" in prefix
    assert "FEISHU_APP_SECRET=newSecret123" in prefix
    assert "LARKSUITE_CLI_APP_ID=cli_NEW" in prefix
    assert "LARKSUITE_CLI_APP_SECRET=newSecret123" in prefix


def test_pane_env_prefix_skips_feishu_credentials_when_profile_app_mismatches():
    """A stale operator env from another team must not be baked into newly
    spawned panes when claudeteam.toml selects a different lark profile."""
    with isolated_env(team={"agents": {"a": {}}}) as tmp, \
            attr_patch(lifecycle, _feishu_env_matches_profile=lambda profile: False), \
            env_patch(FEISHU_APP_ID="cli_OTHER",
                      FEISHU_APP_SECRET="oldSecret123",
                      LARKSUITE_CLI_APP_ID="cli_OTHER",
                      LARKSUITE_CLI_APP_SECRET="oldSecret123"):
        (tmp / "claudeteam.toml").write_text(
            'lark_profile = "work-assistant"\n',
            encoding="utf-8",
        )
        prefix = pane_env_prefix()
    assert "LARK_CLI_PROFILE=work-assistant" in prefix
    assert "FEISHU_APP_ID=" not in prefix
    assert "FEISHU_APP_SECRET=" not in prefix
    assert "LARKSUITE_CLI_APP_ID=" not in prefix
    assert "LARKSUITE_CLI_APP_SECRET=" not in prefix


def test_pane_env_prefix_propagates_agent_tool_network_env():
    """Agent tools need explicit proxy/token env inside tmux panes.

    macOS system proxy settings do not enter spawned shells, so OpenAI can
    time out directly even while a local proxy is healthy. Lark calls still
    strip proxy vars via feishu.no_proxy before launching lark-cli.
    """
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}), env_patch(
            HTTPS_PROXY="http://127.0.0.1:7897",
            HTTP_PROXY="http://127.0.0.1:7897",
            ALL_PROXY="socks5://127.0.0.1:7897",
            NO_PROXY="localhost,127.0.0.1",
            OPENAI_IMAGE_MODEL="gpt-image-2",
            OPENAI_IMAGE_BASE_URL="https://relay.example/v1",
            OPENAI_IMAGE_API_KEY="sk-image",
            REPLICATE_API_TOKEN="r8_dummy"):
        prefix = pane_env_prefix("worker_codex")
    assert "HTTPS_PROXY=http://127.0.0.1:7897" in prefix
    assert "HTTP_PROXY=http://127.0.0.1:7897" in prefix
    assert "ALL_PROXY=socks5://127.0.0.1:7897" in prefix
    assert "NO_PROXY=localhost,127.0.0.1" in prefix
    assert "OPENAI_IMAGE_MODEL=gpt-image-2" in prefix
    assert "OPENAI_IMAGE_BASE_URL=https://relay.example/v1" in prefix
    assert "OPENAI_IMAGE_API_KEY=sk-image" in prefix
    assert "REPLICATE_API_TOKEN=r8_dummy" in prefix


def test_claude_agent_spawn_cmd_uses_agent_specific_provider_preset_when_present():
    team = {
        "agents": {
            "worker_translate": {
                "cli": "claude-code",
                "model": "sonnet",
                "provider_preset": "cheap-translate",
            }
        }
    }
    with isolated_env(team=team) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "ccswitch.json").write_text(
            '{"env":{"ANTHROPIC_BASE_URL":"https://global.example","ANTHROPIC_AUTH_TOKEN":"sk-global"}}',
            encoding="utf-8",
        )
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"cheap-translate":{"ANTHROPIC_BASE_URL":"https://cm.example/v1",'
            '"ANTHROPIC_AUTH_TOKEN":"sk-cm","ANTHROPIC_DEFAULT_SONNET_MODEL":"minimax-m25"}}}',
            encoding="utf-8",
        )
        cmd = lifecycle._agent_spawn_cmd(
            "worker_translate", get_adapter("claude-code"), "minimax-m25")
        script = _spawn_script_text(cmd)
    assert "ANTHROPIC_BASE_URL=https://cm.example/v1" in script
    assert "ANTHROPIC_AUTH_TOKEN=sk-cm" in script
    assert "global.example" not in script


def test_claude_agent_spawn_cmd_skips_env_provider_keys_with_preset():
    team = {
        "agents": {
            "manager": {
                "cli": "claude-code",
                "model": "opus",
                "provider_preset": "local-claude",
            }
        }
    }
    with isolated_env(team=team) as tmp, env_patch(
            ANTHROPIC_BASE_URL="https://stale.example/v1",
            ANTHROPIC_AUTH_TOKEN="sk-stale"):
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"local-claude":{"ANTHROPIC_BASE_URL":"https://local.example/v1",'
            '"ANTHROPIC_AUTH_TOKEN":"sk-local","ANTHROPIC_DEFAULT_OPUS_MODEL":"claude-opus-4-7"}}}',
            encoding="utf-8",
        )
        cmd = lifecycle._agent_spawn_cmd(
            "manager", get_adapter("claude-code"), "claude-opus-4-7")
        script = _spawn_script_text(cmd)
    assert "stale.example" not in script
    assert "sk-stale" not in script
    assert "ANTHROPIC_BASE_URL=https://local.example/v1" in script
    assert "ANTHROPIC_AUTH_TOKEN=sk-local" in script
    assert script.count("ANTHROPIC_BASE_URL=") == 1


def test_pane_env_prefix_skips_provider_env_for_codex_agents():
    team = {
        "agents": {
            "worker_codex": {
                "cli": "codex-cli",
                "model": "gpt-5.2",
                "provider_preset": "flux-gpt-tiered",
            }
        }
    }
    with isolated_env(team=team) as tmp, env_patch(
            ANTHROPIC_BASE_URL="https://dashscope.example/anthropic",
            ANTHROPIC_AUTH_TOKEN="sk-aliyun-old"):
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"flux-gpt-tiered":{"ANTHROPIC_BASE_URL":"https://flux.example/v1",'
            '"ANTHROPIC_AUTH_TOKEN":"sk-flux","ANTHROPIC_DEFAULT_SONNET_MODEL":"gpt-5.2"}}}',
            encoding="utf-8",
        )
        prefix = pane_env_prefix("worker_codex")
    assert "dashscope.example" not in prefix
    assert "sk-aliyun-old" not in prefix
    assert "flux.example" not in prefix
    assert "sk-flux" not in prefix
    assert "CODEX_HOME=" in prefix


def test_pane_env_prefix_hides_provider_secret_for_codex_agents():
    team = {
        "agents": {
            "worker_codex": {
                "cli": "codex-cli",
                "model": "gpt-5.2",
                "provider_preset": "backup-openai",
            }
        }
    }
    with isolated_env(team=team) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"backup-openai":{"OPENAI_BASE_URL":"https://backup.example/v1",'
            '"OPENAI_API_KEY":"sk-backup","OPENAI_MODEL":"gpt-5.4"}}}',
            encoding="utf-8",
        )
        prefix = pane_env_prefix("worker_codex")
    assert "OPENAI_API_KEY=" not in prefix
    assert "ANTHROPIC_AUTH_TOKEN=" not in prefix
    assert "OPENAI_BASE_URL=" not in prefix
    assert "OPENAI_MODEL=" not in prefix
    assert "CODEX_HOME=" in prefix


def test_claude_agent_spawn_cmd_runtime_agent_override_beats_team_config_preset():
    team = {
        "agents": {
            "worker_translate": {
                "cli": "claude-code",
                "model": "sonnet",
                "provider_preset": "cheap-translate",
            }
        }
    }
    with isolated_env(team=team) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "ccswitch.json").write_text(
            '{"env":{"ANTHROPIC_BASE_URL":"https://global.example","ANTHROPIC_AUTH_TOKEN":"sk-global"}}',
            encoding="utf-8",
        )
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"cheap-translate":{"ANTHROPIC_BASE_URL":"https://cheap.example/v1",'
            '"ANTHROPIC_AUTH_TOKEN":"sk-cheap","ANTHROPIC_DEFAULT_SONNET_MODEL":"cheap-sonnet"},'
            '"cm-minimax-m25":{"ANTHROPIC_BASE_URL":"https://cm.example/v1",'
            '"ANTHROPIC_AUTH_TOKEN":"sk-cm","ANTHROPIC_DEFAULT_SONNET_MODEL":"minimax-m25"}}}',
            encoding="utf-8",
        )
        (tmp / "state" / "agent-provider-overrides.json").write_text(
            '{"agents":{"worker_translate":{"provider_preset":"cm-minimax-m25"}}}',
            encoding="utf-8",
        )
        cmd = lifecycle._agent_spawn_cmd(
            "worker_translate", get_adapter("claude-code"), "minimax-m25")
        script = _spawn_script_text(cmd)
    assert "ANTHROPIC_BASE_URL=https://cm.example/v1" in script
    assert "ANTHROPIC_AUTH_TOKEN=sk-cm" in script
    assert "cheap.example" not in script


def test_pane_env_prefix_shell_quotes_paths_with_spaces():
    """shlex.quote should wrap any value containing whitespace; otherwise
    `eval $(...)` in a downstream shell would split on the space."""
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE="my profile"):
        prefix = pane_env_prefix()
    # quoted form: 'my profile' (single quotes) — never raw `my profile`
    assert "'my profile'" in prefix


def test_lazy_spawn_cmd_cd_to_config_dir_not_caller_cwd():
    """A lazy worker may be woken by a manager pane whose cwd drifted into
    another checkout. Spawn from claudeteam.toml's directory so Codex trusts
    and reads the intended project, not the caller's shell cwd."""
    team = {"agents": {"worker_codex": {"cli": "codex-cli", "model": "gpt-5.5"}}}
    with isolated_env(team=team) as tmp:
        cmd = lazy_spawn_cmd("worker_codex")
        script = _spawn_script_text(cmd)
        expected = f"cd {shlex.quote(str(tmp))} && "
        cfg = paths.codex_config_file("worker_codex").read_text(encoding="utf-8")
    assert cmd.startswith("bash ")
    assert expected in script
    assert f'[projects."{tmp}"]' in cfg
    assert 'trust_level = "trusted"' in cfg


# ── provision_pane: LAZY ──────────────────────────────────────────


def test_provision_lazy_agent_sets_待命_and_skips_spawn():
    """Lazy agents in team.json get status 待命; spawn_agent is never
    called (the pane stays at a shell prompt)."""
    team = {"agents": {"sleepy": {"cli": "claude-code", "lazy": True}}}
    spawn_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True):
        outcome = provision_pane("sleepy", tmux.Target("S", "sleepy"))
        assert outcome == LAZY
        assert spawn_calls == []
        snap = local_facts.get_status("sleepy")
        assert snap["status"] == "待命"
        assert "lazy" in snap["task"]
        assert local_facts.get_heartbeat("sleepy") is not None


def test_provision_lazy_agent_with_unread_inbox_wakes_on_start():
    """Queued work must survive a down/up: lazy workers with unread inbox
    rows should boot and process instead of staying at a shell prompt."""
    team = {"agents": {"sleepy": {"cli": "claude-code", "lazy": True}}}
    spawn_calls = []
    inject_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True,
            inject=lambda t, text, **kw: inject_calls.append((str(t), text)) or True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: True):
        local_facts.append_message("sleepy", "manager", "do pending work", priority="高")
        outcome = provision_pane("sleepy", tmux.Target("S", "sleepy"))

    assert outcome == READY
    assert spawn_calls
    assert inject_calls
    assert "bin/ct inbox sleepy" in inject_calls[0][1]


# ── provision_pane: SPAWN_FAILED ──────────────────────────────────


def test_provision_spawn_failure_returns_spawn_failed():
    team = {"agents": {"a": {"cli": "claude-code"}}}
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: False):
        outcome = provision_pane("a", tmux.Target("S", "a"))
    assert outcome == SPAWN_FAILED


# ── provision_pane: READY (happy path) ────────────────────────────


def test_provision_ready_spawns_then_injects_init_prompt():
    """Happy path: spawn succeeds, wait_until_ready true, identity init
    is injected, status flips to 进行中."""
    team = {"agents": {"alice": {"cli": "claude-code", "model": "opus"}}}
    spawn_calls = []
    inject_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True,
            inject=lambda t, text, **kw: inject_calls.append((str(t), text)) or True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: True):
        outcome = provision_pane("alice", tmux.Target("S", "alice"))
        assert outcome == READY
        assert len(spawn_calls) == 1
        # Identity init prompt was injected after spawn
        assert len(inject_calls) == 1
        assert "alice" in inject_calls[0][1]
        assert "identity.md" in inject_calls[0][1]
        snap = local_facts.get_status("alice")
        assert snap["status"] == "进行中"
        assert local_facts.get_heartbeat("alice") is not None


def test_provision_ready_no_init_when_identity_inject_fails():
    team = {"agents": {"alice": {"cli": "claude-code", "model": "opus"}}}
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda *a, **kw: True,
            inject=lambda *a, **kw: False), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: True):
        outcome = provision_pane("alice", tmux.Target("S", "alice"))
    assert outcome == READY_NO_INIT


def test_provision_ready_pane_env_prefix_baked_into_spawn_cmd():
    team = {"agents": {"a": {"cli": "claude-code"}}}
    spawn_calls = []
    with isolated_env(team=team) as tmp, tmux_patch(
            spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True,
            inject=lambda *a, **kw: True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: True):
        provision_pane("a", tmux.Target("S", "a"))
        cmd = spawn_calls[0][1]
        script = _spawn_script_text(cmd)
    assert cmd.startswith("bash ")
    assert script.startswith("#!/usr/bin/env bash\nset -e\n")
    assert f"cd {shlex.quote(str(tmp))} && " in script
    assert "CLAUDETEAM_STATE_DIR=" in script
    # Adapter contributed the actual CLI spawn after the env prefix
    assert "claude" in script


def test_provision_codex_bootstraps_project_auth_and_single_codex_home():
    team = {"agents": {"worker_codex": {"cli": "codex-cli"}}}
    spawn_calls = []
    with isolated_env(team=team) as tmp:
        host_codex = tmp / "host-codex"
        host_codex.mkdir(parents=True, exist_ok=True)
        src_auth = host_codex / "auth.json"
        src_auth.write_text('{"tokens":{"id_token":"abc"}}', encoding="utf-8")
        src_auth_text = src_auth.read_text(encoding="utf-8")
        with env_patch(CODEX_HOME=str(host_codex)), tmux_patch(
                spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True,
                inject=lambda *a, **kw: True), \
                attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
            outcome = provision_pane("worker_codex", tmux.Target("S", "worker_codex"))
            copied_auth = paths.codex_auth_file("worker_codex").read_text(encoding="utf-8")
            expected_codex_home = shlex.quote(str(paths.codex_home_dir("worker_codex")))
            script = _spawn_script_text(spawn_calls[0][1])
    assert outcome == READY_NO_INIT
    assert copied_auth == src_auth_text
    assert script.count("CODEX_HOME=") == 1
    assert f"CODEX_HOME={expected_codex_home}" in script
    assert str(host_codex) not in script


def test_provision_codex_writes_project_local_custom_provider_config():
    team = {
        "agents": {
            "worker_codex": {
                "cli": "codex-cli",
                "model": "gpt-5.5",
                "provider_preset": "flux-codex-dev",
            }
        }
    }
    with isolated_env(team=team) as tmp:
        state = tmp / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "provider-presets.json").write_text(
            json.dumps({
                "presets": {
                    "flux-codex-dev": {
                        "ANTHROPIC_BASE_URL": "https://api.fluxincode.com/v1",
                        "ANTHROPIC_AUTH_TOKEN": "sk-flux-123",
                        "ANTHROPIC_DEFAULT_OPUS_MODEL": "gpt-5.3-codex",
                    }
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        with tmux_patch(
                spawn_agent=lambda *a, **kw: True,
                inject=lambda *a, **kw: True), \
                attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
            outcome = provision_pane("worker_codex", tmux.Target("S", "worker_codex"))
        auth = json.loads(paths.codex_auth_file("worker_codex").read_text(encoding="utf-8"))
        cfg = paths.codex_config_file("worker_codex").read_text(encoding="utf-8")
    assert outcome == READY_NO_INIT
    assert auth == {"OPENAI_API_KEY": "sk-flux-123"}
    assert 'model_provider = "custom"' in cfg
    assert 'model = "gpt-5.5"' in cfg
    assert 'model_verbosity = "medium"' in cfg
    assert 'base_url = "https://api.fluxincode.com/v1"' in cfg


def test_provision_codex_spawn_model_follows_openai_model_preset():
    """REGRESSION: Codex homes could be written with the backup model while
    the actual pane still started with the team/requested model."""
    team = {
        "agents": {
            "manager": {
                "cli": "codex-cli",
                "model": "gpt-5.2",
                "provider_preset": "backup-openai",
            }
        }
    }
    spawn_calls = []
    with isolated_env(team=team) as tmp:
        state = tmp / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "provider-presets.json").write_text(
            json.dumps({
                "presets": {
                    "backup-openai": {
                        "OPENAI_BASE_URL": "https://backup.example/v1",
                        "OPENAI_API_KEY": "sk-backup",
                        "OPENAI_MODEL": "gpt-5.4",
                    }
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        with tmux_patch(
                spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True,
                inject=lambda *a, **kw: True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
            outcome = provision_pane("manager", tmux.Target("S", "manager"))
            script = _spawn_script_text(spawn_calls[0][1])
        cfg = paths.codex_config_file("manager").read_text(encoding="utf-8")

    assert outcome == READY_NO_INIT
    assert 'model = "gpt-5.4"' in cfg
    assert "--model gpt-5.4" in script
    assert "--model gpt-5.2" not in script


def test_provision_codex_sets_medium_verbosity_for_gpt_5_2():
    """REGRESSION: gpt-5.2 rejects Codex's default low text verbosity."""
    team = {
        "agents": {
            "manager": {
                "cli": "codex-cli",
                "model": "gpt-5.2",
            }
        }
    }
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda *a, **kw: True,
            inject=lambda *a, **kw: True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
        outcome = provision_pane("manager", tmux.Target("S", "manager"))
        cfg = paths.codex_config_file("manager").read_text(encoding="utf-8")

    assert outcome == READY_NO_INIT
    assert 'model = "gpt-5.2"' in cfg
    assert 'model_verbosity = "medium"' in cfg


def test_provision_codex_inherits_host_custom_provider_base_url():
    """When only auth.json is copied from a custom Codex setup, the agent
    must also inherit the custom base_url or the key is sent to api.openai.com."""
    team = {
        "agents": {
            "worker_codex": {
                "cli": "codex-cli",
                "model": "gpt-5.5",
            }
        }
    }
    with isolated_env(team=team) as tmp:
        host_codex = tmp / "host-codex"
        host_codex.mkdir(parents=True, exist_ok=True)
        (host_codex / "auth.json").write_text(
            '{"OPENAI_API_KEY":"sk-compatible"}',
            encoding="utf-8",
        )
        (host_codex / "config.toml").write_text(
            'model_provider = "custom"\n'
            'model = "gpt-5.5"\n'
            'model_reasoning_effort = "xhigh"\n'
            'disable_response_storage = true\n\n'
            '[model_providers.custom]\n'
            'name = "custom"\n'
            'wire_api = "responses"\n'
            'requires_openai_auth = true\n'
            'base_url = "https://api.fluxincode.com/v1"\n',
            encoding="utf-8",
        )
        with env_patch(CODEX_HOME=str(host_codex)), tmux_patch(
                spawn_agent=lambda *a, **kw: True,
                inject=lambda *a, **kw: True), \
                attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
            outcome = provision_pane("worker_codex", tmux.Target("S", "worker_codex"))
        cfg = paths.codex_config_file("worker_codex").read_text(encoding="utf-8")
    assert outcome == READY_NO_INIT
    assert 'model_provider = "custom"' in cfg
    assert 'model = "gpt-5.5"' in cfg
    assert 'model_reasoning_effort = "xhigh"' in cfg
    assert 'base_url = "https://api.fluxincode.com/v1"' in cfg


def test_provision_codex_copies_shared_mcp_sections_to_agent_home():
    team = {
        "agents": {
            "worker_codex": {
                "cli": "codex-cli",
                "model": "gpt-5.5",
            }
        }
    }
    with isolated_env(team=team) as tmp:
        shared_cfg = paths.codex_config_file()
        shared_cfg.parent.mkdir(parents=True, exist_ok=True)
        shared_cfg.write_text(
            '[projects."/work"]\ntrust_level = "trusted"\n\n'
            '[mcp_servers.context7]\n'
            'command = "npx"\n'
            'args = ["-y", "@upstash/context7-mcp"]\n\n'
            '[mcp_servers.devtools]\n'
            'command = "npx"\n'
            'args = ["-y", "chrome-devtools-mcp@latest"]\n',
            encoding="utf-8",
        )
        with tmux_patch(
                spawn_agent=lambda *a, **kw: True,
                inject=lambda *a, **kw: True), \
                attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
            provision_pane("worker_codex", tmux.Target("S", "worker_codex"))
        cfg = paths.codex_config_file("worker_codex").read_text(encoding="utf-8")

    assert "[mcp_servers.context7]" in cfg
    assert "@upstash/context7-mcp" in cfg
    assert "[mcp_servers.devtools]" in cfg
    assert "chrome-devtools-mcp@latest" in cfg


def test_provision_codex_copies_bundled_repo_skills_to_agent_home():
    team = {
        "agents": {
            "worker_codex": {
                "cli": "codex-cli",
                "model": "gpt-5.5",
            }
        }
    }
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda *a, **kw: True,
            inject=lambda *a, **kw: True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
        provision_pane("worker_codex", tmux.Target("S", "worker_codex"))
        skill = (
            paths.codex_home_dir("worker_codex")
            / "skills"
            / "cross-team-flow"
            / "SKILL.md"
        )
        assert skill.exists()
        text = skill.read_text(encoding="utf-8")
    assert "cross-team AI-agent collaboration" in text
    assert "Three-Layer Check" in text


# ── provision_pane: READY_NO_INIT ─────────────────────────────────


def test_provision_ready_no_init_when_marker_never_appears():
    """When wait_until_ready times out, spawn already happened so the
    pane is alive — status still flips to 进行中, but the identity
    init prompt is NOT injected (no point injecting into a CLI that
    might still be loading)."""
    team = {"agents": {"a": {"cli": "claude-code"}}}
    inject_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: True,
            inject=lambda t, text, **kw: inject_calls.append((str(t), text)) or True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
        outcome = provision_pane("a", tmux.Target("S", "a"))
        assert outcome == READY_NO_INIT
        assert inject_calls == []  # no identity init when CLI not ready
        snap = local_facts.get_status("a")
        assert snap["status"] == "进行中"  # status still flips
        assert local_facts.get_heartbeat("a") is not None


# ── provision_pane: CONFIG_ERROR (round-61) ──────────────────────


def test_provision_returns_config_error_on_unknown_cli():
    """REGRESSION: a typo in team.json's `cli` field (e.g. 'claude-cod'
    missing the e) used to raise KeyError straight through start.py,
    killing the entire claudeteam start. Now returns CONFIG_ERROR so
    the caller can warn + skip + continue with the rest of the team."""
    import io
    import contextlib
    team = {"agents": {"typo_agent": {"cli": "claude-cod"}}}  # unknown CLI
    err = io.StringIO()
    with isolated_env(team=team), \
            contextlib.redirect_stderr(err):
        outcome = provision_pane("typo_agent", tmux.Target("S", "typo_agent"))
    assert outcome == CONFIG_ERROR
    # Stderr explains which agent + what's wrong
    assert "typo_agent" in err.getvalue()
    assert "claude-cod" in err.getvalue() or "unknown cli" in err.getvalue()


# ── _ensure_claude_agent_home (R172.b) ───────────────────────────


def test_ensure_claude_agent_home_does_not_raise_when_data_missing():
    """On hosts without /data (macOS, test runners), the helper falls
    back to <state_dir>/agent-home/<agent>. Boss-flagged 2026-05-05:
    don't crash claudeteam start outside Docker."""
    import os
    if os.path.exists("/data"):
        return  # skip on Linux containers; helper does real work there
    # Must not raise on missing /data — falls back to state_dir
    lifecycle._ensure_claude_agent_home("manager")
    lifecycle._ensure_claude_agent_home("worker_cc")


def test_merge_runtime_env_into_claude_settings_overrides_host_model_aliases():
    import json
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        settings = Path(tmp) / "settings.json"
        settings.write_text(
            '{\n'
            '  "env": {\n'
            '    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",\n'
            '    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1"\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )
        lifecycle._merge_runtime_env_into_claude_settings(
            settings,
            {
                "ANTHROPIC_BASE_URL": "https://minimax.a7m.com.cn",
                "ANTHROPIC_AUTH_TOKEN": "sk-test",
                "ANTHROPIC_MODEL": "MiniMax-M2.7-highspeed",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2.7-highspeed",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2.7-highspeed",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2.7-highspeed",
            },
        )
        data = json.loads(settings.read_text(encoding="utf-8"))
    env = data["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://minimax.a7m.com.cn"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    assert env["ANTHROPIC_MODEL"] == "MiniMax-M2.7-highspeed"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "MiniMax-M2.7-highspeed"


def test_merge_runtime_env_into_claude_settings_uses_agent_override_values():
    import json
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        settings = Path(tmp) / "settings.json"
        settings.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://global.example"}}\n', encoding="utf-8")
        lifecycle._merge_runtime_env_into_claude_settings(
            settings,
            {
                "ANTHROPIC_BASE_URL": "https://cm.example/v1",
                "ANTHROPIC_AUTH_TOKEN": "sk-cm",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "minimax-m25",
            },
        )
        data = json.loads(settings.read_text(encoding="utf-8"))
    env = data["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://cm.example/v1"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-cm"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "minimax-m25"


def test_ensure_claude_agent_home_writes_keychain_extract_as_regular_file():
    """macOS host: when `security find-generic-password` succeeds, write
    the result as a *regular file* (not a symlink). Earlier impl
    symlinked to ~/.claude/.credentials.json which (a) goes stale
    versus the live keychain and (b) gets atomic-replaced by claude on
    refresh, defeating the share intent. 2026-05-07 host smoke ate
    'refreshToken: ""' for breakfast — pin the regular-file invariant."""
    import os
    import platform
    if platform.system() != "Darwin":
        return  # macOS-only path
    import subprocess
    fresh_creds = ('{"claudeAiOauth":{"accessToken":"a-tok",'
                   '"refreshToken":"r-tok","expiresAt":9999999999000}}')
    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=fresh_creds, stderr="")
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}), \
            attr_patch(subprocess, run=fake_run):
        lifecycle._ensure_claude_agent_home("manager")
        from claudeteam.agents.claude_code import agent_home
        cred = Path(agent_home("manager")) / ".claude" / ".credentials.json"
        assert cred.exists(), "creds file not materialised"
        assert not cred.is_symlink(), "expected regular file, got symlink"
        assert "r-tok" in cred.read_text(), \
            "expected fresh keychain content, got stale"


def test_ensure_claude_agent_home_overwrites_stale_creds_each_call():
    """Re-extract on every call: prior stale snapshot is overwritten so
    `claudeteam down && claudeteam up` actually re-materialises from
    keychain. Old impl gated on `if not cred_link.exists()` so the
    file never refreshed once written."""
    import os
    import platform
    if platform.system() != "Darwin":
        return
    import subprocess
    tokens = iter(["v1-tok", "v2-tok"])
    def fake_run(argv, **kw):
        tok = next(tokens, "vN-tok")
        body = ('{"claudeAiOauth":{"accessToken":"a","refreshToken":"%s",'
                '"expiresAt":9999999999000}}' % tok)
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=body, stderr="")
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}), \
            attr_patch(subprocess, run=fake_run):
        lifecycle._ensure_claude_agent_home("manager")
        from claudeteam.agents.claude_code import agent_home
        cred = Path(agent_home("manager")) / ".claude" / ".credentials.json"
        assert "v1-tok" in cred.read_text()
        lifecycle._ensure_claude_agent_home("manager")
        # Second call must replace the file with v2's content
        assert "v2-tok" in cred.read_text(), \
            "stale snapshot not overwritten on re-provision"


def test_ensure_claude_agent_home_writes_managed_mcp_and_bypass_project_state():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team) as tmp:
        home = Path.home()
        mcp = home / ".mcp.json"
        claude_json = home / ".claude.json"
        orig_mcp = mcp.read_text(encoding="utf-8") if mcp.exists() else None
        orig_claude_json = claude_json.read_text(encoding="utf-8") if claude_json.exists() else None
        try:
            mcp.write_text(
                '{"mcpServers":{"context7":{"command":"context7-mcp","args":["--transport","stdio"]}}}',
                encoding="utf-8",
            )
            claude_json.write_text('{"projects":{}}', encoding="utf-8")
            lifecycle._ensure_claude_agent_home("manager")
            from claudeteam.agents.claude_code import agent_home, managed_mcp_config
            managed = Path(managed_mcp_config("manager"))
            assert managed.exists()
            assert "context7" in managed.read_text(encoding="utf-8")
            agent_cfg = Path(agent_home("manager")) / ".claude.json"
            data = __import__("json").loads(agent_cfg.read_text(encoding="utf-8"))
            proj = data["projects"][str(Path.cwd())]
            assert proj["hasTrustDialogAccepted"] is True
            assert proj["permissions"]["allowBypass"] is True
            assert proj["workspaceConfig"]["permissionMode"] == "bypassPermissions"
        finally:
            if orig_mcp is None:
                mcp.unlink(missing_ok=True)
            else:
                mcp.write_text(orig_mcp, encoding="utf-8")
            if orig_claude_json is None:
                claude_json.unlink(missing_ok=True)
            else:
                claude_json.write_text(orig_claude_json, encoding="utf-8")


# ── _migrate_codex_wire_api ─────────────────────────────────────────


def test_migrate_wire_api_chat_to_responses():
    env = {"OPENAI_WIRE_API": "chat"}
    _migrate_codex_wire_api(env, "worker_cc")
    assert env["OPENAI_WIRE_API"] == "responses"


def test_migrate_wire_api_preserves_responses():
    env = {"OPENAI_WIRE_API": "responses"}
    _migrate_codex_wire_api(env, "worker_cc")
    assert env["OPENAI_WIRE_API"] == "responses"


def test_migrate_wire_api_preserves_other_values():
    env = {"OPENAI_WIRE_API": "custom-wire-format"}
    _migrate_codex_wire_api(env, "worker_cc")
    assert env["OPENAI_WIRE_API"] == "custom-wire-format"


def test_migrate_wire_api_noop_when_key_missing():
    env: dict[str, str] = {}
    _migrate_codex_wire_api(env, "worker_cc")
    assert "OPENAI_WIRE_API" not in env


def test_migrate_wire_api_case_insensitive():
    env = {"OPENAI_WIRE_API": "CHAT"}
    _migrate_codex_wire_api(env, "worker_cc")
    assert env["OPENAI_WIRE_API"] == "responses"
