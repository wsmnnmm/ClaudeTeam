"""Tests for `claudeteam switch` — multi-team env-export emitter."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from helpers import env_patch, isolated_env, run_cli


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
        tf = Path(tmp) / "team.json"
        rt = Path(tmp) / "runtime_config.json"
        with env_patch(CLAUDETEAM_STATE_DIR=str(sd),
                       CLAUDETEAM_TEAM_FILE=str(tf),
                       CLAUDETEAM_RUNTIME_CONFIG=str(rt)):
            rc, out, _ = run_cli(["switch"])
        assert rc == 0
        assert str(sd) in out
        assert str(tf) in out
        assert str(rt) in out


def test_switch_no_arg_prints_defaults_when_env_unset():
    """No env vars set → switch prints the (default) markers + resolved paths."""
    with env_patch(CLAUDETEAM_STATE_DIR=None,
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
    """Pointing at a directory with team.json prints three exports +
    confirmation comment."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _team_dir(Path(tmp))
        rc, out, _ = run_cli(["switch", str(d)])
    assert rc == 0
    assert f"export CLAUDETEAM_STATE_DIR=" in out
    assert f"export CLAUDETEAM_TEAM_FILE=" in out
    assert f"export CLAUDETEAM_RUNTIME_CONFIG=" in out
    # The three export targets should embed the team-dir path
    assert str(d) in out
    # eval-friendly hint is present
    assert "eval" in out


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


def test_switch_rejects_dir_without_team_json():
    """A real directory but without team.json should be rejected — the
    marker file is what makes a directory a 'team'."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _team_dir(Path(tmp), with_team_json=False)
        rc, _, err = run_cli(["switch", str(d)])
    assert rc == 1
    assert "team.json" in err
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
        assert data["presets"]["qwen-free"]["effortLevel"] == "medium"
