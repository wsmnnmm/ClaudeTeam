"""Feishu chat operations: send_text, send_card.

Identity: callers pick `as_user` (True) vs `as_bot` (False, default).
User identity needs OAuth login on the lark-cli profile; bot identity
needs the app to have im:message scope.

All functions take an optional `lark_run=` callable for tests.
"""
from __future__ import annotations

import json
from typing import Callable

from claudeteam.feishu.lark import run as _real_run


def send_text(chat_id: str, text: str, *, profile: str = "", as_user: bool = False,
              reply_to: str = "", lark_run: Callable = _real_run) -> dict | None:
    """Send a plain-text message to a Feishu chat.

    Returns the lark-cli `data` dict (typically `{"chat_id": ..., "message_id": ...}`)
    on success, None on failure.
    """
    if not chat_id:
        return None
    args = [
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--text", text,
        "--as", "user" if as_user else "bot",
    ]
    if reply_to:
        args += ["--reply-to", reply_to]
    return lark_run(args, profile=profile)


def send_card(chat_id: str, card: dict, *, profile: str = "", as_user: bool = False,
              lark_run: Callable = _real_run) -> dict | None:
    """Send an interactive card.  `card` is the Feishu card schema (dict)."""
    if not chat_id:
        return None
    args = [
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", json.dumps(card, ensure_ascii=False),
        "--as", "user" if as_user else "bot",
    ]
    return lark_run(args, profile=profile)


def list_recent(chat_id: str, *, page_size: int = 20, profile: str = "",
                as_user: bool = True, lark_run: Callable = _real_run) -> list[dict]:
    """List recent messages in a chat (newest-first per Feishu API).

    Returns the `messages` array; defaults to user identity since the
    bot often lacks chat-history read permission.
    """
    if not chat_id:
        return []
    args = [
        "im", "+chat-messages-list",
        "--chat-id", chat_id,
        "--page-size", str(page_size),
        "--as", "user" if as_user else "bot",
        "--format", "json",
    ]
    data = lark_run(args, profile=profile)
    if not data:
        return []
    return list(data.get("messages") or [])
