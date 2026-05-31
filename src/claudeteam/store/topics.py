"""Lightweight topic state for Feishu group conversations.

This store is deliberately smaller than the task system.  A topic is not a
work item; it is a recoverable conversation lane:

    current topic pointer + one short capsule + optional evidence links.

Raw chat history stays in logs/inbox/artifacts.  Only the capsule is injected
back into an agent prompt, so topic switching restores context without making
every future turn heavier.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from claudeteam.runtime import paths
from claudeteam.util import flock, fmt_time_ms, now_ms, read_json, write_json


VALID_STATUSES = {"active", "closed"}
DEFAULT_STATUS = "active"
MAX_CAPSULE_CHARS = 4000
MAX_PROMPT_CAPSULE_CHARS = 1600
MAX_INITIAL_CAPSULE_CHARS = 360

_BJ_TZ = timezone(timedelta(hours=8))

# Topic drift detection — when a new message shares almost no meaningful
# terms with the current topic capsule, it's likely a different
# conversation lane and should auto-spawn a new topic.
_DRIFT_MIN_OVERLAP_RATIO = 0.15
_DRIFT_MIN_SHARED_TERMS = 1
_DRIFT_MIN_TEXT_CHARS = 15
_TOPIC_NAME_MAX_CHARS = 24

# Characters to strip when extracting key terms for drift comparison.
_TERM_SEP_CHARS = set("，。！？；：、\n\r\t\"'（）()[]【】《》<>/=+-*&^%$#@!~`|{}")


def _extract_key_terms(text: str) -> set[str]:
    """Extract key terms (2+ Chinese chars or 3+ ASCII word chars)."""
    terms: set[str] = set()
    buf: list[str] = []
    in_ascii = False

    def _flush():
        nonlocal in_ascii
        if not buf:
            return
        word = "".join(buf)
        buf.clear()
        in_ascii = False
        if len(word) >= 2:
            terms.add(word.lower())

    for ch in str(text or ""):
        if ch in _TERM_SEP_CHARS or ch.isspace():
            _flush()
            continue
        ch_is_ascii = ch.isascii() and ch.isalpha()
        if buf and ch_is_ascii != in_ascii:
            _flush()
        in_ascii = ch_is_ascii
        buf.append(ch)

    _flush()
    # Also extract 2-char Chinese bigrams for finer matching
    cleaned = "".join(
        ch for ch in str(text or "")
        if ch not in _TERM_SEP_CHARS and not ch.isspace() and not ch.isascii()
    )
    for i in range(len(cleaned) - 1):
        terms.add(cleaned[i:i + 2].lower())

    return {t for t in terms if len(t) >= 2}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def topic_drift_detected(text: str, capsule: str) -> bool:
    """True when `text` shares almost no terms with the current topic capsule.

    A low overlap means the new message is likely a different conversation
    lane and should auto-spawn a topic rather than polluting the current one.
    """
    if not capsule.strip():
        return False
    if len(str(text or "").strip()) < _DRIFT_MIN_TEXT_CHARS:
        return False
    msg_terms = _extract_key_terms(text)
    cap_terms = _extract_key_terms(capsule)
    if not msg_terms:
        return False
    overlap = _jaccard(msg_terms, cap_terms)
    shared = len(msg_terms & cap_terms)
    return overlap < _DRIFT_MIN_OVERLAP_RATIO and shared < _DRIFT_MIN_SHARED_TERMS


def auto_topic_name(text: str) -> str:
    """Derive a short topic name from the first meaningful phrase in `text`.

    Returns a display-safe name (no `#` prefix) suitable for
    `topics.switch()`. Falls back to a timestamp-based name.
    """
    cleaned = str(text or "").strip()
    # Strip leading #topic if present
    if cleaned.startswith("#"):
        for i, ch in enumerate(cleaned):
            if ch.isspace() or ch in ":：":
                cleaned = cleaned[i:].lstrip(" \t\r\n:：")
                break
    # Take first sentence (up to first 。！？\n or length cap)
    for sep in ("。", "！", "？", "\n", "，", ","):
        idx = cleaned.find(sep)
        if 0 < idx < _TOPIC_NAME_MAX_CHARS * 2:
            cleaned = cleaned[:idx]
            break
    # Strip common prefixes that don't add meaning
    for prefix in ("对了", "另外", "还有", "那个", "就是", "我想问", "问一下"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 1:
            cleaned = cleaned[len(prefix):].lstrip()
            break
    name = cleaned[:_TOPIC_NAME_MAX_CHARS].strip().rstrip("，。！？,.")
    if not name or len(name) < 2:
        from datetime import datetime
        name = datetime.now().strftime("%m%d%H%M")
    return normalize_name(name)


# ── message → topic index (for quote-reply linking) ─────────────


def _msg_topic_index_key(msg_id: str) -> str:
    return str(msg_id or "").strip()


def record_msg_topic(msg_id: str, topic_name: str) -> None:
    """Record that `msg_id` was associated with a topic.

    When a later message quote-replies to `msg_id`, we can look up which
    topic lane the parent was in and switch back to it automatically.
    """
    mid = _msg_topic_index_key(msg_id)
    if not mid:
        return
    tkey = topic_key(topic_name)
    if not tkey:
        return
    now = now_ms()
    with _locked():
        data = _load()
        idx = data.setdefault("_msg_topics", {})
        idx[mid] = {"topic_key": tkey, "recorded_at": now}
        # Prune entries older than 48h to keep the index bounded.
        cutoff = now - (48 * 3600 * 1000)
        stale = [k for k, v in idx.items()
                  if isinstance(v, dict) and v.get("recorded_at", 0) < cutoff]
        for k in stale:
            del idx[k]
        _save(data)


def lookup_parent_topic(reply_to: str) -> str | None:
    """Return the topic name for a parent message, if known."""
    mid = _msg_topic_index_key(reply_to)
    if not mid:
        return None
    data = _load()
    idx = data.get("_msg_topics", {})
    entry = idx.get(mid)
    if not isinstance(entry, dict):
        return None
    tkey = entry.get("topic_key", "")
    if not tkey:
        return None
    row = data.get("topics", {}).get(tkey)
    return str(row.get("name") or "") if row else None


def _file():
    return paths.state_dir() / "topics.json"


def _locked():
    return flock(_file().with_suffix(".lock"))


def _default() -> dict:
    return {"current": "", "topics": {}, "_meta": {"version": 1}}


def _load() -> dict:
    data = read_json(_file(), _default())
    data.setdefault("current", "")
    data.setdefault("topics", {})
    data.setdefault("_meta", {"version": 1})
    return data


def _save(data: dict) -> None:
    write_json(_file(), data)


def normalize_name(name: str) -> str:
    """Return a display-safe topic name without the leading `#`.

    Topic names are user-facing, so we preserve Chinese / mixed-case display
    text.  Matching uses `topic_key()` below.
    """
    cleaned = str(name or "").strip()
    while cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()
    return cleaned.strip(" \t\r\n:：")


def topic_key(name: str) -> str:
    return normalize_name(name).casefold()


def _match_text(value: object) -> str:
    return str(value or "").casefold()


def parse_topic_prefix(text: str) -> tuple[str, str, bool]:
    """Parse a leading `#topic` marker.

    Returns `(topic_name, body_without_marker, matched)`.  The topic name ends
    at whitespace or a Chinese/ASCII colon so both `#TeamOps hi` and
    `#TeamOps：hi` work.  `## heading` and bare `#` are ignored to avoid
    treating markdown headings as topic switches.
    """
    raw = str(text or "")
    stripped = raw.lstrip()
    if not stripped.startswith("#") or stripped.startswith("##"):
        return "", raw, False
    if len(stripped) == 1 or stripped[1].isspace():
        return "", raw, False

    i = 1
    while i < len(stripped):
        ch = stripped[i]
        if ch.isspace() or ch in ":：":
            break
        i += 1
    name = normalize_name(stripped[1:i])
    if not name:
        return "", raw, False
    body = stripped[i:].lstrip(" \t\r\n:：")
    return name, body, True


def _clip_capsule(text: str) -> str:
    text = str(text or "").strip()
    if len(text) <= MAX_CAPSULE_CHARS:
        return text
    keep = MAX_CAPSULE_CHARS - 24
    return "（前文已截断）\n" + text[-keep:].lstrip()


def _clean_sources(sources: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for src in sources or []:
        value = str(src or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out[:12]


def _topic_is_visible(row: dict, *, include_closed: bool) -> bool:
    return include_closed or row.get("status") != "closed"


def _score_match(row: dict, query: str) -> int:
    q = topic_key(query)
    if not q:
        return 0
    name = topic_key(row.get("name", ""))
    if q == name:
        return 100
    if name.startswith(q):
        return 90
    if q in name:
        return 80
    if len(name) >= 2 and name in q:
        return 70
    capsule = _match_text(row.get("capsule"))
    if q in capsule:
        return 60
    for src in row.get("sources") or []:
        if q in _match_text(src):
            return 50
    return 0


def _resolve_in(data: dict, name: str, *,
                include_closed: bool = False) -> tuple[str, dict | None, str]:
    """Resolve exact or conservative fuzzy topic reference.

    Return `(key, row, matched_by)`. `matched_by` is non-empty only when the
    caller's query was a fuzzy hit rather than the stored topic name.  If two
    candidates tie and neither is current, return no match so we do not switch
    the user's active lane incorrectly.
    """
    query = normalize_name(name)
    key = topic_key(query)
    if not key:
        return "", None, ""
    topic_rows = data.setdefault("topics", {})
    exact = topic_rows.get(key)
    if exact is not None and _topic_is_visible(exact, include_closed=include_closed):
        return key, exact, ""

    scored: list[tuple[int, int, str, dict]] = []
    for cand_key, row in topic_rows.items():
        if not _topic_is_visible(row, include_closed=include_closed):
            continue
        score = _score_match(row, query)
        if score:
            scored.append((score, int(row.get("updated_at") or 0), cand_key, row))
    if not scored:
        return "", None, ""

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    top_score = scored[0][0]
    tied = [item for item in scored if item[0] == top_score]
    if len(tied) > 1:
        current_key = topic_key(data.get("current") or "")
        current_hits = [item for item in tied if item[2] == current_key]
        if len(current_hits) == 1:
            _, _, cand_key, row = current_hits[0]
            return cand_key, row, query
        return "", None, ""

    _, _, cand_key, row = scored[0]
    return cand_key, row, query


def _new_topic(name: str, now: int) -> dict:
    return {
        "name": normalize_name(name),
        "status": DEFAULT_STATUS,
        "capsule": "",
        "sources": [],
        "created_at": now,
        "updated_at": now,
        "last_seen_at": now,
        "last_message_id": "",
    }


def _initial_capsule(body: str) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
    if len(text) > MAX_INITIAL_CAPSULE_CHARS:
        text = text[:MAX_INITIAL_CAPSULE_CHARS].rstrip() + "..."
    return f"本轮说明：{text}"


def _ensure_in(data: dict, name: str, *, now: int) -> dict:
    key = topic_key(name)
    if not key:
        raise ValueError("topic name cannot be empty")
    topics = data.setdefault("topics", {})
    row = topics.get(key)
    if row is None:
        row = _new_topic(name, now)
        topics[key] = row
    else:
        row["name"] = row.get("name") or normalize_name(name)
        row.setdefault("status", DEFAULT_STATUS)
        row.setdefault("capsule", "")
        row.setdefault("sources", [])
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
        row.setdefault("last_seen_at", now)
        row.setdefault("last_message_id", "")
    return row


def ensure(name: str) -> dict:
    now = now_ms()
    with _locked():
        data = _load()
        row = _ensure_in(data, name, now=now)
        _save(data)
        return dict(row)


def get(name: str) -> dict | None:
    data = _load()
    _, row, matched_by = _resolve_in(data, name, include_closed=True)
    if not row:
        return None
    out = dict(row)
    if matched_by:
        out["_matched_by"] = matched_by
    return out


def current() -> dict | None:
    data = _load()
    name = data.get("current") or ""
    if not name:
        return None
    row = data.get("topics", {}).get(topic_key(name))
    return dict(row) if row else None


def current_name() -> str:
    row = current()
    return str(row.get("name") or "") if row else ""


def list_topics(*, include_closed: bool = False) -> list[dict]:
    rows = [dict(r) for r in _load().get("topics", {}).values()]
    if not include_closed:
        rows = [r for r in rows if r.get("status") != "closed"]
    rows.sort(key=lambda r: int(r.get("updated_at") or 0), reverse=True)
    return rows


def switch(name: str, *, msg_id: str = "", initial_capsule: str = "") -> dict:
    display = normalize_name(name)
    if not display:
        raise ValueError("topic name cannot be empty")
    now = now_ms()
    with _locked():
        data = _load()
        previous = data.get("current") or ""
        _, row, matched_by = _resolve_in(data, display, include_closed=True)
        if row is None:
            row = _ensure_in(data, display, now=now)
        else:
            row = _ensure_in(data, str(row.get("name") or display), now=now)
        row["status"] = DEFAULT_STATUS
        if initial_capsule and not str(row.get("capsule") or "").strip():
            row["capsule"] = _clip_capsule(initial_capsule)
        row["updated_at"] = now
        row["last_seen_at"] = now
        row["last_message_id"] = str(msg_id or row.get("last_message_id") or "")
        if previous and topic_key(previous) != topic_key(row.get("name") or display):
            prev = data.get("topics", {}).get(topic_key(previous))
            if prev is not None:
                prev["last_seen_at"] = now
        data["current"] = row["name"]
        _save(data)
        out = dict(row)
        out["_previous"] = previous
        out["_changed"] = topic_key(previous) != topic_key(row.get("name") or display)
        if matched_by:
            out["_matched_by"] = matched_by
        return out


def touch_current(*, msg_id: str = "") -> dict | None:
    now = now_ms()
    with _locked():
        data = _load()
        name = data.get("current") or ""
        if not name:
            return None
        row = data.get("topics", {}).get(topic_key(name))
        if row is None:
            return None
        row["updated_at"] = now
        row["last_seen_at"] = now
        if msg_id:
            row["last_message_id"] = str(msg_id)
        _save(data)
        return dict(row)


def apply_message(text: str, *, msg_id: str = "") -> dict:
    """Update topic state for a boss chat message.

    Leading `#topic` switches lanes; messages without a marker simply touch
    the current topic, if any.  Return value is a small event dict that can be
    rendered into an agent prompt.

    Also records the msg_id → topic mapping so later quote-replies can be
    linked back to the correct topic lane.
    """
    name, body, matched = parse_topic_prefix(text)
    if matched:
        row = switch(name, msg_id=msg_id, initial_capsule=_initial_capsule(body))
        if msg_id:
            record_msg_topic(msg_id, str(row.get("name") or name))
        return {
            "kind": "switch",
            "topic": row,
            "body": body,
            "previous": row.get("_previous", ""),
            "changed": bool(row.get("_changed")),
        }
    row = touch_current(msg_id=msg_id)
    if row and msg_id:
        record_msg_topic(msg_id, str(row.get("name") or ""))
    return {
        "kind": "continue",
        "topic": row,
        "body": str(text or ""),
        "previous": "",
        "changed": False,
    }


def set_capsule(name: str, capsule: str, *,
                sources: Iterable[str] | None = None) -> dict:
    display = normalize_name(name)
    if not display:
        raise ValueError("topic name cannot be empty")
    now = now_ms()
    with _locked():
        data = _load()
        row = data.setdefault("topics", {}).get(topic_key(display))
        if row is None:
            row = _ensure_in(data, display, now=now)
        else:
            row = _ensure_in(data, str(row.get("name") or display), now=now)
        row["capsule"] = _clip_capsule(capsule)
        if sources is not None:
            row["sources"] = _clean_sources(sources)
        row["updated_at"] = now
        _save(data)
        return dict(row)


def add_note(name: str, note: str, *,
             source: str = "") -> dict:
    display = normalize_name(name) or current_name()
    if not display:
        raise ValueError("no current topic; pass a topic name")
    note = str(note or "").strip()
    if not note:
        raise ValueError("note cannot be empty")
    now = now_ms()
    with _locked():
        data = _load()
        _, row, matched_by = _resolve_in(data, display, include_closed=True)
        if row is None:
            row = _ensure_in(data, display, now=now)
        else:
            row = _ensure_in(data, str(row.get("name") or display), now=now)
        old = str(row.get("capsule") or "").strip()
        bullet = f"- {note}"
        row["capsule"] = _clip_capsule("\n".join(x for x in (old, bullet) if x))
        if source:
            row["sources"] = _clean_sources([*row.get("sources", []), source])
        row["updated_at"] = now
        _save(data)
        out = dict(row)
        if matched_by:
            out["_matched_by"] = matched_by
        return out


def close(name: str = "") -> dict | None:
    display = normalize_name(name) or current_name()
    if not display:
        return None
    now = now_ms()
    with _locked():
        data = _load()
        _, row, matched_by = _resolve_in(data, display, include_closed=True)
        if row is None:
            return None
        row["status"] = "closed"
        row["updated_at"] = now
        if topic_key(data.get("current") or "") == topic_key(row.get("name") or display):
            data["current"] = ""
        _save(data)
        out = dict(row)
        if matched_by:
            out["_matched_by"] = matched_by
        return out


def _beijing_time_ms(ms: int) -> str:
    if not ms:
        return "?"
    return datetime.fromtimestamp(ms / 1000, tz=_BJ_TZ).strftime("%Y-%m-%d %H:%M 北京时间")


_FIELD_ALIASES = {
    "目标": "目标",
    "目的": "目标",
    "要解决": "目标",
    "当前判断": "当前判断",
    "判断": "当前判断",
    "结论": "当前判断",
    "现状": "当前判断",
    "状态": "当前判断",
    "本轮说明": "当前判断",
    "下一步": "下一步",
    "next": "下一步",
    "边界": "边界",
    "约束": "边界",
    "不要": "边界",
    "禁止": "边界",
}


def _split_capsule_fields(capsule: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "目标": [],
        "当前判断": [],
        "下一步": [],
        "边界": [],
    }
    fallback: list[str] = []
    for raw_line in str(capsule or "").splitlines():
        line = raw_line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        label = ""
        value = ""
        for sep in ("：", ":"):
            if sep in line:
                left, right = line.split(sep, 1)
                label = left.strip().casefold()
                value = right.strip()
                break
        bucket = _FIELD_ALIASES.get(label)
        if bucket and value:
            sections[bucket].append(value)
        else:
            fallback.append(line)
    if fallback and not sections["当前判断"]:
        sections["当前判断"] = fallback
    return sections


def _clip_display(text: str, limit: int = 900) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n（已截断，完整胶囊仍保存在 topic store）"


def render_topic_card(row: dict | None) -> str:
    """Render a boss-facing Feishu card body for one topic."""
    if not row:
        return "当前没有命中的话题。可以用 `#话题名` 开一个，或用 `/topic` 看已有话题。"

    status = "已关闭" if row.get("status") == "closed" else "进行中"
    lines = [
        f"**当前话题**：`#{row.get('name', '')}`",
        f"**状态**：{status}",
        f"**胶囊更新时间**：{_beijing_time_ms(int(row.get('updated_at') or 0))}",
    ]
    if row.get("_matched_by"):
        lines.append(f"**匹配词**：`{row.get('_matched_by')}` → `#{row.get('name', '')}`")

    capsule = str(row.get("capsule") or "").strip()
    if not capsule:
        lines.extend([
            "",
            "**当前判断**",
            "暂无胶囊。新话题第一句如果带说明，会自动写入这里。",
        ])
    else:
        sections = _split_capsule_fields(capsule)
        wrote = False
        for title in ("目标", "当前判断", "下一步", "边界"):
            values = sections.get(title) or []
            if not values:
                continue
            lines.extend(["", f"**{title}**", _clip_display("\n".join(values))])
            wrote = True
        if not wrote:
            lines.extend(["", "**当前判断**", _clip_display(capsule)])

    sources = row.get("sources") or []
    if sources:
        lines.extend(["", f"**证据**：{len(sources)} 条"])
    return "\n".join(lines)


def render_topic(row: dict | None) -> str:
    if not row:
        return "当前没有话题。用 `#话题名` 或 `claudeteam topic switch <name>` 建一个。"
    lines = [
        f"topic: #{row.get('name', '')}",
        f"status: {row.get('status', DEFAULT_STATUS)}",
        f"updated: {fmt_time_ms(int(row.get('updated_at') or 0))}",
        "",
        "capsule:",
        str(row.get("capsule") or "（暂无胶囊，切换后会按空白话题继续）"),
    ]
    sources = row.get("sources") or []
    if sources:
        lines.extend(["", "sources:", *[f"- {src}" for src in sources]])
    return "\n".join(lines)


def render_event_for_prompt(event: dict) -> str:
    row = event.get("topic") if event else None
    if not row:
        return (
            "\n[话题上下文] 当前没有已绑定话题。先对照 "
            "`claudeteam topic list --all` 和 docs/claudeteam/topic-index.md "
            "判断本条属于哪个既有 topic；若是新主线，先 "
            "`claudeteam topic switch <name>` 并写一屏恢复胶囊。"
            "回群时说明“归到 #话题名”。\n"
        )
    name = row.get("name", "")
    kind = "切换" if event.get("kind") == "switch" else "延续"
    prev = event.get("previous") or ""
    capsule = str(row.get("capsule") or "").strip()
    if not capsule:
        capsule = "暂无胶囊；先按用户当前消息做最小查证，必要时再补 `claudeteam topic note`。"
    elif len(capsule) > MAX_PROMPT_CAPSULE_CHARS:
        capsule = capsule[:MAX_PROMPT_CAPSULE_CHARS].rstrip() + "\n（胶囊过长，已截断注入）"
    sources = row.get("sources") or []
    source_line = ""
    if sources:
        source_line = "\n证据入口：" + "；".join(str(s) for s in sources[:4])
    prev_line = f"（从 #{prev} 切来）" if prev and event.get("changed") else ""
    return (
        f"\n[话题上下文] 当前话题#{name}，本条为{kind}{prev_line}。"
        f"只加载下面胶囊，不要把完整群聊历史塞进上下文；需要更多证据再查 sources/raw logs。\n"
        f"胶囊：\n{capsule}{source_line}\n"
    )
