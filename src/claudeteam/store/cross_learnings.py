"""Cross-team shared learning pool.

A single append-only JSONL file that all teams on the same machine can
read and write. When an agent records a "learning" memory, it is
optionally mirrored here so other teams benefit from the discovery.

The pool lives at a tunable path (default: ~/.claudeteam/shared/learnings.jsonl)
so deployments that use different home directories or want a shared
volume can override it via `cross_learnings.pool_path` tunable.

Entry shape:
  {team, agent, kind, content, ref, created_at, source_memory_agent}
"""
from __future__ import annotations

import json
from pathlib import Path

from claudeteam.runtime import config, paths, tunables
from claudeteam.util import flock, now_ms, read_jsonl


_MAX_POOL_ENTRIES = 500
_POOL_FILE = "learnings.jsonl"


def _pool_dir() -> Path:
    custom = tunables.tunable("cross_learnings.pool_path", "")
    if custom:
        return Path(str(custom)).expanduser().resolve()
    return Path.home() / ".claudeteam" / "shared"


def _pool_file() -> Path:
    return _pool_dir() / _POOL_FILE


def _pool_lock() -> Path:
    d = _pool_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "learnings.lock"


def _team_label() -> str:
    """Short label for the current team, used in pool entries."""
    try:
        return config.session_name()
    except Exception:
        return str(paths.config_file().parent.name)


def mirror_learning(agent: str, kind: str, content: str,
                    *, ref: str = "") -> dict | None:
    """Append a learning to the cross-team pool.

    Returns the pool entry dict, or None if mirroring is disabled or fails.
    """
    if not bool(tunables.tunable("cross_learnings.mirror_enabled", True)):
        return None
    if kind != "learning":
        return None
    try:
        entry = {
            "team": _team_label(),
            "agent": agent,
            "kind": kind,
            "content": str(content or ""),
            "ref": str(ref or ""),
            "source_memory_agent": agent,
            "created_at": now_ms(),
        }
        pool_dir = _pool_dir()
        pool_dir.mkdir(parents=True, exist_ok=True)
        pool_path = _pool_file()
        with flock(_pool_lock()):
            rows = read_jsonl(pool_path)
            rows.append(entry)
            if len(rows) > _MAX_POOL_ENTRIES:
                rows = rows[-_MAX_POOL_ENTRIES:]
            pool_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                encoding="utf-8",
            )
        return entry
    except Exception:
        return None


def list_shared(*, limit: int = 50, team: str = "") -> list[dict]:
    """Return recent shared learnings, newest first."""
    pool_path = _pool_file()
    rows = read_jsonl(pool_path)
    if team:
        rows = [r for r in rows if r.get("team") == team]
    rows.reverse()
    return rows[:limit]


def render_for_prompt(*, limit: int = 10) -> str:
    """Markdown snippet suitable for injecting into an agent's init prompt."""
    rows = list_shared(limit=limit)
    if not rows:
        return ""
    lines = [
        "## 跨团队共享经验 (cross-team learnings)",
        "",
        "以下是从其他团队最近沉淀的可复用经验，"
        "在执行任务时参考以避免同类团队的重复踩坑：",
        "",
    ]
    for i, row in enumerate(rows, 1):
        team = row.get("team", "?")
        agent = row.get("agent", "?")
        content = str(row.get("content", ""))
        ref = str(row.get("ref", ""))
        lines.append(f"{i}. **[{team}] {agent}**: {content}")
        if ref:
            lines.append(f"   — 关联: {ref}")
    lines.append("")
    return "\n".join(lines)
