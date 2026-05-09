#!/usr/bin/env python3
"""Lightweight MiniMax research helper for ClaudeTeam.

Purpose:
- Use the user's fast-expiring MiniMax quota for batch research work
- Avoid routing that quota through codex-cli custom-provider, which
  currently requires the OpenAI Responses API while this MiniMax gateway
  only implements chat completions
- Keep all secrets in a local ignored config file under state/

Usage:
  python3 scripts/minimax_research.py prompt --text "your prompt"
  python3 scripts/minimax_research.py file --input prompt.txt

Outputs plain text to stdout. Non-zero exit on request / config errors.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "state" / "minimax_research.json"
DEFAULT_TIMEOUT = 120


def _load_config(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            f"config missing: {path}\n"
            "Create it with url / key / model fields before running.")
    except json.JSONDecodeError as e:
        raise SystemExit(f"invalid JSON in {path}: {e}")
    for key in ("url", "api_key", "model"):
        if not data.get(key):
            raise SystemExit(f"config field missing: {key}")
    return data


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def _chat_completions_url(base_url: str) -> str:
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def _request(prompt: str, *, cfg: dict, timeout: int) -> str:
    body = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": int(cfg.get("max_tokens", 4096)),
    }
    req = urllib.request.Request(
        _chat_completions_url(_normalize_base_url(cfg["url"])),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise SystemExit(f"http {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"network error: {e}")

    try:
        choice = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise SystemExit(f"unexpected response: {json.dumps(payload, ensure_ascii=False)[:1000]}")

    content = choice.get("content", "")
    if content:
        return str(content).strip()

    # MiniMax may return reasoning-only fragments when max_tokens is too
    # small. Surface a readable error rather than a blank success.
    reason = choice.get("reasoning_content", "")
    finish = payload.get("choices", [{}])[0].get("finish_reason", "unknown")
    raise SystemExit(
        f"empty assistant content (finish_reason={finish}). "
        f"Try increasing max_tokens. reasoning fragment: {reason[:200]!r}"
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="minimax_research")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_prompt = sub.add_parser("prompt")
    p_prompt.add_argument("--text", required=True)

    p_file = sub.add_parser("file")
    p_file.add_argument("--input", required=True)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(list(argv or sys.argv[1:]))
    cfg = _load_config(Path(ns.config))
    if ns.cmd == "prompt":
        prompt = ns.text
    else:
        prompt = Path(ns.input).read_text(encoding="utf-8")
    print(_request(prompt, cfg=cfg, timeout=ns.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
