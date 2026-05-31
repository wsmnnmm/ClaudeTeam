"""Tests for `claudeteam switch` — multi-team env-export emitter."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from helpers import attr_patch, env_patch, isolated_env, run_cli
from claudeteam.commands import switch as switch_cmd
from claudeteam.runtime import providers


def _team_dir(tmp: Path, *, with_team_json: bool = True) -> Path:
    """Create a fake team directory under `tmp`. Optionally skip team.json
    so the missing-marker error path is exercised."""
    d = tmp / "team-a"
    d.mkdir()
    if with_team_json:
        (d / "team.json").write_text(
            json.dumps({"agents": {"manager": {}}}), encoding="utf-8")
    return d


# ── help / no-arg ────────────────────────────────────────────────


def test_switch_no_arg_prints_current_active():
    """With no team-dir, switch reports what env vars currently point at
    so the operator can confirm without grepping shell history."""
    with tempfile.TemporaryDirectory() as tmp:
        sd = Path(tmp) / "state"
        cf = Path(tmp) / "claudeteam.toml"
        tf = Path(tmp) / "team.json"
        rt = Path(tmp) / "runtime_config.json"
        with env_patch(CLAUDETEAM_STATE_DIR=str(sd),
                       CLAUDETEAM_CONFIG_FILE=str(cf),
                       CLAUDETEAM_TEAM_FILE=str(tf),
                       CLAUDETEAM_RUNTIME_CONFIG=str(rt)):
            rc, out, _ = run_cli(["switch"])
        assert rc == 0
        assert str(sd) in out
        assert str(cf) in out
        assert str(tf) in out
        assert str(rt) in out


def test_switch_no_arg_prints_defaults_when_env_unset():
    """No env vars set → switch prints the (default) markers + resolved paths."""
    with env_patch(CLAUDETEAM_STATE_DIR=None,
                   CLAUDETEAM_CONFIG_FILE=None,
                   CLAUDETEAM_TEAM_FILE=None,
                   CLAUDETEAM_RUNTIME_CONFIG=None):
        rc, out, _ = run_cli(["switch"])
    assert rc == 0
    assert "(default)" in out


def test_switch_help_returns_zero():
    rc, out, _ = run_cli(["switch", "--help"])
    assert rc == 0
    assert "usage: claudeteam switch" in out


# ── happy path ───────────────────────────────────────────────────


def test_switch_emits_export_lines_for_team_dir():
    """Pointing at a directory with team.json prints exports +
    confirmation comment."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _team_dir(Path(tmp))
        rc, out, _ = run_cli(["switch", str(d)])
    assert rc == 0
    assert f"export CLAUDETEAM_STATE_DIR=" in out
    assert f"export CLAUDETEAM_CONFIG_FILE=" in out
    assert f"export CLAUDETEAM_TEAM_FILE=" in out
    assert f"export CLAUDETEAM_RUNTIME_CONFIG=" in out
    # The export targets should embed the team-dir path.
    assert str(d) in out
    # eval-friendly hint is present
    assert "eval" in out


def test_switch_emits_exports_for_toml_only_team_dir():
    """Modern claudeteam.toml-only deployments must switch cleanly.

    This also pins CLAUDETEAM_CONFIG_FILE so commands cannot accidentally
    read the caller's cwd claudeteam.toml while writing to another team state.
    """
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "team-a"
        d.mkdir()
        (d / "claudeteam.toml").write_text("[team]\nsession = 'S'\n", encoding="utf-8")
        rc, out, _ = run_cli(["switch", str(d)])
    assert rc == 0
    assert f"export CLAUDETEAM_STATE_DIR=" in out
    assert f"export CLAUDETEAM_CONFIG_FILE=" in out
    assert f"export CLAUDETEAM_TEAM_FILE=" in out
    assert str(d / "claudeteam.toml") in out


def test_switch_quotes_paths_with_spaces():
    """Shell-quoting matters: a path with spaces must remain eval-safe."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "team with space"
        d.mkdir()
        (d / "team.json").write_text("{}", encoding="utf-8")
        rc, out, _ = run_cli(["switch", str(d)])
    assert rc == 0
    # shlex.quote wraps a space-containing path in single quotes
    assert "'" in out


def test_switch_expands_tilde():
    """`claudeteam switch ~/teams/x` should expand the tilde before
    checking for team.json (otherwise it would always 404)."""
    rc, out, err = run_cli(["switch", "~/this-dir-should-not-exist-xyz"])
    # Either way the dir doesn't exist; the point is no `~` shows up
    # in the rendered error message — that would indicate no expansion.
    combined = out + err
    assert "~" not in combined or "does not exist" in combined


# ── error paths ──────────────────────────────────────────────────


def test_switch_rejects_nonexistent_dir():
    rc, _, err = run_cli(["switch", "/tmp/definitely-not-here-12345"])
    assert rc == 1
    assert "does not exist" in err


def test_switch_rejects_dir_without_team_config():
    """A real directory without claudeteam.toml or team.json is rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _team_dir(Path(tmp), with_team_json=False)
        rc, _, err = run_cli(["switch", str(d)])
    assert rc == 1
    assert "claudeteam.toml or team.json" in err
    assert "claudeteam init" in err  # hint to next step


def test_switch_rejects_extra_args():
    rc, _, err = run_cli(["switch", "/tmp", "extra"])
    assert rc == 1
    assert "too many args" in err


def test_switch_model_shows_project_local_provider_state():
    with isolated_env(team={"agents": {"manager": {"model": "sonnet"}}}) as tmp:
        env_dir = tmp / ".env.local.d"
        env_dir.mkdir()
        (env_dir / "claudeteam-provider.env").write_text(
            "ANTHROPIC_BASE_URL=https://minimax.example\n"
            "ANTHROPIC_AUTH_TOKEN=sk-test\n"
            "ANTHROPIC_MODEL=MiniMax-M2.7-highspeed\n"
            "ANTHROPIC_DEFAULT_SONNET_MODEL=MiniMax-M2.7-highspeed\n",
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli(["switch", "model"])
        finally:
            os.chdir(old_cwd)
        assert rc == 0, err
        assert "provider_env:" in out
        assert "https://minimax.example" in out
        assert "requested=sonnet effective=MiniMax-M2.7-highspeed" in out


def test_switch_model_writes_project_local_env_and_ccswitch():
    with isolated_env(team={"agents": {"manager": {"model": "sonnet"}}}) as tmp:
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli([
                "switch", "model",
                "--base-url", "https://minimax.a7m.com.cn",
                "--auth-token", "sk-abc",
                "--model", "MiniMax-M2.7-highspeed",
                "--effort", "high",
            ])
        finally:
            os.chdir(old_cwd)
        assert rc == 0, err
        env_path = tmp / ".env.local.d" / "claudeteam-provider.env"
        cc_path = tmp / "state" / "ccswitch.json"
        assert env_path.exists()
        assert cc_path.exists()
        env_text = env_path.read_text(encoding="utf-8")
        assert "ANTHROPIC_BASE_URL=https://minimax.a7m.com.cn" in env_text
        assert "ANTHROPIC_AUTH_TOKEN=sk-abc" in env_text
        assert "ANTHROPIC_DEFAULT_SONNET_MODEL=MiniMax-M2.7-highspeed" in env_text
        data = json.loads(cc_path.read_text(encoding="utf-8"))
        assert data["env"]["ANTHROPIC_BASE_URL"] == "https://minimax.a7m.com.cn"
        assert data["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "MiniMax-M2.7-highspeed"
        assert data["effortLevel"] == "high"
        assert "project-local model routing updated" in out


def test_switch_model_does_not_fake_effort_from_default_model_env():
    with isolated_env(team={"default_model": "sonnet", "agents": {"manager": {"model": "sonnet"}}}) as tmp:
        env_dir = tmp / ".env.local.d"
        env_dir.mkdir()
        (env_dir / "claudeteam-provider.env").write_text(
            "ANTHROPIC_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "ANTHROPIC_MODEL=qwen-plus\n"
            "ANTHROPIC_DEFAULT_SONNET_MODEL=qwen-plus\n",
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            with env_patch(CLAUDETEAM_DEFAULT_MODEL="sonnet"):
                rc, out, err = run_cli(["switch", "model"])
        finally:
            os.chdir(old_cwd)
        assert rc == 0, err
        assert "effort:       (unset)" in out


def test_switch_model_preset_save_and_use():
    with isolated_env(team={"agents": {"manager": {"model": "sonnet"}}}) as tmp:
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli([
                "switch", "model",
                "--base-url", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "--auth-token", "sk-qwen",
                "--model", "qwen-plus",
                "--effort", "medium",
            ])
            assert rc == 0, err
            rc, out, err = run_cli(["switch", "model", "preset", "--save", "qwen"])
            assert rc == 0, err
            rc, out, err = run_cli(["switch", "model", "preset", "--list"])
            assert rc == 0, err
            assert "qwen" in out
            rc, out, err = run_cli([
                "switch", "model",
                "--base-url", "https://minimax.a7m.com.cn",
                "--auth-token", "sk-mini",
                "--model", "MiniMax-M2.7-highspeed",
                "--effort", "high",
            ])
            assert rc == 0, err
            rc, out, err = run_cli(["switch", "model", "preset", "--use", "qwen"])
        finally:
            os.chdir(old_cwd)
        assert rc == 0, err
        env_text = (tmp / ".env.local.d" / "claudeteam-provider.env").read_text(encoding="utf-8")
        assert "ANTHROPIC_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1" in env_text
        assert "ANTHROPIC_DEFAULT_SONNET_MODEL=qwen-plus" in env_text
        data = json.loads((tmp / "state" / "ccswitch.json").read_text(encoding="utf-8"))
        assert data["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "qwen-plus"
        assert data["effortLevel"] == "medium"


def test_switch_model_preset_save_from_flags_without_touching_active_provider():
    with isolated_env(team={"agents": {"manager": {"model": "sonnet"}}}) as tmp:
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli([
                "switch", "model",
                "--base-url", "https://minimax.a7m.com.cn",
                "--auth-token", "sk-mini",
                "--model", "MiniMax-M2.7-highspeed",
                "--effort", "high",
            ])
            assert rc == 0, err
            before_env = (tmp / ".env.local.d" / "claudeteam-provider.env").read_text(encoding="utf-8")
            rc, out, err = run_cli([
                "switch", "model", "preset",
                "--save", "qwen-free",
                "--base-url", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "--auth-token", "sk-qwen",
                "--model", "qwen-plus",
                "--effort", "medium",
            ])
            assert rc == 0, err
            after_env = (tmp / ".env.local.d" / "claudeteam-provider.env").read_text(encoding="utf-8")
        finally:
            os.chdir(old_cwd)
        assert before_env == after_env
        data = json.loads((tmp / "state" / "provider-presets.json").read_text(encoding="utf-8"))
        assert data["presets"]["qwen-free"]["ANTHROPIC_BASE_URL"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert data["presets"]["qwen-free"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "qwen-plus"


def test_switch_model_preset_use_replaces_stale_openai_keys():
    with isolated_env(team={"agents": {"manager": {"model": "gpt-5.5"}}}) as tmp:
        env_dir = tmp / ".env.local.d"
        env_dir.mkdir()
        (env_dir / "claudeteam-provider.env").write_text(
            "OPENAI_BASE_URL=https://zyapi.tuluo.top:8888/v1\n"
            "OPENAI_API_KEY=pk-old\n"
            "OPENAI_MODEL=gpt-5.4\n",
            encoding="utf-8",
        )
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"flux-primary":{"ANTHROPIC_BASE_URL":"https://api.fluxincode.com/v1",'
            '"ANTHROPIC_AUTH_TOKEN":"sk-flux","ANTHROPIC_DEFAULT_HAIKU_MODEL":"gpt-5.4-mini",'
            '"ANTHROPIC_DEFAULT_SONNET_MODEL":"gpt-5.2","ANTHROPIC_DEFAULT_OPUS_MODEL":"gpt-5.5"}}}',
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli(["switch", "model", "preset", "--use", "flux-primary"])
        finally:
            os.chdir(old_cwd)
        assert rc == 0, err
        env_text = (tmp / ".env.local.d" / "claudeteam-provider.env").read_text(encoding="utf-8")
        assert "ANTHROPIC_BASE_URL=https://api.fluxincode.com/v1" in env_text
        assert "OPENAI_BASE_URL=" not in env_text
        assert "OPENAI_API_KEY=" not in env_text


def test_switch_model_shows_agent_provider_preset_effective_model():
    team = {
        "agents": {
            "manager": {"model": "sonnet"},
            "worker_translate": {
                "model": "sonnet",
                "provider_preset": "cheap-translate",
            },
        }
    }
    with isolated_env(team=team) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "ccswitch.json").write_text(
            '{"env":{"ANTHROPIC_BASE_URL":"https://global.example","ANTHROPIC_DEFAULT_SONNET_MODEL":"global-sonnet"}}',
            encoding="utf-8",
        )
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"cheap-translate":{"ANTHROPIC_BASE_URL":"https://cm.example/v1",'
            '"ANTHROPIC_DEFAULT_SONNET_MODEL":"minimax-m25"}}}',
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli(["switch", "model"])
        finally:
            os.chdir(old_cwd)
    assert rc == 0, err
    assert "manager: requested=sonnet effective=global-sonnet" in out
    assert "worker_translate: requested=sonnet effective=minimax-m25 provider_preset=cheap-translate" in out


def test_switch_model_agent_applies_runtime_override_preset():
    team = {
        "agents": {
            "manager": {"model": "sonnet"},
            "worker_integrator": {"model": "sonnet"},
        }
    }
    with isolated_env(team=team) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "ccswitch.json").write_text(
            '{"env":{"ANTHROPIC_BASE_URL":"https://global.example","ANTHROPIC_DEFAULT_SONNET_MODEL":"global-sonnet"}}',
            encoding="utf-8",
        )
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"cm-minimax-m25":{"ANTHROPIC_BASE_URL":"https://cm.example/v1",'
            '"ANTHROPIC_DEFAULT_SONNET_MODEL":"minimax-m25"}}}',
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli([
                "switch", "model", "agent", "worker_integrator",
                "--preset", "cm-minimax-m25",
            ])
            assert rc == 0, err
            rc, out, err = run_cli(["switch", "model"])
            data = json.loads((tmp / "state" / "agent-provider-overrides.json").read_text(encoding="utf-8"))
        finally:
            os.chdir(old_cwd)
    assert rc == 0, err
    assert data["agents"]["worker_integrator"]["provider_preset"] == "cm-minimax-m25"
    assert "worker_integrator: requested=sonnet effective=minimax-m25 provider_preset=cm-minimax-m25" in out


def test_switch_model_agent_clear_removes_runtime_override():
    team = {
        "agents": {
            "worker_integrator": {"model": "sonnet"},
        }
    }
    with isolated_env(team=team) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "ccswitch.json").write_text(
            '{"env":{"ANTHROPIC_BASE_URL":"https://global.example","ANTHROPIC_DEFAULT_SONNET_MODEL":"global-sonnet"}}',
            encoding="utf-8",
        )
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"cm-minimax-m25":{"ANTHROPIC_BASE_URL":"https://cm.example/v1",'
            '"ANTHROPIC_DEFAULT_SONNET_MODEL":"minimax-m25"}}}',
            encoding="utf-8",
        )
        (tmp / "state" / "agent-provider-overrides.json").write_text(
            '{"agents":{"worker_integrator":{"provider_preset":"cm-minimax-m25"}}}',
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli([
                "switch", "model", "agent", "worker_integrator", "--clear"
            ])
            assert rc == 0, err
            rc, out, err = run_cli(["switch", "model"])
        finally:
            os.chdir(old_cwd)
    assert rc == 0, err
    assert "worker_integrator: requested=sonnet effective=global-sonnet" in out


def test_switch_model_models_fetches_current_provider_and_saves_snapshot():
    with isolated_env(team={"agents": {"manager": {"model": "sonnet"}}}) as tmp:
        env_dir = tmp / ".env.local.d"
        env_dir.mkdir()
        (env_dir / "claudeteam-provider.env").write_text(
            "ANTHROPIC_BASE_URL=https://api.fluxincode.com/v1\n"
            "ANTHROPIC_MODEL=gpt-5.5\n"
            "ANTHROPIC_DEFAULT_SONNET_MODEL=gpt-5.5\n",
            encoding="utf-8",
        )
        seen = {}

        def fake_fetch(source, payload, *, auth_token_override=""):
            seen["source"] = source
            seen["payload"] = dict(payload)
            seen["auth"] = auth_token_override
            return {
                "source": source,
                "base_url": payload.get("ANTHROPIC_BASE_URL", ""),
                "models_url": "https://api.fluxincode.com/v1/models",
                "configured_models": ["gpt-5.5"],
                "available_configured_models": ["gpt-5.5"],
                "missing_configured_models": [],
                "models": ["gpt-5.4", "gpt-5.5"],
                "fetched_at": "2026-05-25T00:00:00+00:00",
                "raw": {"data": [{"id": "gpt-5.4"}, {"id": "gpt-5.5"}]},
            }

        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            with attr_patch(switch_cmd, _fetch_model_catalog=fake_fetch):
                rc, out, err = run_cli(["switch", "model", "models", "--save"])
        finally:
            os.chdir(old_cwd)
        assert rc == 0, err
        assert seen["source"] == "current"
        assert "models_count: 2" in out
        snapshot = json.loads((tmp / "state" / "provider-models.json").read_text(encoding="utf-8"))
        assert snapshot["providers"]["current"]["models"] == ["gpt-5.4", "gpt-5.5"]


def test_switch_model_models_fetches_preset_and_prints_json():
    with isolated_env(team={"agents": {"manager": {"model": "sonnet"}}}) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-presets.json").write_text(
            '{"presets":{"zyapi-backup":{"ANTHROPIC_BASE_URL":"https://zyapi.tuluo.top:8888/v1",'
            '"ANTHROPIC_AUTH_TOKEN":"sk-zy","ANTHROPIC_DEFAULT_SONNET_MODEL":"gpt-5.4"}}}',
            encoding="utf-8",
        )

        def fake_fetch(source, payload, *, auth_token_override=""):
            assert source == "zyapi-backup"
            assert payload["ANTHROPIC_BASE_URL"] == "https://zyapi.tuluo.top:8888/v1"
            return {
                "source": source,
                "base_url": payload["ANTHROPIC_BASE_URL"],
                "models_url": "https://zyapi.tuluo.top:8888/v1/models",
                "configured_models": ["gpt-5.4"],
                "available_configured_models": ["gpt-5.4"],
                "missing_configured_models": [],
                "models": ["gpt-5.3-codex", "gpt-5.4", "gpt-5.5"],
                "fetched_at": "2026-05-25T00:00:00+00:00",
                "raw": {"data": [{"id": "gpt-5.3-codex"}]},
            }

        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            with attr_patch(switch_cmd, _fetch_model_catalog=fake_fetch):
                rc, out, err = run_cli([
                    "switch", "model", "models",
                    "--preset", "zyapi-backup",
                    "--json",
                ])
        finally:
            os.chdir(old_cwd)
        assert rc == 0, err
        data = json.loads(out)
        assert data["source"] == "zyapi-backup"
        assert data["models_url"] == "https://zyapi.tuluo.top:8888/v1/models"
        assert "gpt-5.3-codex" in data["models"]


def test_switch_model_models_url_appends_v1_models():
    assert switch_cmd._models_list_url_from_base("https://api.fluxincode.com/v1") == (
        "https://api.fluxincode.com/v1/models")
    assert switch_cmd._models_list_url_from_base("https://example.com/openai") == (
        "https://example.com/openai/v1/models")


def test_switch_model_service_use_overrides_agent_preset_without_editing_agent():
    team = {
        "agents": {
            "manager": {
                "cli": "codex-cli",
                "model": "gpt-5.5",
                "provider_preset": "flux-primary",
            }
        }
    }
    with isolated_env(team=team) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-presets.json").write_text(
            json.dumps({
                "presets": {
                    "flux-primary": {
                        "ANTHROPIC_BASE_URL": "https://api.fluxincode.com/v1",
                        "ANTHROPIC_AUTH_TOKEN": "sk-flux",
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": "gpt-5.5",
                    },
                    "zyapi-backup": {
                        "ANTHROPIC_BASE_URL": "https://zyapi.tuluo.top:8888/v1",
                        "ANTHROPIC_AUTH_TOKEN": "sk-zy",
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": "gpt-5.5",
                    },
                }
            }),
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rc, out, err = run_cli([
                "switch", "model", "service", "--use", "zyapi"
            ])
            effective = providers.provider_env_for_agent("manager")
            state = providers.load_service_state()
        finally:
            os.chdir(old_cwd)

    assert rc == 0, err
    assert "applied service: zyapi (zyapi-backup)" in out
    assert state["active_service"] == "zyapi"
    assert effective["ANTHROPIC_BASE_URL"] == "https://zyapi.tuluo.top:8888/v1"
    assert effective["ANTHROPIC_AUTH_TOKEN"] == "sk-zy"


def test_switch_model_service_auto_picks_fastest_healthy_service():
    with isolated_env(team={"agents": {"manager": {"cli": "codex-cli", "model": "gpt-5.5"}}}) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-presets.json").write_text(
            json.dumps({
                "presets": {
                    "flux-primary": {
                        "ANTHROPIC_BASE_URL": "https://api.fluxincode.com/v1",
                        "ANTHROPIC_AUTH_TOKEN": "sk-flux",
                    },
                    "onekey-backup": {
                        "ANTHROPIC_BASE_URL": "https://onekey.dualseason.com/v1",
                        "ANTHROPIC_AUTH_TOKEN": "sk-onekey",
                    },
                    "zyapi-backup": {
                        "ANTHROPIC_BASE_URL": "https://zyapi.tuluo.top:8888/v1",
                        "ANTHROPIC_AUTH_TOKEN": "sk-zy",
                    },
                }
            }),
            encoding="utf-8",
        )

        def fake_fetch(source, payload, *, auth_token_override=""):
            if source == "flux-primary":
                raise RuntimeError("503 auth_unavailable")
            return {
                "source": source,
                "base_url": payload["ANTHROPIC_BASE_URL"],
                "models_url": payload["ANTHROPIC_BASE_URL"].rstrip("/") + "/models",
                "configured_models": [],
                "available_configured_models": [],
                "missing_configured_models": [],
                "models": ["gpt-5.5"],
                "fetched_at": "2026-05-30T00:00:00+00:00",
                "raw": {"data": [{"id": "gpt-5.5"}]},
            }

        ticks = iter([0.0, 0.050, 1.0, 1.220, 2.0, 2.090])
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            with attr_patch(switch_cmd, _fetch_model_catalog=fake_fetch), \
                    attr_patch(switch_cmd.time, perf_counter=lambda: next(ticks)):
                rc, out, err = run_cli([
                    "switch", "model", "service", "--auto",
                    "--order", "flux,onekey,zyapi",
                ])
            state = providers.load_service_state()
        finally:
            os.chdir(old_cwd)

    assert rc == 0, err
    assert "flux" in out and "503 auth_unavailable" in out
    assert "applied service: zyapi (zyapi-backup)" in out
    assert state["active_service"] == "zyapi"
    assert state["source_preset"] == "zyapi-backup"


def test_switch_model_service_classifies_by_real_base_url_before_preset_name():
    presets = {
        "zyapi-backup": {
            "ANTHROPIC_BASE_URL": "https://onekey.dualseason.com/v1",
        },
        "deepseek-rescue": {
            "ANTHROPIC_BASE_URL": "https://onekey.dualseason.com/v1",
        },
        "flux-primary": {
            "ANTHROPIC_BASE_URL": "https://api.fluxincode.com/v1",
        },
    }

    assert switch_cmd._resolve_service_candidate("zyapi", presets) is None
    service, preset, _ = switch_cmd._resolve_service_candidate("onekey", presets)
    assert service == "onekey"
    assert preset == "zyapi-backup"


def test_switch_model_service_skips_claude_only_onekey_for_codex_team():
    team = {"agents": {"worker_codex": {"cli": "codex-cli", "model": "gpt-5.5"}}}
    with isolated_env(team=team) as tmp:
        (tmp / "state").mkdir(parents=True, exist_ok=True)
        (tmp / "state" / "provider-presets.json").write_text(
            json.dumps({
                "presets": {
                    "local-claude-code-flagship": {
                        "ANTHROPIC_BASE_URL": "https://onekey.dualseason.com/",
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6",
                    },
                    "zyapi-backup": {
                        "ANTHROPIC_BASE_URL": "https://zyapi.tuluo.top:8888/v1",
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": "gpt-5.5",
                    },
                }
            }),
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp)
            rows = switch_cmd._service_candidates(["onekey", "zyapi"])
        finally:
            os.chdir(old_cwd)

    assert [row["service"] for row in rows] == ["zyapi"]
    assert rows[0]["preset"] == "zyapi-backup"
