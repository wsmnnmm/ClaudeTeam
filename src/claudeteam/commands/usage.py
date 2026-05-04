"""`claudeteam usage` — token / credit consumption snapshot.

R170: every CLI we ship an adapter for now has *some* visibility:
  - claude-code → `npx ccusage <view>` (community ccusage CLI; reads
    `~/.claude/projects` logs)
  - codex       → decode `~/.codex/auth.json` id_token JWT (chatgpt
    OAuth) and surface plan + subscription window. There is no public
    Codex usage endpoint, so this is plan-static, not live percent.
  - kimi-code   → `https://api.kimi.com/coding/v1/usages` with the
    bearer token from `~/.kimi/credentials/kimi-code.json`. Returns
    weekly + 5h sliding window quotas.
  - codex-cli / kimi-cli (legacy aliases) / others → no upstream tool;
    we say so and skip.

Pure shell-out / direct HTTP wrapper, no caching. Add new CLI types
here as their ecosystems grow tools.

Useful when the boss asks "how much did this team burn today?" or
when planning lazy-wake configuration. With `--json`, dump a
machine-readable record so `slash._handle_usage` (Feishu /usage card)
and dashboards can ingest the same numbers without re-parsing the
formatted output.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from claudeteam.runtime import config
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag, print_json,
    reject_extra_args,
)


USAGE = ("usage: claudeteam usage [--view daily|monthly|session|blocks] "
         "[--days N] [--json]")

# ccusage's documented views — validated against argv for clearer errors
_VIEWS = ("daily", "monthly", "session", "blocks")

_KIMI_USAGE_URL = "https://api.kimi.com/coding/v1/usages"


def _run_ccusage(view: str, days: str = "",
                 *, runner: Callable | None = None) -> tuple[int, str]:
    """Invoke ccusage via npx and return (rc, combined_output)."""
    if runner is None:
        runner = lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if shutil.which("npx") is None:
        return 1, "(npx not on PATH; install Node.js to use ccusage)"
    argv = ["npx", "-y", "ccusage", view]
    if days:
        argv += ["--days", days]
    try:
        r = runner(argv)
    except subprocess.TimeoutExpired:
        return 1, "(ccusage timed out after 60s)"
    except OSError as e:
        return 1, f"(ccusage exec failed: {e})"
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out


def _decode_jwt_payload(token: str) -> dict | None:
    """Return the payload dict of a JWT, or None if undecodable.

    Signature is NOT verified — we only inspect plan info that the
    issuer signs but we trust because the file came from disk."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        body = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(body))
    except Exception:
        return None


def _query_codex_usage(home: Path | None = None) -> dict:
    """Read `~/.codex/auth.json` and surface plan + window. There is
    no public Codex usage endpoint we can hit, so this returns plan
    metadata only — sufficient for "is the subscription still valid".

    Returns `{ok, plan, valid_until, valid_from, email, note}` on
    success, or `{ok: false, note}` describing why we couldn't read
    plan info."""
    auth_path = (home or Path.home()) / ".codex" / "auth.json"
    try:
        data = json.loads(auth_path.read_text())
    except FileNotFoundError:
        return {"ok": False, "note": f"{auth_path} 不存在；运行 `codex login` 完成 OAuth"}
    except (OSError, ValueError) as e:
        return {"ok": False, "note": f"读取 {auth_path} 失败：{e}"}
    token = (data.get("tokens") or {}).get("id_token") or ""
    payload = _decode_jwt_payload(token)
    if not payload:
        return {"ok": False, "note": "auth.json 中 id_token 无法解码"}
    chat = payload.get("https://api.openai.com/auth", {}) or {}
    plan = chat.get("chatgpt_plan_type") or "unknown"
    return {
        "ok": True,
        "plan": str(plan).capitalize(),
        "valid_from": chat.get("chatgpt_subscription_active_start", ""),
        "valid_until": chat.get("chatgpt_subscription_active_until", ""),
        "email": payload.get("email", ""),
    }


def _opener_default(req, timeout):  # pragma: no cover - thin wrapper
    return urllib_request.urlopen(req, timeout=timeout)


def _query_kimi_usage(home: Path | None = None,
                      *, opener: Callable = _opener_default) -> dict:
    """Hit Kimi's coding API for the current quota window. Bearer
    token lives in `~/.kimi/credentials/kimi-code.json`. Returns
    `{ok, metrics: [{label, used_pct, remaining_pct, used, limit, reset_iso}]}`
    or `{ok: false, note}` describing why we couldn't query."""
    cred_path = (home or Path.home()) / ".kimi" / "credentials" / "kimi-code.json"
    try:
        token = json.loads(cred_path.read_text()).get("access_token", "")
    except FileNotFoundError:
        return {"ok": False, "note": f"{cred_path} 不存在；运行 `kimi` 完成登录"}
    except (OSError, ValueError) as e:
        return {"ok": False, "note": f"读取 {cred_path} 失败：{e}"}
    if not token:
        return {"ok": False, "note": "credentials/kimi-code.json 缺少 access_token"}
    req = urllib_request.Request(
        _KIMI_USAGE_URL, headers={"Authorization": f"Bearer {token}"})
    try:
        with opener(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except urllib_error.HTTPError as e:
        return {"ok": False, "note": f"Kimi API HTTP {e.code}：{e.reason}"}
    except (urllib_error.URLError, OSError, ValueError) as e:
        return {"ok": False, "note": f"Kimi API 请求失败：{e}"}

    metrics: list[dict] = []
    usage = payload.get("usage", {}) or {}
    try:
        limit = int(usage.get("limit", 0))
        used = int(usage.get("used", 0))
    except (TypeError, ValueError):
        limit = used = 0
    if limit > 0:
        used_pct = round(used / limit * 100)
        metrics.append({
            "label": "Weekly limit",
            "used": used,
            "limit": limit,
            "used_pct": used_pct,
            "remaining_pct": max(0, 100 - used_pct),
            "reset_iso": usage.get("resetTime", ""),
        })
    for item in payload.get("limits", []) or []:
        window = item.get("window", {}) or {}
        detail = item.get("detail", {}) or {}
        try:
            i_limit = int(detail.get("limit", 0))
            i_remaining = int(detail.get("remaining", 0))
        except (TypeError, ValueError):
            continue
        if i_limit <= 0:
            continue
        i_used = i_limit - i_remaining
        used_pct = round(i_used / i_limit * 100)
        duration = int(window.get("duration", 0) or 0)
        unit = window.get("timeUnit", "")
        if "MINUTE" in unit and duration >= 60 and duration % 60 == 0:
            label = f"{duration // 60}h limit"
        elif "MINUTE" in unit:
            label = f"{duration}m limit"
        else:
            label = f"{duration}s window"
        metrics.append({
            "label": label,
            "used": i_used,
            "limit": i_limit,
            "used_pct": used_pct,
            "remaining_pct": max(0, 100 - used_pct),
            "reset_iso": detail.get("resetTime", ""),
        })
    if not metrics:
        return {"ok": False, "note": "Kimi API 返回数据无可解析配额"}
    return {"ok": True, "metrics": metrics}


_NO_TOOL = "no upstream usage tool — track via the provider dashboard"
_UNKNOWN = "unknown — no usage adapter"
_KNOWN_NO_TOOL = ("codex-cli", "kimi-cli", "qwen-code", "qwen-cli", "gemini-cli")


def _note_for(cli: str) -> str:
    return _NO_TOOL if cli in _KNOWN_NO_TOOL else _UNKNOWN


def _build_data(view: str, days: str, clis: set[str],
                *, home: Path | None = None,
                opener: Callable = _opener_default) -> dict:
    """Run each CLI's usage probe and return a structured record.
    Used by both the text renderer (formatted lines) and the --json
    renderer (slash._handle_usage card)."""
    data: dict[str, Any] = {
        "view": view,
        "days": days or None,
        "clis": sorted(clis),
        "claude_code": None,
        "codex": None,
        "kimi": None,
        "other_clis": [],
    }
    if "claude-code" in clis:
        rc, out = _run_ccusage(view, days)
        data["claude_code"] = {
            "rc": rc,
            "ok": rc == 0,
            "output": out,
            "lines": (out or "").splitlines(),
        }
    if "codex-cli" in clis or "codex" in clis:
        data["codex"] = _query_codex_usage(home)
    if "kimi-code" in clis or "kimi-cli" in clis:
        data["kimi"] = _query_kimi_usage(home, opener=opener)
    handled = {"claude-code", "codex-cli", "codex", "kimi-code", "kimi-cli"}
    for cli in sorted(clis):
        if cli in handled:
            continue
        data["other_clis"].append({"cli": cli, "note": _note_for(cli)})
    return data


def _emit_text(data: dict) -> None:
    print(f"━━ usage ({data['view']}) ━━")
    cc = data.get("claude_code")
    if cc is not None:
        print("\nclaude-code (via ccusage):")
        if not cc["ok"]:
            print("  ⚠️  ccusage failed:")
            for line in cc["lines"]:
                print(f"    {line}")
        else:
            for line in cc["lines"]:
                print(f"  {line}")
    cx = data.get("codex")
    if cx is not None:
        print("\ncodex (chatgpt OAuth):")
        if not cx["ok"]:
            print(f"  ⚠️  {cx['note']}")
        else:
            print(f"  Plan: {cx['plan']}  ({cx.get('email', '')})")
            if cx.get("valid_until"):
                print(f"  Valid until: {cx['valid_until']}")
    km = data.get("kimi")
    if km is not None:
        print("\nkimi-code (api.kimi.com):")
        if not km["ok"]:
            print(f"  ⚠️  {km['note']}")
        else:
            for m in km["metrics"]:
                print(f"  {m['label']}: 已用 {m['used_pct']}% "
                      f"({m['used']}/{m['limit']}) · 剩余 {m['remaining_pct']}% "
                      f"· 重置 {m['reset_iso']}")
    if data["other_clis"]:
        print("\nother CLIs:")
        for row in data["other_clis"]:
            print(f"  {row['cli']}: {row['note']}")


def _emit_json(data: dict) -> None:
    print_json(data)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0

    as_json = pop_bool_flag(rest, "--json")
    view = pop_flag(rest, "--view") or "daily"
    days = pop_flag(rest, "--days") or ""
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    if view not in _VIEWS:
        return error_exit(f"❌ unknown view: {view} (valid: {' / '.join(_VIEWS)})")

    try:
        agents = config.load_team().get("agents", {})
        clis = {a.get("cli", "claude-code") for a in agents.values()}
    except Exception:
        clis = set()

    data = _build_data(view, days, clis)
    if as_json:
        _emit_json(data)
    else:
        _emit_text(data)
    return 0
