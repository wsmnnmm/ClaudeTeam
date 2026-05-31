"""API cost guard — PreToolUse Hook backend for paid-API call interception.

Called by the Claude Code PreToolUse hook before every Bash tool invocation.
If the command looks like a paid API call, estimates cost and warns/blocks
when the session budget is exceeded.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# ── known paid API endpoint patterns → estimated cost per call (USD) ──
_API_PRICING: dict[str, tuple[str, float]] = {
    # (regex pattern, estimated_usd_per_call)
    "openai": (r"https?://api\.openai\.com/v\d+/chat/completions", 0.02),
    "openai-image": (r"https?://api\.openai\.com/v\d+/images/generations", 0.04),
    "anthropic": (r"https?://api\.anthropic\.com/v\d+/messages", 0.03),
    "deepseek": (r"https?://api\.deepseek\.com/v\d+/chat/completions", 0.001),
    "deepseek-beta": (r"https?://api\.deepseek\.com/beta/chat/completions", 0.001),
    "evolink-video": (r"evolink.*/(videos?/generations|video)", 0.50),
    "evolink-image": (r"evolink.*/(images?/generations|image)", 0.05),
    "seedance": (r"seedance", 0.50),
    "kling": (r"kling", 0.30),
    "midjourney": (r"midjourney", 0.10),
    "wan": (r"\bwan\b.*video", 0.20),
    "veo": (r"\bveo\b", 0.20),
    "sora": (r"\bsora\b", 0.15),
    "z-image": (r"z[- ]?image.*turbo", 0.01),
    "flux": (r"\bflux\b", 0.02),
    "grok-video": (r"grok.*video", 0.25),
}

# ── budget management ──────────────────────────────────────────

_DEFAULT_BUDGET_USD = float(
    os.environ.get("CLAUDETEAM_API_BUDGET_USD", "5.0")
)

_BUDGET_FILE = Path(
    os.environ.get(
        "CLAUDETEAM_API_BUDGET_FILE",
        os.path.join(os.environ.get("CLAUDETEAM_STATE_DIR", ""), "api_budget.json"),
    )
)


def _read_budget() -> dict:
    if _BUDGET_FILE.exists():
        try:
            return json.loads(_BUDGET_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"limit_usd": _DEFAULT_BUDGET_USD, "spent_usd": 0.0, "calls": 0}


def _write_budget(data: dict) -> None:
    _BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BUDGET_FILE.write_text(json.dumps(data, indent=2) + "\n")


def _detect_api_call(command: str) -> tuple[str, float] | None:
    """Return (provider_name, estimated_cost_usd) if command looks like a paid API call."""
    cmd_lower = command.lower()
    for name, (pattern, cost) in _API_PRICING.items():
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return name, cost
    return None


def check(command: str) -> dict:
    """Check `command` for paid API calls. Returns a result dict.

    Called by the PreToolUse hook wrapper script. Exit code 0 = proceed,
    exit code 1 = blocked (budget exceeded), exit code 2 = warning.
    """
    detected = _detect_api_call(command)
    if not detected:
        return {"action": "pass", "reason": "no paid API detected"}

    name, estimated_cost = detected
    budget = _read_budget()
    spent = float(budget.get("spent_usd", 0.0))
    limit = float(budget.get("limit_usd", _DEFAULT_BUDGET_USD))
    new_total = spent + estimated_cost
    pct = (new_total / limit * 100) if limit > 0 else 100

    result = {
        "action": "pass",
        "provider": name,
        "estimated_cost_usd": estimated_cost,
        "spent_usd": round(spent, 4),
        "limit_usd": limit,
        "new_total_usd": round(new_total, 4),
        "budget_pct": round(pct, 1),
    }

    if limit > 0 and new_total > limit:
        result["action"] = "block"
        result["reason"] = (
            f"BUDGET EXCEEDED: ${estimated_cost:.4f} would bring total to "
            f"${new_total:.2f} (limit ${limit:.2f})"
        )
    elif limit > 0 and pct >= 80:
        result["action"] = "warn"
        result["reason"] = (
            f"BUDGET WARNING: ${estimated_cost:.4f} would bring total to "
            f"${new_total:.2f} ({pct:.0f}% of ${limit:.2f} limit)"
        )
    else:
        result["reason"] = (
            f"API call to {name}: ~${estimated_cost:.4f} "
            f"(total ${new_total:.2f} / ${limit:.2f})"
        )

    return result


def record_spend(estimated_cost_usd: float) -> None:
    """Record an API cost after the call succeeds."""
    budget = _read_budget()
    budget["spent_usd"] = round(float(budget.get("spent_usd", 0.0)) + estimated_cost_usd, 4)
    budget["calls"] = int(budget.get("calls", 0)) + 1
    _write_budget(budget)


def reset_budget(limit_usd: float | None = None) -> dict:
    """Reset the session budget tracker. Returns the new state."""
    new_limit = limit_usd if limit_usd is not None else _DEFAULT_BUDGET_USD
    data = {"limit_usd": new_limit, "spent_usd": 0.0, "calls": 0}
    _write_budget(data)
    return data


def budget_status() -> dict:
    """Return current budget status."""
    return _read_budget()


# ── CLI entry point (called by the PreToolUse hook) ─────────────


def main() -> int:
    """Read the command from stdin (passed by the PreToolUse hook), check it.

    Exit codes:
      0 — proceed
      1 — blocked (budget exceeded)
      2 — warning (budget >= 80%)
    """
    command = sys.stdin.read().strip() if not sys.stdin.isatty() else " ".join(sys.argv[1:])
    if not command:
        print("api-cost-guard: no command to check", file=sys.stderr)
        return 0

    result = check(command)

    if result["action"] == "pass":
        print(f"💰 api-cost-guard: {result['reason']}", file=sys.stderr)
        return 0
    elif result["action"] == "warn":
        print(f"⚠️  api-cost-guard: {result['reason']}", file=sys.stderr)
        print(f"   Budget: ${result['spent_usd']:.2f} spent / ${result['limit_usd']:.2f} limit ({result['budget_pct']:.0f}%)", file=sys.stderr)
        # Warning doesn't block, just notifies
        return 0
    else:
        print(f"🛑 api-cost-guard: {result['reason']}", file=sys.stderr)
        print(f"   Budget: ${result['spent_usd']:.2f} spent / ${result['limit_usd']:.2f} limit", file=sys.stderr)
        print(f"   To override: export CLAUDETEAM_API_BUDGET_USD=<higher_limit>", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
