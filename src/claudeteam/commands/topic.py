"""Inspect and update the lightweight conversation topic store."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from claudeteam.store import tasks
from claudeteam.store import topics
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag, print_json,
    reject_extra_args, usage_error,
)


USAGE = """usage:
  claudeteam topic [current] [--json]
  claudeteam topic list [--all] [--json]
  claudeteam topic switch <name> [--json]
  claudeteam topic show [name] [--json]
  claudeteam topic note [--topic <name>] <note text> [--source <path-or-url>]
  claudeteam topic set <name> <capsule text> [--source <path-or-url> ...]
  claudeteam topic digest [--all] [--json] [--write <dir-or-file>]
  claudeteam topic close [name] [--json]

Topic names are written without the leading # here. In Feishu chat, a boss
message that starts with #topic switches the current topic automatically.
Topic lookup accepts clear partial terms such as `工作`, `bug`, or `T-164`."""


def _emit_topic(row: dict | None, *, as_json: bool) -> int:
    if as_json:
        print_json(row or {})
    else:
        print(topics.render_topic(row))
    return 0


def _handle_current(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    return _emit_topic(topics.current(), as_json=as_json)


def _handle_list(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    include_closed = pop_bool_flag(rest, "--all")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    rows = topics.list_topics(include_closed=include_closed)
    if as_json:
        print_json(rows)
        return 0
    if not rows:
        print("no topics")
        return 0
    current = topics.current_name()
    for row in rows:
        marker = "*" if row.get("name") == current else " "
        capsule = str(row.get("capsule") or "").splitlines()
        preview = capsule[0][:80] if capsule else "（无胶囊）"
        print(f"{marker} #{row.get('name')} [{row.get('status')}] {preview}")
    return 0


def _handle_switch(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    if not rest:
        return usage_error(USAGE)
    name = rest.pop(0)
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    try:
        row = topics.switch(name)
    except ValueError as e:
        return error_exit(f"❌ topic switch: {e}")
    return _emit_topic(row, as_json=as_json)


def _handle_show(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    name = rest.pop(0) if rest else ""
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    row = topics.get(name) if name else topics.current()
    return _emit_topic(row, as_json=as_json)


def _handle_note(rest: list[str]) -> int:
    source = ""
    if "--source" in rest:
        i = rest.index("--source")
        if i + 1 >= len(rest):
            return usage_error(USAGE)
        source = rest[i + 1]
        del rest[i:i + 2]
    name = pop_flag(rest, "--topic") or ""
    if not rest:
        return usage_error(USAGE)
    note = " ".join(rest).strip()
    try:
        row = topics.add_note(name, note, source=source)
    except ValueError as e:
        return error_exit(f"❌ topic note: {e}")
    print(f"topic note added: #{row.get('name')}")
    return 0


def _handle_set(rest: list[str]) -> int:
    sources: list[str] = []
    while "--source" in rest:
        i = rest.index("--source")
        if i + 1 >= len(rest):
            return usage_error(USAGE)
        sources.append(rest[i + 1])
        del rest[i:i + 2]
    if len(rest) < 2:
        return usage_error(USAGE)
    name = rest.pop(0)
    capsule = " ".join(rest).strip()
    try:
        row = topics.set_capsule(name, capsule, sources=sources)
    except ValueError as e:
        return error_exit(f"❌ topic set: {e}")
    print(f"topic capsule set: #{row.get('name')}")
    return 0


def _active_tasks_by_topic() -> dict[str, list[dict]]:
    active_statuses = tasks.VALID_STATUSES - tasks.TERMINAL_STATUSES
    grouped: dict[str, list[dict]] = {}
    for task in tasks.list_tasks():
        if task.get("status") not in active_statuses:
            continue
        topic_name = str(task.get("topic") or "").strip()
        if not topic_name:
            continue
        grouped.setdefault(topics.topic_key(topic_name), []).append(task)
    return grouped


def _digest_row(row: dict, linked_tasks: list[dict]) -> dict:
    return {
        "name": row.get("name", ""),
        "status": row.get("status", topics.DEFAULT_STATUS),
        "capsule": row.get("capsule", ""),
        "sources": row.get("sources", []),
        "updated_at": row.get("updated_at"),
        "tasks": [
            {
                "id": t.get("id", ""),
                "status": t.get("status", ""),
                "assignee": t.get("assignee", ""),
                "title": t.get("title", ""),
                "artifact_path": t.get("artifact_path", ""),
            }
            for t in linked_tasks
        ],
    }


def _first_capsule_line(row: dict) -> str:
    capsule = str(row.get("capsule") or "").strip().splitlines()
    return capsule[0][:120] if capsule else "（暂无胶囊）"


def _build_digest(*, include_closed: bool = False) -> list[dict]:
    grouped = _active_tasks_by_topic()
    rows = topics.list_topics(include_closed=include_closed)
    return [
        _digest_row(row, grouped.get(topics.topic_key(row.get("name", "")), []))
        for row in rows
    ]


def _render_digest_text(digest: list[dict]) -> str:
    if not digest:
        return "no topics"
    lines = ["topic digest"]
    for row in digest:
        lines.append(f"# {row['name']} [{row['status']}]")
        lines.append(f"  capsule: {_first_capsule_line(row)}")
        if row["sources"]:
            lines.append(f"  sources: {len(row['sources'])}")
        if row["tasks"]:
            lines.append("  tasks:")
            for task in row["tasks"][:8]:
                title = str(task.get("title") or "").replace("\n", " ")[:90]
                lines.append(
                    f"  - {task.get('id')} [{task.get('status')}] "
                    f"{task.get('assignee') or '-'}: {title}"
                )
            if len(row["tasks"]) > 8:
                lines.append(f"  - ... {len(row['tasks']) - 8} more")
        else:
            lines.append("  tasks: none")
    return "\n".join(lines)


def _digest_file_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    return f"topic-digest-{stamp}.md"


def write_digest(target: str | Path, *, include_closed: bool = False,
                 now: datetime | None = None) -> Path:
    path = Path(target).expanduser()
    if not path.suffix:
        path = path / _digest_file_name(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _render_digest_text(_build_digest(include_closed=include_closed))
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _handle_digest(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    include_closed = pop_bool_flag(rest, "--all")
    write_target = pop_flag(rest, "--write")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    digest = _build_digest(include_closed=include_closed)
    if as_json:
        print_json(digest)
        return 0
    if write_target:
        path = write_digest(write_target, include_closed=include_closed)
        print(f"written: {path}")
        return 0
    print(_render_digest_text(digest))
    return 0


def _handle_close(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    name = rest.pop(0) if rest else ""
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    row = topics.close(name)
    if row is None:
        return error_exit("❌ no topic to close")
    return _emit_topic(row, as_json=as_json)


def main(argv: list[str]) -> int:
    args = list(argv)
    if maybe_print_help(args, USAGE):
        return 0
    if not args:
        return _handle_current([])
    if args[0].startswith("--"):
        return _handle_current(args)
    cmd = args.pop(0)
    if cmd == "current":
        return _handle_current(args)
    if cmd == "list":
        return _handle_list(args)
    if cmd == "switch":
        return _handle_switch(args)
    if cmd == "show":
        return _handle_show(args)
    if cmd == "note":
        return _handle_note(args)
    if cmd == "set":
        return _handle_set(args)
    if cmd == "digest":
        return _handle_digest(args)
    if cmd == "close":
        return _handle_close(args)
    return error_exit(f"❌ unknown topic subcommand: {cmd}\n{USAGE}")
