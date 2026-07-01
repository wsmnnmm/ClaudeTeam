"""Tests for `claudeteam install-hooks` — Claude Code slash-command markdowns."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from claudeteam.commands import install_hooks

from helpers import attr_patch, isolated_env, run_cli, tmux_patch


# ── happy path ──────────────────────────────────────────────────


def test_install_hooks_creates_md_per_command():
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, _ = run_cli(["install-hooks", tmp])
        assert rc == 0

        cmds_dir = Path(tmp) / ".claude" / "commands"
        assert cmds_dir.exists()
        # Round-94 added remember/recall, round-104 added peek to keep
        # slash dispatch consistent with the manager identity v2's 巡视
        # cadence (was hard-coded raw tmux capture-pane).
        for name in ("inbox", "team", "status", "say", "task", "topic", "health",
                     "remember", "recall", "peek"):
            assert (cmds_dir / f"{name}.md").exists(), f"missing {name}.md"
        assert "wrote 10 slash command" in out


def test_install_hooks_peek_md_documents_5min_cadence():
    """Round-104: /peek hook teaches `claudeteam peek <agent> [N]` as
    the branded 5-min 巡视 path, replacing manager identity v2's
    hard-coded raw `tmux capture-pane -t {session}:<agent>`."""
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        body = (Path(tmp) / ".claude" / "commands" / "peek.md").read_text(
            encoding="utf-8")
        assert "claudeteam peek" in body
        # The 巡视 phrase must show up so agents recognise the use-case
        assert "巡视" in body or "cadence" in body.lower()
        # Default N + max documented (matches command's clamp)
        assert "30" in body
        assert "2000" in body


def test_install_hooks_topic_md_documents_capsule_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        body = (Path(tmp) / ".claude" / "commands" / "topic.md").read_text(
            encoding="utf-8")
        assert "claudeteam topic" in body
        assert "topic capsule" in body


def test_install_hooks_remember_md_documents_kind_vocabulary():
    """The remember hook must teach which `kind` values are convention
    so agents don't invent free-form labels (still works but breaks
    cross-agent consistency for the boss reading recall output)."""
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        body = (Path(tmp) / ".claude" / "commands" / "remember.md").read_text(
            encoding="utf-8")
        for kind in ("task_assigned", "task_completed", "learning",
                     "blocker", "decision", "note"):
            assert kind in body
        assert "claudeteam remember" in body


def test_install_hooks_say_md_documents_card_only_after_R169():
    """Removed --no-card escape hatch — every chat message is a card.
    The hook doc no longer mentions any plain-text path so claude
    agents don't try to opt out."""
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        body = (Path(tmp) / ".claude" / "commands" / "say.md").read_text(
            encoding="utf-8")
        # Card-only messaging surfaced
        assert "v2 card" in body
        assert "--no-card" not in body  # escape hatch gone
        # Invocation form documented
        assert "claudeteam say <your-name> - --to user" in body
        assert "cat <<'EOF'" in body
        # Threading caveat surfaced
        assert "thread" in body.lower() or "ignored" in body.lower()


def test_install_hooks_idempotent_overwrites_existing_files():
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        # tweak one to test overwrite
        team_path = Path(tmp) / ".claude" / "commands" / "team.md"
        team_path.write_text("STALE", encoding="utf-8")

        rc, out, _ = run_cli(["install-hooks", tmp])
        assert rc == 0
        assert "updated" in out
        assert "STALE" not in team_path.read_text(encoding="utf-8")


def test_install_hooks_default_target_is_cwd():
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            rc, _, _ = run_cli(["install-hooks"])
            assert rc == 0
            assert (Path(tmp) / ".claude" / "commands" / "team.md").exists()
        finally:
            os.chdir(cwd)


def test_install_hooks_say_md_mentions_chat():
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        say_md = (Path(tmp) / ".claude" / "commands" / "say.md").read_text(encoding="utf-8")
        assert "Feishu chat" in say_md
        assert "claudeteam say" in say_md


def test_install_hooks_normalizes_cost_guard_hook_to_single_current_command():
    with tempfile.TemporaryDirectory() as tmp:
        claude_dir = Path(tmp) / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{
                            "type": "command",
                            "command": "bash /Users/wsm/Project/ClaudeTeam/scripts/check-api-cost.sh",
                        }],
                    },
                    {
                        "matcher": "Bash",
                        "hooks": [{
                            "type": "command",
                            "command": "bash /srv/ai/ClaudeTeam/scripts/check-api-cost.sh",
                        }],
                    },
                    {
                        "matcher": "Edit",
                        "hooks": [{
                            "type": "command",
                            "command": "echo keep-me",
                        }],
                    },
                    {
                        "matcher": "Bash",
                        "hooks": [{
                            "type": "command",
                            "command": "bash /srv/ai/ClaudeTeam/scripts/check-api-cost.sh",
                        }],
                    },
                ]
            }
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        with attr_patch(
            install_hooks,
            _find_hook_script=lambda: "/srv/ai/ClaudeTeam/scripts/check-api-cost.sh",
        ):
            rc, out, err = run_cli(["install-hooks", tmp])
            assert rc == 0
            assert err == ""
            assert "API cost guard hook" in out

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        pretool = settings["hooks"]["PreToolUse"]
        bash_cost_entries = [
            entry for entry in pretool
            if entry.get("matcher") == "Bash"
            and any(
                "check-api-cost.sh" in str(hook.get("command", ""))
                for hook in entry.get("hooks", [])
                if isinstance(hook, dict)
            )
        ]
        assert len(bash_cost_entries) == 1
        command = bash_cost_entries[0]["hooks"][0]["command"]
        assert command.endswith("scripts/check-api-cost.sh")
        assert "/Users/wsm/Project/ClaudeTeam/scripts/check-api-cost.sh" not in command
        assert any(entry.get("matcher") == "Edit" for entry in pretool)


# ── parsing ──────────────────────────────────────────────────────


def test_install_hooks_too_many_args_returns_one():
    rc, _, err = run_cli(["install-hooks", "/a", "/b"])
    assert rc == 1
    assert "usage:" in err


# ── pane-staleness warning ────────────────────────────────────────


def test_install_hooks_warns_when_session_already_running():
    """REGRESSION: round 5 smoke G15b — running install-hooks AFTER
    `claudeteam up` is the wrong order; existing claude-code panes
    have already cached their slash commands and won't pick up the
    new files. install-hooks should warn loudly."""
    team = {"session": "ClaudeTeam", "agents": {"manager": {}}}
    with isolated_env(team=team) as tmp, \
            tmux_patch(has_session=lambda s: s == "ClaudeTeam"):
        run_cli(["install-hooks", str(tmp)])
        team_path = Path(tmp) / ".claude" / "commands" / "team.md"
        team_path.write_text("STALE", encoding="utf-8")
        rc, _, err = run_cli(["install-hooks", str(tmp)])
        assert rc == 0
        # warning lands on stderr (via util.warn)
        assert "tmux session 'ClaudeTeam' is already running" in err
        assert "claudeteam down && claudeteam up" in err


def test_install_hooks_does_not_warn_when_session_running_but_files_unchanged():
    team = {"session": "ClaudeTeam", "agents": {"manager": {}}}
    with isolated_env(team=team) as tmp, \
            tmux_patch(has_session=lambda s: s == "ClaudeTeam"):
        first_rc, _, _ = run_cli(["install-hooks", str(tmp)])
        assert first_rc == 0
        rc, out, err = run_cli(["install-hooks", str(tmp)])
        assert rc == 0
        assert "updated" not in out
        assert "unchanged" in out
        assert "already running" not in err


def test_install_hooks_silent_when_no_session():
    team = {"session": "ClaudeTeam", "agents": {"manager": {}}}
    with isolated_env(team=team) as tmp, \
            tmux_patch(has_session=lambda s: False):
        rc, _, err = run_cli(["install-hooks", str(tmp)])
        assert rc == 0
        assert "already running" not in err
