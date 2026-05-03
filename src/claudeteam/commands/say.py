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
    "[--reply <message_id>] [--as user|bot] [--no-local] [--card]"
)


# Card colors per agent role conventions. manager → blue (default visual
# weight), worker_* → green (status updates), boss-tagged → grey (just
# context). Unknown agents fall back to blue. Round-99: extracted so a
# future deployment with custom roles can override without touching the
# render code.
_AGENT_CARD_COLORS = {
    "manager": "blue",
}


def _color_for(agent: str) -> str:
    if agent in _AGENT_CARD_COLORS:
        return _AGENT_CARD_COLORS[agent]
    if agent.startswith("worker"):
        return "green"
    return "blue"


@dataclass(frozen=True)
class _Args:
    agent: str
    message: str
    reply_to: str = ""
    as_user: bool = False
    local: bool = True
    as_card: bool = False


def _parse(argv: list[str]) -> _Args | None:
    if len(argv) < 2:
        return None
    rest = list(argv)
    as_card = pop_bool_flag(rest, "--card")
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

    if args.as_card:
        # Round-99: --card wraps the message in a Feishu interactive card.
        # Title carries `[<agent>]` so attribution is still visible (the
        # plain-text path's `[<agent>] <body>` prefix is the equivalent).
        # reply_to does NOT thread for cards (Feishu interactive cards
        # don't support thread-reply); print a one-line warning if the
        # caller passed --reply with --card so the threading silently
        # going away doesn't surprise them.
        if args.reply_to:
            print(f"  ⚠️ --card ignores --reply (Feishu cards don't thread)",
                  file=sys.stderr)
        card = simple_card(f"[{args.agent}]", args.message,
                            color=_color_for(args.agent))
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
