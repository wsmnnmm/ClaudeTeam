#!/usr/bin/env python3
"""Print expiry + refreshToken state of claude .credentials.json.

Used by `make creds` so operators see at a glance whether the
freshly-extracted keychain token is healthy. Boss-flagged 2026-05-04:
deployment must never fail because the bind-mounted creds were stale,
so the deploy flow surfaces expiry up-front.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: show_cred_expiry.py <path>", file=sys.stderr)
        return 1
    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        print(f"  ✗ {path}: {e}")
        return 1
    oauth = data.get("claudeAiOauth", {})
    exp_ms = oauth.get("expiresAt") or 0
    refresh = oauth.get("refreshToken") or ""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(exp_ms / 1000))
    print(f"  ok · expires {when}, refreshToken={'set' if refresh else 'EMPTY'}")
    if not refresh:
        print("  ⚠️  refreshToken empty → claude can't auto-refresh; "
              "next expiry will require `make creds` again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
