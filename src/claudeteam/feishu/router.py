"""Pure routing decisions for inbound Feishu events.

Given a Feishu message event dict and the team's agent list, decide one of:
  - DROP:      dedup, cross-team, bot self-talk, empty text, no msg_id,
               agent message with no @target
  - SLASH:     text starts with `/` after stripping any `[<sender>] `
               prefix → router-level zero-LLM dispatch
               (handled by `feishu/slash.dispatch`)
  - BROADCAST: `@team` / `@all` / `全体X` triggers fan-out to every
               non-sender agent (R36 + R42 token-boundary fix to include
               ASCII period in the @-name terminator set)
  - ROUTE:     `@<agent>` mention → deliver to those agents, OR
               unrecognised sender (= human, defaults to `default_target`)

Pure function — no I/O, no globals. `commands/router.py` calls this
once per event from the subscribe loop and `feishu/deliver.apply`
acts on the Decision.

Drop reasons (`Decision.reason`) are stable strings so log filters
can grep for them: `no_msg_id` / `dedup` / `cross_team` / `bot_self`
/ `empty` / `agent_no_target`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Action(Enum):
    DROP = "drop"
    ROUTE = "route"
    SLASH = "slash"   # operator slash command, dispatched at router-level (zero LLM)
    BROADCAST = "broadcast"  # @team / @all / 全体成员 → every non-sender agent


@dataclass(frozen=True)
class Decision:
    action: Action
    targets: list[str] = field(default_factory=list)   # agents to deliver to
    sender: str = ""                                    # parsed agent sender, if recognised
    text: str = ""                                      # cleaned message text
    msg_id: str = ""
    reason: str = ""                                    # drop reason or "" on route
    create_time: str = ""                               # epoch ms (for catchup cursor)

    def is_drop(self) -> bool:
        return self.action is Action.DROP


# Sender prefix is the bracketed form `[agent]` only.  `@agent` is treated
# as a mention regardless of position (so a human typing `@worker_cc do X`
# routes to worker_cc rather than being misread as worker_cc-as-sender).
_SENDER_RE = re.compile(r"^\s*\[([A-Za-z0-9_\-]+)\]\s*")
_MENTION_RE = re.compile(r"@([A-Za-z0-9_\-]+)")

# Broadcast triggers — match exactly these tokens or 全体 prefix.
_BROADCAST_TOKENS = ("@team", "@all", "@everyone")
_BROADCAST_PREFIX = "全体"   # matches "全体成员", "全体注意" etc.


def _is_broadcast(text: str) -> bool:
    """Detect operator broadcast: `@team` / `@all` / `@everyone` or
    a Chinese 全体X phrase."""
    if not text:
        return False
    if _BROADCAST_PREFIX in text:
        return True
    # Token-aware check (avoid matching @teammate or @allowance) — the
    # token must end at whitespace, EOL, or punctuation. Both ASCII and
    # full-width punct counts: an operator ending a sentence with
    # "@team." (period) should still broadcast, not just "@team!" /
    # "@team,". Round-42 caught the missing ASCII period.
    for tok in _BROADCAST_TOKENS:
        if re.search(rf"(^|\s){re.escape(tok)}(\s|$|[，。,.!?])", text):
            return True
    return False


def _parse_sender(text: str, agents: set[str]) -> tuple[str, str]:
    """If the message starts with `[agent]` and `agent` is on the team,
    strip it and return (agent, remaining_text); else ("", text)."""
    m = _SENDER_RE.match(text)
    if not m or m.group(1) not in agents:
        return "", text
    return m.group(1), text[m.end():].lstrip()


def _parse_mentions(text: str, agents: set[str]) -> list[str]:
    """Return @-mentioned team agents, in order, without duplicates."""
    seen: list[str] = []
    for m in _MENTION_RE.finditer(text):
        name = m.group(1)
        if name in agents and name not in seen:
            seen.append(name)
    return seen


# R174: card-title sender-extraction. Worker `claudeteam say` posts
# interactive cards with title `{emoji} {agent} · {role}`; the
# subscribe layer's text-extractor for interactive messages embeds the
# card title at the start of the extracted text. Match it here so we
# can identify which agent sent a chat message even though the
# inbound `sender_id` is the bot's open_id (one app, all agents share
# it). Manager's own messages still get dropped to avoid self-loops.
_CARD_TITLE_AGENT_RE = re.compile(
    r"(?:^|<card title=\")[^\">\n]*?(?<![\w])([A-Za-z][A-Za-z0-9_\-]+)\s*·"
)


def _card_sender_agent(text: str, agents: set[str]) -> str:
    """Return the agent name parsed from a card-format `say` message,
    or "" if not a recognizable card. Used by router to attribute
    bot-sent messages to the originating worker so manager can see
    them in inbox."""
    for m in _CARD_TITLE_AGENT_RE.finditer(text):
        candidate = m.group(1)
        if candidate in agents:
            return candidate
    return ""


def classify_event(event: dict, *,
                   team_agents: list[str],
                   chat_id: str = "",
                   bot_id: str = "",
                   seen_msg_ids: set[str] | None = None,
                   default_target: str = "manager") -> Decision:
    """Classify one inbound Feishu message event.

    R174: ALL human chat messages route to `default_target` (manager).
    `@worker_cc` and `@team` / `全体X` no longer fan out from the
    router — boss-flagged design: manager is the sole interface to
    boss; worker dispatch must go through `claudeteam send`. Bot-sent
    interactive cards from non-manager workers also route to
    manager's inbox so manager can see worker chat replies and
    summarize. Manager's own bot messages still drop (avoid loop).

    Args:
        event: dict with keys message_id, chat_id, sender_id, text, msg_type
        team_agents: list of agent names known to this deployment
        chat_id: this team's chat — events from other chats get dropped
        bot_id: this app's bot open_id — bot self-talk gets dropped UNLESS
                it parses as a non-manager worker card (then routed to manager)
        seen_msg_ids: optional dedup set; populate as you process
        default_target: agent that receives all routed messages (manager)

    Decision rules (first match wins):
        no message_id          → DROP "no_msg_id"
        seen msg_id            → DROP "dedup"
        wrong chat_id          → DROP "cross_team"
        sender == bot_id AND
          card sender is manager
            (or unidentifiable) → DROP "bot_self"
        sender == bot_id AND
          card sender is worker → ROUTE to [manager] (manager sees worker say)
        empty text             → DROP "empty"
        text starts with `/`   → SLASH (operator command, zero-LLM dispatch)
        agent-tagged sender + no @target → DROP "agent_no_target"
        else (human sender)    → ROUTE to [default_target]
    """
    agents = set(team_agents)
    msg_id = event.get("message_id", "")
    common = {"msg_id": msg_id, "create_time": str(event.get("create_time", ""))}
    if not msg_id:
        return Decision(Action.DROP, reason="no_msg_id", **common)
    if seen_msg_ids is not None and msg_id in seen_msg_ids:
        return Decision(Action.DROP, reason="dedup", **common)
    if chat_id and event.get("chat_id") and event["chat_id"] != chat_id:
        return Decision(Action.DROP, reason="cross_team", **common)

    raw_text = (event.get("text") or "").strip()

    # Bot self-talk: the app open_id sent this. Default = drop. R174
    # exception: if the card was posted by a NON-manager worker (per
    # card-title parse), route to manager's inbox so manager has
    # visibility into worker chat replies. Self-loop guard: manager's
    # own cards always drop here.
    if bot_id and event.get("sender_id") == bot_id:
        card_agent = _card_sender_agent(raw_text, agents) if raw_text else ""
        if card_agent and card_agent != default_target:
            return Decision(Action.ROUTE, targets=[default_target],
                            sender=card_agent, text=raw_text, **common)
        return Decision(Action.DROP, reason="bot_self", **common)

    if not raw_text:
        return Decision(Action.DROP, reason="empty", **common)

    # Slash command: matched at router level, NOT injected into any pane.
    # Deliver layer runs the registered handler and posts the result back
    # to chat as a bot reply. Zero LLM involvement.
    slash_text = re.sub(r"^\s*\[[^\]]+\]\s*", "", raw_text)
    if slash_text.startswith("/"):
        return Decision(Action.SLASH, text=slash_text, **common)

    sender, text = _parse_sender(raw_text, agents)

    # R174: human / unknown sender → manager only. `@worker_cc` and
    # `全体X` are no longer routing instructions; they're text content
    # for manager to read and decide how to dispatch.
    if not sender:
        return Decision(Action.ROUTE, targets=[default_target], text=text, **common)

    # agent-tagged message with no @-target → broadcast with nobody to hear it
    return Decision(Action.DROP, sender=sender, text=text,
                    reason="agent_no_target", **common)
