"""Slash-command dispatch — zero-LLM router-level handlers.

When the chat receives a message starting with `/`, `feishu/router.py`
emits `Decision(Action.SLASH, text=raw_text)`. `feishu/deliver.py` calls
`dispatch(text, ctx)` here, gets a string reply, and posts it back to
the chat as a bot message. **No worker pane is touched, no LLM runs.**

Supported commands (mirrors the old branch's contract 1:1):

    /help                    list commands
    /team                    `claudeteam team` output
    /health                  `claudeteam health` output
    /usage [view]            `claudeteam usage [--view <view>]` output
    /tmux <agent> [N]        last N (default 10) lines of agent's pane
    /send <agent> <msg>      tmux send-keys + Enter into agent's pane
    /compact <agent>         inject literal "/compact" so agent self-compacts
    /stop <agent>            send Ctrl-C to agent's pane
    /clear <agent>           inject "/clear" + re-init prompt to reset agent

Commands that need an agent name validate against the team agent set.
Commands that read pane text use `tmux capture-pane`. Commands that
shell out to `claudeteam` invoke the same in-tree CLI a worker would
(via `subprocess.run`), so output matches what an operator sees on the
host shell.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from claudeteam.agents import identity
from claudeteam.runtime import tmux


# ── context wiring ────────────────────────────────────────────────


@dataclass(frozen=True)
class SlashContext:
    """Dependency bag handed to every handler. Keeps handlers pure-ish:
    they only touch what's in here, easy to fake in tests."""
    team_agents: list[str]
    session: str
    run: Callable = subprocess.run    # for shell-out (`claudeteam <cmd>`)
    sleep: Callable = time.sleep      # for /clear's settle delay

    @property
    def agent_set(self) -> frozenset[str]:
        return frozenset(self.team_agents)


_AGENT_NAME_RE = re.compile(r"[A-Za-z0-9_\-]+")
_MAX_TMUX_LINES = 2000


def _shell(ctx: SlashContext, argv: list[str], timeout: int = 30) -> str:
    """Run a shell command via ctx.run, return stdout (or stderr on failure)."""
    try:
        r = ctx.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"⚠️ {' '.join(argv[:2])}: {e}"
    if r.returncode != 0:
        return (r.stdout or "") + (r.stderr or "") or f"⚠️ rc={r.returncode}"
    return (r.stdout or "").rstrip() or "(empty)"


# ── individual handlers ───────────────────────────────────────────


def _handle_help(text: str, ctx: SlashContext) -> str | None:
    if not re.fullmatch(r"/help\s*", text):
        return None
    return (
        "Slash commands (router-side, zero LLM):\n"
        "  /help                   — this list\n"
        "  /team                   — agent status table\n"
        "  /health                 — deployment health snapshot\n"
        "  /usage [view]           — token consumption (claude-code only)\n"
        "  /tmux <agent> [N]       — capture last N (default 10) lines of agent pane\n"
        "  /send <agent> <msg>     — type <msg> into agent's pane via tmux send-keys\n"
        "  /compact <agent>        — make agent /compact its context\n"
        "  /stop <agent>           — Ctrl-C agent (interrupt current action)\n"
        "  /clear <agent>          — wipe agent context + re-inject identity init"
    )


def _handle_team(text: str, ctx: SlashContext) -> str | None:
    if not re.fullmatch(r"/team\s*", text):
        return None
    out = _shell(ctx, ["claudeteam", "team"])
    return f"=== team ===\n{out}"


def _handle_health(text: str, ctx: SlashContext) -> str | None:
    if not re.fullmatch(r"/health\s*", text):
        return None
    out = _shell(ctx, ["claudeteam", "health"], timeout=60)
    return f"=== health ===\n{out}"


def _handle_usage(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/usage(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    argv = ["claudeteam", "usage"]
    if m.group(1):
        argv += ["--view", m.group(1)]
    out = _shell(ctx, argv, timeout=120)
    return f"=== usage ===\n{out}"


def _handle_tmux(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/tmux(?:\s+([A-Za-z0-9_\-]+))?(?:\s+(\d+))?\s*", text)
    if not m:
        return None
    agent = m.group(1) or (ctx.team_agents[0] if ctx.team_agents else "manager")
    lines = int(m.group(2)) if m.group(2) else 10
    lines = max(1, min(lines, _MAX_TMUX_LINES))
    if agent not in ctx.agent_set:
        return f"⚠️ unknown agent: `{agent}` (known: {sorted(ctx.agent_set)})"
    target = tmux.Target(ctx.session, agent)
    body = tmux.capture_pane(target, lines=lines)
    body = body.rstrip() or "(window empty)"
    return f"=== {ctx.session}:{agent} (last {lines} lines) ===\n{body}"


def _handle_send(text: str, ctx: SlashContext) -> str | None:
    if re.fullmatch(r"/send\s*", text):
        return "usage: /send <agent> <message>"
    m = re.match(r"^/send\s+(\S+)\s+(.+)$", text, re.DOTALL)
    if not m:
        if re.match(r"^/send\s+\S+\s*$", text):
            return "usage: /send <agent> <message>  (missing message body)"
        return None
    agent = m.group(1).strip()
    msg = m.group(2).strip()
    if not _AGENT_NAME_RE.fullmatch(agent):
        return f"⚠️ invalid agent name: `{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ unknown agent: `{agent}` (known: {sorted(ctx.agent_set)})"
    target = tmux.Target(ctx.session, agent)
    ok = tmux.inject(target, msg)
    return f"{'✅' if ok else '❌'} /send → {ctx.session}:{agent}\ncontent: {msg}"


def _handle_compact(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/compact(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    agent = (m.group(1) or (ctx.team_agents[0] if ctx.team_agents else "manager")).strip()
    if not _AGENT_NAME_RE.fullmatch(agent):
        return f"⚠️ invalid agent name: `{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ unknown agent: `{agent}`"
    target = tmux.Target(ctx.session, agent)
    ok = tmux.inject(target, "/compact")
    return f"{'✅' if ok else '❌'} /compact → {ctx.session}:{agent}"


def _handle_stop(text: str, ctx: SlashContext) -> str | None:
    if re.fullmatch(r"/stop\s*", text):
        return "usage: /stop <agent>"
    m = re.match(r"^/stop\s+(\S+)\s*$", text)
    if not m:
        return None
    agent = m.group(1).strip()
    if not _AGENT_NAME_RE.fullmatch(agent):
        return f"⚠️ invalid agent name: `{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ unknown agent: `{agent}`"
    target = tmux.Target(ctx.session, agent)
    ok = tmux.send_keys(target, "C-c")
    return f"{'✅' if ok else '❌'} /stop → {ctx.session}:{agent} · sent C-c"


def _handle_clear(text: str, ctx: SlashContext) -> str | None:
    if re.fullmatch(r"/clear\s*", text):
        return "usage: /clear <agent>  (wipes agent context + re-inits identity)"
    m = re.match(r"^/clear\s+(\S+)\s*$", text)
    if not m:
        return None
    agent = m.group(1).strip()
    if not _AGENT_NAME_RE.fullmatch(agent):
        return f"⚠️ invalid agent name: `{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ unknown agent: `{agent}`"
    target = tmux.Target(ctx.session, agent)
    if not tmux.inject(target, "/clear"):
        return f"❌ /clear → {ctx.session}:{agent} · failed at /clear inject"
    ctx.sleep(2.0)
    if not tmux.inject(target, identity.init_prompt(agent)):
        return f"⚠️ /clear → {ctx.session}:{agent} · /clear sent but re-init inject failed"
    return f"✅ /clear → {ctx.session}:{agent} · cleared + re-injected identity init"


# Dispatch order matters — first matching handler wins. Ordered by likely
# call frequency (status reads ahead of pane mutations).
_HANDLERS: tuple[Callable[[str, SlashContext], str | None], ...] = (
    _handle_help,
    _handle_team,
    _handle_health,
    _handle_usage,
    _handle_tmux,
    _handle_send,
    _handle_compact,
    _handle_stop,
    _handle_clear,
)


def is_slash_command(text: str) -> bool:
    """True if `text` is a recognised slash command. Pure check, no I/O."""
    if not text or not text.lstrip().startswith("/"):
        return False
    stripped = text.strip()
    # Cheap detection: does any handler claim it?
    fake_ctx = SlashContext(team_agents=[], session="")
    return any(h(stripped, fake_ctx) is not None for h in _HANDLERS)


def dispatch(text: str, ctx: SlashContext) -> str:
    """Run the first matching handler against `text`. Returns the reply
    string (always a string — caller posts it directly to chat). Unknown
    slash commands get a "use /help" suggestion."""
    if not text:
        return "⚠️ empty slash command"
    stripped = text.strip()
    for handler in _HANDLERS:
        try:
            reply = handler(stripped, ctx)
        except Exception as e:
            return f"⚠️ slash handler error: {e}"
        if reply is not None:
            return reply
    return f"⚠️ unknown slash command: `{stripped}` — try /help"
