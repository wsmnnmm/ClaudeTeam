"""`claudeteam install-hooks` — drop slash-command markdowns + PreToolUse
API cost guard for Claude Code agents.

Writes `.claude/commands/{name}.md` files and `.claude/settings.json`
with the PreToolUse API cost guard hook.

Slash commands: /inbox /team /status /say /task /topic /health
/remember /recall /peek

PreToolUse hook: intercepts Bash calls → check-api-cost.sh → warns on
paid API patterns, blocks when session budget exceeded.

Idempotent — overwrites existing files. Codex and Kimi panes ignore
.claude/ so this is harmless for them.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from claudeteam.runtime import config, tmux
from claudeteam.util import atomic_write_text, maybe_print_help, usage_error, warn


USAGE = "usage: claudeteam install-hooks [path]   (default: $PWD)"


_HEAD = """\
You are a ClaudeTeam agent. Read $CLAUDETEAM_STATE_DIR/agents/<your-name>/identity.md
to confirm your name. If env is unset, look up YOUR pane's window name
explicitly — never the global active one — with:

    tmux display-message -t "$TMUX_PANE" -p '#W'

(Bare `tmux display-message -p '#W'` returns whatever window the operator
is focused on, NOT yours; that path made the manager pane self-identify
as `worker_kimi` in round 7 smoke and call `claudeteam say worker_kimi`.)

"""


# Commands whose body uses <your-name> need _HEAD prepended at write
# time. `health` is name-agnostic (it inspects the team, not self).
_COMMANDS: dict[str, str] = {
    "inbox": (
        "Run `claudeteam inbox <your-name>` to list unread messages. "
        "Acknowledge each with `claudeteam read <local_id>` once you start work on it.\n"
    ),
    "team": (
        "Run `claudeteam team` to see every agent's status + heartbeat. "
        "Use this before delegating to confirm targets are alive.\n"
    ),
    "status": (
        "Run `claudeteam status <your-name> <state> <task>` where state is one of "
        "`进行中 / 已完成 / 阻塞 / 待命`. Update at every meaningful transition.\n"
    ),
    "say": (
        "Take the user's argument as the message to post in the Feishu chat as you.\n"
        "\n"
        "Every `claudeteam say` posts a v2 card with a color-coded header\n"
        "(manager → blue, worker_cc → purple, worker_* → green) and a\n"
        "`{emoji} {your-name} · {your role}` title. Group chat reads as\n"
        "structured per-role updates rather than raw text.\n"
        "\n"
        "Use stdin form so shell quoting cannot rewrite Markdown, URLs,\n"
        "quotes, backticks, `$`, or backslashes before `say` receives them:\n"
        "\n"
        "```bash\n"
        "cat <<'EOF' | claudeteam say <your-name> - --to user\n"
        "【报道】当前状态：在线，正在做 X\n"
        "EOF\n"
        "printf '%s\\n' '收到' | claudeteam say <your-name> - --to user\n"
        "```\n"
        "\n"
        "`say` does not accept internal task flags. Do not pass `--task-id`,\n"
        "`--artifact`, or `--done`; if useful, mention task id/artifact inside\n"
        "a human-readable body as an audit note.\n"
        "\n"
        "Cards don't thread (`--reply <id>` is silently ignored).\n"
    ),
    "task": (
        "Manage the task tracker:\n"
        "- `claudeteam task list` to see open work\n"
        "- `claudeteam task create <assignee> <title>` to add\n"
        "- `claudeteam task done <T-id>` when finished\n"
    ),
    "topic": (
        "Run `claudeteam topic` to inspect the current conversation topic. "
        "Use `claudeteam topic show [name-or-clear-term]` before answering "
        "historical or topic-switching questions; clear partial terms such as "
        "`工作`, `bug`, or `T-164` can match an existing topic. Use "
        "`claudeteam topic note <one-line fact>` to keep the topic capsule "
        "short and recoverable.\n"
    ),
    "health": (
        "Run `claudeteam health` and summarize: any red checks? any agent with "
        "no heartbeat in the last 30 minutes?\n"
    ),
    # Durable per-agent memory hooks. Without these `/remember` and
    # `/recall` would go through claude-code's LLM parse path instead
    # of CLI dispatch, slower and inconsistent with the other hooks.
    "remember": (
        "Take the user's argument as a memory note for yourself. "
        "Run `claudeteam remember <your-name> <kind> \"<content>\" [--ref <ref>]` "
        "where kind is one of: task_assigned / task_completed / learning / "
        "blocker / decision / note. Memory persists across /clear and "
        "auto-injects into your next init prompt.\n"
    ),
    # Round-104: peek hook for the 5-min 巡视 cadence (manager identity v2).
    # Wraps `tmux capture-pane` so agents don't have to remember the
    # session name or pane-target syntax.
    "peek": (
        "Run `claudeteam peek <agent> [N]` to see another agent's last N pane "
        "lines (default 30, max 2000). Use this for the 5-min 巡视 cadence "
        "if you're manager — quicker than `tmux capture-pane -t ...` and the "
        "session name is auto-resolved from team.json so no typo risk. "
        "Output is plain text; pipe to grep / less / `claudeteam remember "
        "<your-name> note \"$(claudeteam peek <agent> 5)\"` to record what "
        "you saw.\n"
    ),
    "recall": (
        "Run `claudeteam recall <your-name>` to print your most recent memory "
        "entries (default last 20, oldest-first). Add `<other-agent>` instead "
        "of <your-name> to peek at another agent's memory (manager 巡视 use).\n"
    ),
}

# Every command except `health` refers to <your-name>; `health` is name-agnostic.
_NAME_AGNOSTIC = {"health", "topic"}


def _full_body(name: str) -> str:
    body = _COMMANDS[name]
    return body if name in _NAME_AGNOSTIC else _HEAD + body


def _write_command_files(target_dir: Path) -> tuple[int, int]:
    """Write each slash-command .md. Returns (created, overwritten).
    atomic_write_text handles parent mkdir, so no explicit setup needed."""
    created = 0
    overwritten = 0
    for name in _COMMANDS:
        path = target_dir / f"{name}.md"
        if path.exists():
            overwritten += 1
        else:
            created += 1
        atomic_write_text(path, _full_body(name))
    return created, overwritten


def _find_hook_script() -> str | None:
    """Find check-api-cost.sh in the repo or installed location."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "check-api-cost.sh",
        Path("/srv/ai/ClaudeTeam/scripts/check-api-cost.sh"),
        Path("/Users/wsm/Project/ClaudeTeam/scripts/check-api-cost.sh"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _write_cost_guard_hook(claude_dir: Path) -> bool:
    """Write .claude/settings.json with the PreToolUse API cost guard hook.

    If settings.json already exists, merge the hook config into it.
    Returns True if the hook was newly added.
    """
    hook_script = _find_hook_script()
    if not hook_script:
        return False

    settings_path = claude_dir / "settings.json"
    existing = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    cost_guard_hook = {
        "matcher": "Bash",
        "hooks": [{
            "type": "command",
            "command": f"bash {hook_script}",
        }],
    }

    hooks = existing.get("hooks", {})
    pretool = hooks.get("PreToolUse", [])
    # Check if cost guard is already registered
    for entry in pretool:
        if isinstance(entry, dict) and "check-api-cost" in str(entry.get("command", "")):
            return False  # already installed

    pretool.append(cost_guard_hook)
    hooks["PreToolUse"] = pretool
    existing["hooks"] = hooks

    settings_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    return True


def _remove_cost_guard_hook(claude_dir: Path) -> bool:
    """Remove the API cost guard from .claude/settings.json. Returns True if removed."""
    settings_path = claude_dir / "settings.json"
    if not settings_path.exists():
        return False
    try:
        existing = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    hooks = existing.get("hooks", {})
    pretool = hooks.get("PreToolUse", [])
    new_pretool = [
        e for e in pretool
        if not (isinstance(e, dict) and "check-api-cost" in str(e.get("command", "")))
    ]
    if len(new_pretool) == len(pretool):
        return False

    hooks["PreToolUse"] = new_pretool
    if not new_pretool:
        del hooks["PreToolUse"]
    if not hooks:
        del existing["hooks"]

    settings_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    return True


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    no_cost_guard = False
    if "--no-cost-guard" in rest:
        rest.remove("--no-cost-guard")
        no_cost_guard = True
    if len(rest) > 1:
        return usage_error(USAGE)

    base = Path(rest[0]) if rest else Path.cwd()
    claude_dir = base / ".claude"
    target = claude_dir / "commands"
    created, overwritten = _write_command_files(target)
    total = created + overwritten
    print(f"✅ wrote {total} slash command(s) to {target}")
    if overwritten:
        print(f"   ({overwritten} overwritten, {created} new)")

    if not no_cost_guard:
        added = _write_cost_guard_hook(claude_dir)
        if added:
            print(f"🛡️  API cost guard hook installed → {claude_dir / 'settings.json'}")
        else:
            script = _find_hook_script()
            if script:
                print(f"🛡️  API cost guard hook already configured")
            else:
                print(f"⚠️  check-api-cost.sh not found; skipping cost guard hook")

    print("\nClaude Code panes spawned in this directory now respond to:")
    for name in sorted(_COMMANDS):
        print(f"  /{name}")

    # Claude Code caches .claude/commands/*.md at process startup; existing
    # panes won't pick up newly-written hooks until restarted. Warn loudly.
    try:
        session = config.session_name()
    except Exception:
        session = ""
    if session and tmux.has_session(session):
        warn(
            f"\n⚠️  tmux session '{session}' is already running.\n"
            f"   Existing claude-code panes cached their slash commands at startup\n"
            f"   and WON'T see these new ones. Restart panes to pick up:\n"
            f"     claudeteam down && claudeteam up"
        )
    return 0
