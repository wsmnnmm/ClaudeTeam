"""Tests for the CLI adapter registry + each adapter's spawn / markers contract."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.agents import get_adapter, known_clis
from claudeteam.agents.base import CliAdapter
from claudeteam.agents.claude_code import ClaudeCodeAdapter
from claudeteam.agents.codex_cli import CodexCliAdapter
from claudeteam.agents.kimi_code import KimiCodeAdapter


# ── registry ──────────────────────────────────────────────────────


def test_registry_lists_known_clis_plus_kimi_and_qwen_aliases():
    """Round-85 added gemini-cli; round-101 added qwen-code (+qwen-cli
    alias). kimi-cli + qwen-cli are aliases so both forms in team.json
    work."""
    names = set(known_clis())
    assert names == {
        "claude-code", "codex-cli", "gemini-cli",
        "kimi-code", "kimi-cli",
        "qwen-code", "qwen-cli",
    }


def test_get_adapter_returns_matching_concrete_type():
    assert isinstance(get_adapter("claude-code"), ClaudeCodeAdapter)
    assert isinstance(get_adapter("codex-cli"), CodexCliAdapter)
    assert isinstance(get_adapter("kimi-code"), KimiCodeAdapter)


def test_kimi_alias_returns_same_instance():
    assert get_adapter("kimi-code") is get_adapter("kimi-cli")


def test_get_adapter_unknown_raises_keyerror_with_known_list():
    try:
        get_adapter("not-a-cli")
    except KeyError as exc:
        msg = str(exc)
        assert "unknown cli" in msg
        for name in ("claude-code", "codex-cli", "kimi-code"):
            assert name in msg
    else:
        raise AssertionError("expected KeyError for unknown cli")


# ── base + interface compliance ──────────────────────────────────


def _all_adapters() -> list[CliAdapter]:
    return [ClaudeCodeAdapter(), CodexCliAdapter(), KimiCodeAdapter()]


def test_every_adapter_implements_required_methods():
    for adapter in _all_adapters():
        assert isinstance(adapter, CliAdapter)
        cmd = adapter.spawn_cmd("worker_x", "sonnet")
        assert isinstance(cmd, str) and cmd.strip()
        ready = adapter.ready_markers()
        assert ready and isinstance(ready, list)
        busy = adapter.busy_markers()
        assert busy and isinstance(busy, list)
        assert adapter.process_name()
        assert adapter.submit_keys()


def test_default_submit_keys_are_enter_variants():
    # base default lists Enter / C-m / C-j; ClaudeCode keeps it, Codex/Kimi prepend M-Enter
    cc = ClaudeCodeAdapter().submit_keys()
    assert cc[0] == "Enter"
    for adapter in (CodexCliAdapter(), KimiCodeAdapter()):
        keys = adapter.submit_keys()
        assert keys[0] == "M-Enter"
        assert "Enter" in keys


# ── per-adapter spawn shape ──────────────────────────────────────


def test_claude_code_spawn_is_dangerously_skip_permissions_with_model():
    cmd = ClaudeCodeAdapter().spawn_cmd("worker_cc", "sonnet-4-6")
    assert "claude --permission-mode bypassPermissions --dangerously-skip-permissions" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config " in cmd
    assert "--model sonnet-4-6" in cmd
    assert "--name worker_cc" in cmd
    assert "IS_SANDBOX=1" in cmd


def test_claude_code_spawn_reads_project_local_ccswitch_settings():
    team = {
        "default_thinking": "medium",
        "agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet"}},
    }
    with isolated_env(team=team) as tmp:
        settings = tmp / "state" / "ccswitch.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            '{"env":{"ANTHROPIC_AUTH_TOKEN":"sk-test","ANTHROPIC_BASE_URL":"https://proxy.example"},'
            '"effortLevel":"max"}',
            encoding="utf-8",
        )
        cmd = ClaudeCodeAdapter().spawn_cmd("worker_cc", "sonnet")
    assert "ANTHROPIC_AUTH_TOKEN=sk-test" in cmd
    assert "ANTHROPIC_BASE_URL=https://proxy.example" in cmd
    assert "--effort max" in cmd


def test_claude_code_spawn_skips_oauth_when_third_party_token_present():
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet"}}}
    with isolated_env(team=team) as tmp:
        home = tmp / "state" / "agent-home" / "worker_cc" / ".claude"
        home.mkdir(parents=True, exist_ok=True)
        (home / ".credentials.json").write_text(
            '{"claudeAiOauth":{"accessToken":"official-token"}}',
            encoding="utf-8",
        )
        settings = tmp / "state" / "ccswitch.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            '{"env":{"ANTHROPIC_AUTH_TOKEN":"sk-third-party"}}',
            encoding="utf-8",
        )
        cmd = ClaudeCodeAdapter().spawn_cmd("worker_cc", "sonnet")
    assert "ANTHROPIC_AUTH_TOKEN=sk-third-party" in cmd
    assert "CLAUDE_CODE_OAUTH_TOKEN=" not in cmd


def test_claude_code_spawn_prefers_agent_provider_preset_over_global_settings():
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
        settings = tmp / "state" / "ccswitch.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            '{"env":{"ANTHROPIC_AUTH_TOKEN":"sk-global","ANTHROPIC_BASE_URL":"https://global.example",'
            '"ANTHROPIC_DEFAULT_SONNET_MODEL":"global-sonnet"},"effortLevel":"high"}',
            encoding="utf-8",
        )
        presets = tmp / "state" / "provider-presets.json"
        presets.write_text(
            '{"presets":{"cheap-translate":{'
            '"ANTHROPIC_BASE_URL":"https://cm.example/v1",'
            '"ANTHROPIC_AUTH_TOKEN":"sk-cm",'
            '"ANTHROPIC_MODEL":"minimax-m25",'
            '"ANTHROPIC_DEFAULT_SONNET_MODEL":"minimax-m25"}}}',
            encoding="utf-8",
        )
        cmd = ClaudeCodeAdapter().spawn_cmd("worker_translate", "minimax-m25")
    assert "ANTHROPIC_BASE_URL=https://cm.example/v1" in cmd
    assert "ANTHROPIC_AUTH_TOKEN=sk-cm" in cmd
    assert "global.example" not in cmd


def test_codex_spawn_passes_openai_model_through():
    cmd = CodexCliAdapter().spawn_cmd("worker_codex", "gpt-5.5")
    assert "codex" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--model gpt-5.5" in cmd
    assert "CODEX_AGENT=worker_codex" in cmd


def test_codex_spawn_drops_non_openai_model():
    cmd = CodexCliAdapter().spawn_cmd("worker_codex", "sonnet")
    assert "--model" not in cmd  # silently dropped
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd


def test_codex_spawn_quotes_agent_name_with_special_chars():
    cmd = CodexCliAdapter().spawn_cmd("worker x", "")
    assert "'worker x'" in cmd  # shlex.quote


def test_kimi_spawn_uses_yolo_flag_and_disable_update():
    cmd = KimiCodeAdapter().spawn_cmd("worker_kimi", "")
    assert "kimi --yolo" in cmd
    assert "DISABLE_UPDATE_CHECK=1" in cmd
    assert "KIMI_AGENT=worker_kimi" in cmd


# ── markers ──────────────────────────────────────────────────────


def test_codex_busy_markers_include_boot_phase():
    """R-busy fix carries over: Booting MCP server must be a busy marker so
    inject_when_idle waits past the boot race."""
    assert "Booting MCP server" in CodexCliAdapter().busy_markers()


def test_kimi_busy_markers_include_using_shell():
    assert "Using Shell" in KimiCodeAdapter().busy_markers()
    assert "Booting" in KimiCodeAdapter().busy_markers()


def test_process_names_match_expected_binaries():
    assert ClaudeCodeAdapter().process_name() == "claude"
    assert CodexCliAdapter().process_name() == "codex"
    assert KimiCodeAdapter().process_name() == "kimi"


# ── codex_cli.ensure_workdir_trusted ─────────────────────────────


def test_ensure_workdir_trusted_writes_entry_when_config_missing(tmp_path=None):
    import tempfile
    from pathlib import Path
    from claudeteam.agents.codex_cli import ensure_workdir_trusted

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "codex" / "config.toml"
        workdir = Path("/some/work/dir")
        ensure_workdir_trusted(workdir, config_path=cfg)
        text = cfg.read_text(encoding="utf-8")
        assert '[projects."/some/work/dir"]' in text
        assert 'trust_level = "trusted"' in text


def test_ensure_workdir_trusted_appends_when_other_entries_present():
    import tempfile
    from pathlib import Path
    from claudeteam.agents.codex_cli import ensure_workdir_trusted

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.toml"
        cfg.write_text('[projects."/other/dir"]\ntrust_level = "trusted"\n', encoding="utf-8")
        ensure_workdir_trusted(Path("/new/dir"), config_path=cfg)
        text = cfg.read_text(encoding="utf-8")
        assert '[projects."/other/dir"]' in text
        assert '[projects."/new/dir"]' in text


def test_ensure_workdir_trusted_idempotent_when_entry_exists():
    import tempfile
    from pathlib import Path
    from claudeteam.agents.codex_cli import ensure_workdir_trusted

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.toml"
        original = '[projects."/already/here"]\ntrust_level = "trusted"\n'
        cfg.write_text(original, encoding="utf-8")
        ensure_workdir_trusted(Path("/already/here"), config_path=cfg)
        # File unchanged
        assert cfg.read_text(encoding="utf-8") == original
