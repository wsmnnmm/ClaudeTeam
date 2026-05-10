"""Minimal `.env` loader.

Purposefully tiny and dependency-free: load `KEY=VALUE` pairs into
`os.environ`, but only when the key is not already set.

Resolution order:
  1. Explicit `path=` arg
  2. Current working directory's `.env`
  3. ClaudeTeam repo root `.env` (fallback for "manage another project
     from a shared ClaudeTeam checkout" deployments)

That last fallback matters when operators run `claudeteam` from a
product repo like `/Users/.../product-lab`: the working directory has
the team config (`claudeteam.toml`) but not necessarily the shared
Feishu bot credentials that live in the ClaudeTeam checkout's `.env`.
Without the fallback, router/watchdog boot with no FEISHU_APP_* env and
`lark-cli event +subscribe` exits immediately as "not configured".
"""
from __future__ import annotations

import os
from pathlib import Path


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _candidate_paths(path: Path | None = None) -> list[Path]:
    if path is not None:
        return [path]
    cwd_env = Path.cwd() / ".env"
    repo_env = Path(__file__).resolve().parents[3] / ".env"
    if repo_env == cwd_env:
        return [cwd_env]
    return [cwd_env, repo_env]


def load_dotenv(path: Path | None = None) -> None:
    for env_path in _candidate_paths(path):
        try:
            text = env_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = _strip_quotes(value)
    _load_env_dir()


def _load_env_dir() -> None:
    """Load extra local env fragments from `.env.local.d/*.env`.

    This gives per-project deploys a safe place to keep secrets that
    should not live in versioned config files like `claudeteam.toml`.
    Files are applied in lexicographic order and, like `.env`, never
    overwrite already-set variables.
    """
    env_dir = Path.cwd() / ".env.local.d"
    try:
        files = sorted(env_dir.glob("*.env"))
    except OSError:
        return
    for env_path in files:
        try:
            text = env_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = _strip_quotes(value)
