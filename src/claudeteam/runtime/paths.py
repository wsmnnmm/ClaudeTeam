"""Single source of truth for runtime filesystem paths.

All paths derive from `$CLAUDETEAM_STATE_DIR` (re-read on every call so
tests get isolation by setting the env, not by monkey-patching).  When
not set, falls back to `~/.claudeteam`.

Layout:
    $CLAUDETEAM_STATE_DIR/
        facts/                   ← inbox.json, status.json, logs.jsonl
        router.pid               ← long-running daemon pid files
        watchdog.pid
        kanban_sync.pid
        router.cursor            ← replay state
        router_messages/         ← per-agent injected message bodies
        inject_locks/            ← per-pane mutex files
"""
from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    """Top-level directory for all runtime state."""
    env = os.environ.get("CLAUDETEAM_STATE_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".claudeteam"


def facts_dir() -> Path:
    """Where local_facts stores inbox / status / log."""
    return state_dir() / "facts"


def state_file(name: str) -> Path:
    """A file under state_dir; parent created on demand."""
    p = state_dir() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def router_pid_file() -> Path:
    return state_file("router.pid")


def router_cursor_file() -> Path:
    return state_file("router.cursor")


def watchdog_pid_file() -> Path:
    return state_file("watchdog.pid")


def kanban_pid_file() -> Path:
    return state_file("kanban_sync.pid")


def router_messages_dir() -> Path:
    p = state_dir() / "router_messages"
    p.mkdir(parents=True, exist_ok=True)
    return p


def inject_locks_dir() -> Path:
    p = state_dir() / "inject_locks"
    p.mkdir(parents=True, exist_ok=True)
    return p
