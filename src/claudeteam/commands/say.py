"""`claudeteam say <agent> <message> [--reply <message_id>]`

Post a chat message as `<agent>`.  Default identity is bot; pass
`--as user` to post as the logged-in lark-cli user.  A persistent default
can be set via `CLAUDETEAM_LARK_SEND_AS=user|bot` for the whole shell.

The message is also mirrored to the local inbox (so the audit log keeps
a copy) — pass `--no-local` to skip that.

Exits non-zero if `chat_id` is unset (run setup or set runtime_config.json).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from claudeteam.feishu import chat as feishu_chat
from claudeteam.feishu.cards import simple_card
from claudeteam.runtime import config
from claudeteam.store import local_facts
from claudeteam.util import env_str, error_exit, pop_bool_flag, pop_flag, usage_error


USAGE = (
    "usage: claudeteam say <agent> <message> "
    "[--reply <message_id>] [--as user|bot] [--no-local] "
    "[--no-card | --card]"
)


# Card colors per agent role conventions. manager → blue (default visual
# weight), worker_* → green (status updates), boss-tagged → grey (just
# context). Unknown agents fall back to blue. Round-99: extracted so a
# future deployment with custom roles can override without touching the
# render code.
_AGENT_CARD_COLORS = {
    "manager": "blue",
}

# R169: default emoji per role keyword. Used when team.json doesn't
# provide an explicit `emoji` field for the agent. Mirrors `main`'s
# AGENTS dict shape (each role gets a glyph) so the card sender header
# at-a-glance signals who's talking.
_DEFAULT_AGENT_EMOJI = {
    "manager": "🎯",
    "worker_cc": "💎",
    "worker_codex": "🟦",
    "worker_kimi": "🟧",
    "worker_gemini": "🟩",
    "worker_qwen": "🟪",
}


def _color_for(agent: str, cfg_color: str | None = None) -> str:
    """Resolve card header color. Per-agent `color` field in team.json
    wins; else manager → blue, worker_* → green, fallback blue."""
    if cfg_color:
        return cfg_color
    if agent in _AGENT_CARD_COLORS:
        return _AGENT_CARD_COLORS[agent]
    if agent.startswith("worker"):
        return "green"
    return "blue"


def _emoji_for(agent: str, cfg_emoji: str | None = None) -> str:
    """Resolve sender emoji. team.json `emoji` field wins, otherwise
    fall back to `_DEFAULT_AGENT_EMOJI`, otherwise ⚙️ (system)."""
    if cfg_emoji:
        return cfg_emoji
    return _DEFAULT_AGENT_EMOJI.get(agent, "⚙️")


def _agent_card_title(agent: str, cfg: dict) -> str:
    """Card title format ported from `main`'s `_agent_card_title`:
    `{emoji} {agent} · {role}` — English agent id + Chinese role at a
    glance, no more bare `[agent]` brackets that boss flagged as too
    bland."""
    emoji = _emoji_for(agent, cfg.get("emoji"))
    role = cfg.get("role") or "系统"
    return f"{emoji} {agent} · {role}"


@dataclass(frozen=True)
class _Args:
    agent: str
    message: str
    reply_to: str = ""
    as_user: bool = False
    local: bool = True
    # R168: default flipped to True. Boss-flagged convention — every
    # agent message in chat should be a colored-header card so the
    # group reads as structured updates, not "raw chat-like text".
    # `--no-card` opts back to plain text for one-line acks.
    as_card: bool = True


def _parse(argv: list[str]) -> _Args | None:
    if len(argv) < 2:
        return None
    rest = list(argv)
    # `--card` is now a no-op (kept for backward compat); `--no-card`
    # flips to plain text. Both consume the flag.
    pop_bool_flag(rest, "--card")
    no_card = pop_bool_flag(rest, "--no-card")
    as_card = not no_card
    no_local = pop_bool_flag(rest, "--no-local")
    reply_to = pop_flag(rest, "--reply") or ""
    as_explicit = pop_flag(rest, "--as")
    if "--reply" in rest or "--as" in rest:
        return None  # flag present but value missing
    if len(rest) < 2:
        return None
    agent = rest[0]
    rest = rest[1:]
    # If --as wasn't passed, fall back to CLAUDETEAM_LARK_SEND_AS env var,
    # then to the bot default. Lets operators "set once per shell".
    as_value = as_explicit if as_explicit is not None else env_str("CLAUDETEAM_LARK_SEND_AS")
    if not rest:
        return None
    return _Args(
        agent=agent,
        message=" ".join(rest),
        reply_to=reply_to,
        as_user=(as_value == "user"),
        local=not no_local,
        as_card=as_card,
    )


def main(argv: list[str]) -> int:
    args = _parse(argv)
    if args is None:
        return usage_error(USAGE)

    chat = config.chat_id()
    if not chat:
        return error_exit("❌ chat_id not set in runtime_config.json")

    profile = config.lark_profile()

    local_facts.touch_heartbeat(args.agent)
    if args.local:
        # Audit log is best-effort — a disk-full or permission-denied
        # error here should NOT block the chat send (the boss is
        # waiting for the message to land in the group; losing the
        # local audit row is a smaller cost than losing the message).
        try:
            local_facts.append_log(args.agent, "say", args.message)
        except OSError as e:
            print(f"  ⚠️ audit log write failed for {args.agent}: {e}",
                  file=sys.stderr)

    # Resolve agent's role + emoji + color from team.json — used for
    # the card title (R169 mirrors main's `{emoji} {agent} · {role}`
    # shape) and for color override. Missing config falls back to
    # the per-agent default tables above.
    try:
        agent_cfg = config.agent_config(args.agent)
    except KeyError:
        agent_cfg = {}

    if args.as_card:
        # Round-99: --card wraps the message in a Feishu interactive card.
        # R168: card became the default. R169: title now uses the
        # `{emoji} {agent} · {role}` shape ported from main —
        # English agent id + Chinese role at a glance — so the boss
        # reads who's talking from the header without scanning the body.
        # reply_to does NOT thread for cards (Feishu interactive cards
        # don't support thread-reply); print a one-line warning if the
        # caller passed --reply with --card so the threading silently
        # going away doesn't surprise them.
        if args.reply_to:
            print(f"  ⚠️ --card ignores --reply (Feishu cards don't thread)",
                  file=sys.stderr)
        title = _agent_card_title(args.agent, agent_cfg)
        card = simple_card(title, args.message,
                            color=_color_for(args.agent, agent_cfg.get("color")))
        result = feishu_chat.send_card(
            chat, card,
            profile=profile,
            as_user=args.as_user,
        )
    else:
        result = feishu_chat.send_text(
            chat, f"[{args.agent}] {args.message}",
            profile=profile,
            as_user=args.as_user,
            reply_to=args.reply_to,
        )
    if result is None:
        return error_exit(f"❌ Feishu send failed for {args.agent}")

    msg_id = result.get("message_id", "")
    print(f"✅ {args.agent} → chat (message_id={msg_id})")
    return 0
