"""`claudeteam say <agent> <message> [--reply <message_id>]`

Post a chat message as `<agent>`.  Default identity is bot; pass
`--as user` to post as the logged-in lark-cli user.  A persistent default
can be set via `CLAUDETEAM_LARK_SEND_AS=user|bot` for the whole shell.

The message is also mirrored to the local inbox (so the audit log keeps
a copy) — pass `--no-local` to skip that.

Exits non-zero if `chat_id` is unset (run setup or set runtime_config.json).
"""
from __future__ import annotations

import html
import sys
from dataclasses import dataclass

from claudeteam.feishu import chat as feishu_chat
from claudeteam.feishu.cards import simple_card
from claudeteam.runtime import config
from claudeteam.store import local_facts
from claudeteam.util import env_str, error_exit, pop_bool_flag, pop_flag, usage_error


USAGE = (
    "usage: claudeteam say <agent> [<message>] "
    "[--image <path-or-image_key>] "
    "[--reply <message_id>] [--as user|bot] [--no-local] "
    "[--to user|manager|worker_<name>]\n"
    "       use '-' as <message> to read message body from stdin"
)


# Card colors per agent. manager → blue (fixed visual weight, "boss
# answer" channel). Workers auto-cycle through _WORKER_PALETTE in
# team-config order so each worker reads as a distinct color in chat —
# 2026-05-09: previously every worker fell back to "green", making
# multi-worker dispatch cards visually indistinguishable. Per-agent
# `card_color` in claudeteam.toml still wins (override).
_AGENT_CARD_COLORS = {
    "manager": "blue",
}
_WORKER_PALETTE = ("green", "purple", "orange", "yellow")

# Default emoji per agent name. Used when claudeteam.toml doesn't
# provide an explicit `emoji` field. The card sender header
# (`{emoji} {agent} · {role}`) signals who's talking at a glance.
_DEFAULT_AGENT_EMOJI = {
    "manager": "🎯",
    "worker_cc": "💎",
    "worker_codex": "🟦",
    "worker_kimi": "🟧",
    "worker_gemini": "🟩",
    "worker_qwen": "🟪",
}


def _role_of(name: str) -> str:
    """Map agent name → role bucket used by chat.publish keys.
    Convention: 'manager' → manager; 'worker_*' → worker; 'user' → user;
    anything else → user (safe default; "对老板说" is the most common
    intent when receiver is unrecognized)."""
    if name == "manager":
        return "manager"
    if name == "user" or not name:
        return "user"
    if name.startswith("worker"):
        return "worker"
    return "user"


def _publish_allowed(sender: str, to_target: str) -> bool:
    """Look up publish rule for sender→receiver, with agent-level override.

    Priority:
      1. team.agents.<sender>.publish_overrides.{key}  (single-agent override)
      2. chat.publish.{key}                             (team-wide tunable)
      3. default True                                    (preserves pre-Step-3 behavior)

    `key` = "{sender_role}_to_{receiver_role}".

    "always" is treated as True — schema uses it as a "don't silence"
    hint but the runtime semantic is just "send".

    Agent-level override is for cases like "I want worker_cc 完工卡进群,
    but worker_codex 完工卡静默" — set worker_codex.publish_overrides
    = {worker_to_user = false} without touching the global rule.
    """
    from claudeteam.runtime import tunables
    sender_role = _role_of(sender)
    receiver_role = _role_of(to_target)
    key = f"{sender_role}_to_{receiver_role}"

    # 1. Agent-level override
    try:
        agent_cfg = config.agent_config(sender)
    except KeyError:
        agent_cfg = {}
    overrides = agent_cfg.get("publish_overrides") or {}
    if key in overrides:
        v = overrides[key]
        return v == "always" or bool(v)

    # 2. Global tunable
    val = tunables.tunable(f"chat.publish.{key}", True)
    if val == "always":
        return True
    return bool(val)


def _color_for(agent: str, cfg_color: str | None = None) -> str:
    """Resolve card header color. Per-agent `card_color` (or legacy
    `color`) in claudeteam.toml wins; else manager → blue (fixed);
    else worker_* → cycle through `_WORKER_PALETTE` in team-config
    order so multiple workers' cards are visually distinct; else
    fallback blue."""
    if cfg_color:
        return cfg_color
    if agent in _AGENT_CARD_COLORS:
        return _AGENT_CARD_COLORS[agent]
    if agent.startswith("worker"):
        try:
            agents = config.load_team().get("agents", {}) or {}
            workers = [n for n in agents if n != "manager" and n.startswith("worker")]
            idx = workers.index(agent) if agent in workers else 0
        except Exception:
            idx = 0
        return _WORKER_PALETTE[idx % len(_WORKER_PALETTE)]
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


def _escape_card_body(text: str) -> str:
    """Protect Feishu markdown from swallowing angle-bracket placeholders.

    Feishu card markdown can treat `<...>` as markup-ish content, which
    makes snippets like `<server>` or `<public>` disappear in cards.
    Escaping only the body keeps the visible text while preserving
    markdown formatting elsewhere.
    """
    return html.escape(text, quote=False)


def _normalize_visible_escapes(text: str) -> str:
    """Turn shell-visible `\\n` / `\\t` into layout whitespace for chat.

    Many operators and scripts build `claudeteam say "line1\\n- line2"`
    inside double quotes. The shell keeps those escapes literal, so
    without normalization Feishu cards show raw `\n` instead of real
    line breaks.

    Keep the transform intentionally narrow:
    - decode only `\r\n`, `\n`, `\t`
    - do not touch other backslash sequences
    - do not decode path-ish tokens such as `C:\new\test` or `/tmp/\n`
      where the backslash is more likely literal content than chat
      formatting.
    """
    if "\\" not in text:
        return text

    def _token_prefix(idx: int) -> str:
        start = idx
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        return text[start:idx]

    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\" or i + 1 >= len(text):
            out.append(ch)
            i += 1
            continue

        prefix = _token_prefix(i)
        nxt = text[i + 1]
        if any(mark in prefix for mark in (":", "/", "\\")):
            out.append(ch)
            i += 1
            continue

        if nxt == "r" and i + 3 < len(text) and text[i + 2] == "\\" and text[i + 3] == "n":
            out.append("\n")
            i += 4
            continue
        if nxt == "n":
            out.append("\n")
            i += 2
            continue
        if nxt == "t":
            out.append("\t")
            i += 2
            continue

        out.append(ch)
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class _Args:
    agent: str
    message: str = ""
    image: str = ""
    reply_to: str = ""
    as_user: bool = False
    local: bool = True
    to: str = "user"   # receiver hint for chat.publish filter; default
                       # "user" preserves backwards-compat for callers
                       # that don't pass --to (manager → user is the
                       # typical case)


def _parse(argv: list[str]) -> _Args | None:
    if len(argv) < 2:
        return None
    rest = list(argv)
    # `--card` / `--no-card` are accepted but ignored — every
    # `claudeteam say` posts a v2 card. The flags are consumed for
    # backwards-compat with operators / docs that still pass them.
    pop_bool_flag(rest, "--card")
    pop_bool_flag(rest, "--no-card")
    no_local = pop_bool_flag(rest, "--no-local")
    reply_to = pop_flag(rest, "--reply") or ""
    image = pop_flag(rest, "--image") or ""
    as_explicit = pop_flag(rest, "--as")
    to_explicit = pop_flag(rest, "--to") or "user"
    if "--reply" in rest or "--as" in rest or "--to" in rest or "--image" in rest:
        return None  # flag present but value missing
    if len(rest) < 1:
        return None
    agent = rest[0]
    rest = rest[1:]
    # `feishu.send_as` cascade: --as flag > legacy env > tunable > "bot" default.
    if as_explicit is not None:
        as_value = as_explicit
    else:
        legacy = env_str("CLAUDETEAM_LARK_SEND_AS")
        if legacy:
            as_value = legacy
        else:
            from claudeteam.runtime import tunables
            as_value = str(tunables.tunable("feishu.send_as", "bot"))
    if not rest and not image:
        return None
    return _Args(
        agent=agent,
        message=" ".join(rest),
        image=image,
        reply_to=reply_to,
        as_user=(as_value == "user"),
        local=not no_local,
        to=to_explicit,
    )


def _message_body(raw: str) -> str:
    """Resolve the user-provided message argument.

    A single '-' follows the common CLI convention: read the actual
    message body from stdin. This lets agents safely send generated
    report files with `cat report.md | claudeteam say worker_x - --to user`.
    """
    if raw == "-":
        return sys.stdin.read().strip()
    return raw


def main(argv: list[str]) -> int:
    args = _parse(argv)
    if args is None:
        return usage_error(USAGE)
    message = _normalize_visible_escapes(_message_body(args.message))
    if args.message == "-" and not message and not args.image:
        return error_exit("❌ empty stdin message for `claudeteam say <agent> -`")

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
        audit_content = message
        if args.image:
            image_note = f"[image] {args.image}"
            audit_content = f"{audit_content}\n{image_note}".strip() if audit_content else image_note
        try:
            local_facts.append_log(args.agent, "say", audit_content)
        except OSError as e:
            print(f"  ⚠️ audit log write failed for {args.agent}: {e}",
                  file=sys.stderr)

    # Resolve agent's role + emoji + color from claudeteam.toml. Used
    # for the card title (`{emoji} {agent} · {role}`) and for color
    # override. Missing config falls back to the per-agent default
    # tables defined at the top of this file.
    try:
        agent_cfg = config.agent_config(args.agent)
    except KeyError:
        agent_cfg = {}

    # Every `claudeteam say` sends a v2 card. `reply_to` is silently
    # ignored because Feishu interactive cards don't thread.
    if args.reply_to:
        print(f"  ⚠️ --reply ignored (Feishu cards don't thread)",
              file=sys.stderr)
    title = _agent_card_title(args.agent, agent_cfg)
    # `card_color` is the new field name (more specific than just "color");
    # fall back to legacy "color" so old team.json keeps working.
    cfg_color = agent_cfg.get("card_color") or agent_cfg.get("color")
    card = simple_card(title, _escape_card_body(message),
                        color=_color_for(args.agent, cfg_color))

    # Step 3: chat.publish filter — operator can silence specific
    # sender→receiver channels via toml (default all true = preserve
    # pre-Step-3 behavior). Audit log was already written above
    # regardless of publish state, so silenced messages still leave a
    # trail.
    if not _publish_allowed(args.agent, args.to):
        from claudeteam.runtime import tunables
        sender_role = _role_of(args.agent)
        receiver_role = _role_of(args.to)
        key = f"chat.publish.{sender_role}_to_{receiver_role}"
        print(f"📝 {args.agent} → silenced by [{key}]=false; logged only")
        return 0

    image_result = None
    if args.image:
        image_result = feishu_chat.send_image(
            chat, args.image,
            profile=profile,
            as_user=args.as_user,
        )
        if image_result is None:
            return error_exit(f"❌ Feishu image send failed for {args.agent}")

    result = {}
    if message:
        result = feishu_chat.send_card(
            chat, card,
            profile=profile,
            as_user=args.as_user,
        )
        if result is None:
            return error_exit(f"❌ Feishu send failed for {args.agent}")

    image_msg_id = image_result.get("message_id", "") if image_result else ""
    msg_id = result.get("message_id", "") if result else ""
    if image_msg_id and msg_id:
        print(f"✅ {args.agent} → chat (image_id={image_msg_id}, message_id={msg_id})")
    elif image_msg_id:
        print(f"✅ {args.agent} → chat (image_id={image_msg_id})")
    else:
        print(f"✅ {args.agent} → chat (message_id={msg_id})")
    return 0
