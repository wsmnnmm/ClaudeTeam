"""Text cleanup helpers for Feishu-visible content."""
from __future__ import annotations

from typing import Any


def _unwrap_accidental_json_string(text: str) -> str:
    """Drop one or two accidental outer JSON-string wrappers.

    This catches shapes like ``"今日主线\\n..."`` that appear when a string
    has already been JSON-encoded before being stored in a JSON field.
    """
    value = str(text)
    for _ in range(2):
        stripped = value.strip()
        if (len(stripped) >= 2
                and stripped[0] == stripped[-1] == '"'
                and any(tok in stripped for tok in (r"\n", r"\r", r"\t", r"\""))):
            value = stripped[1:-1].replace(r"\"", '"')
            continue
        break
    return value


def normalize_visible_escapes(text: str) -> str:
    """Turn visible ``\\n`` / ``\\t`` into layout whitespace.

    Keep the transform narrow:
    - decode only ``\\r\\n``, ``\\n``, ``\\t``
    - do not touch other backslash sequences
    - avoid path-ish tokens such as ``C:\\new\\test`` or ``/tmp/\\n/cache``
    """
    text = _unwrap_accidental_json_string(str(text))
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
        previous_visible_escape = prefix.endswith((r"\n", r"\r\n", r"\t"))
        path_like = (
            prefix.endswith(("/", "\\", ":"))
            or ("\\" in prefix and not previous_visible_escape)
        )
        if path_like:
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


def sanitize_card_payload(value: Any) -> Any:
    """Recursively clean every string leaf in a Feishu card payload."""
    if isinstance(value, str):
        return normalize_visible_escapes(value)
    if isinstance(value, list):
        return [sanitize_card_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_card_payload(item) for item in value)
    if isinstance(value, dict):
        return {key: sanitize_card_payload(item) for key, item in value.items()}
    return value
