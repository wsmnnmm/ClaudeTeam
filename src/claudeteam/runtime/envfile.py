"""Minimal project-local `.env` loader.

Purposefully tiny and dependency-free: load `KEY=VALUE` pairs from the
current working directory's `.env` file into `os.environ`, but only
when the key is not already set. This keeps repo-local deploy secrets
available to every `claudeteam ...` command without mutating global
shell config or requiring operators to remember `source .env` first.
"""
from __future__ import annotations

import os
from pathlib import Path


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or (Path.cwd() / ".env")
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _strip_quotes(value)
