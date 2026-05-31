"""`claudeteam api-budget` — manage the API cost guard session budget.

  claudeteam api-budget status          show current spend / limit
  claudeteam api-budget reset [--limit N]  reset tracker with optional new limit
"""
from __future__ import annotations

from claudeteam.runtime import api_cost_guard as _guard
from claudeteam.util import maybe_print_help, reject_extra_args


USAGE = (
    "usage: claudeteam api-budget status\n"
    "       claudeteam api-budget reset [--limit <usd>]"
)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if not rest:
        return _status()
    action = rest[0].lower()
    rest.pop(0)
    if action == "status":
        if (rc := reject_extra_args(rest, USAGE)) is not None:
            return rc
        return _status()
    if action == "reset":
        limit = None
        filtered = [a for a in rest if not a.startswith("--")]
        for i, arg in enumerate(filtered):
            if arg == "--limit" and i + 1 < len(filtered):
                try:
                    limit = float(filtered[i + 1])
                except ValueError:
                    print(f"❌ --limit must be a number, got: {filtered[i + 1]}")
                    return 1
        return _reset(limit)
    print(f"❌ unknown action: {action}\n\n{USAGE}")
    return 1


def _status() -> int:
    b = _guard.budget_status()
    limit = float(b.get("limit_usd", 5.0))
    spent = float(b.get("spent_usd", 0.0))
    calls = int(b.get("calls", 0))
    pct = (spent / limit * 100) if limit > 0 else 0
    status_icon = "🟢" if pct < 50 else "🟡" if pct < 80 else "🔴"
    print(f"{status_icon} API Budget: ${spent:.2f} / ${limit:.2f} ({pct:.0f}%) — {calls} calls tracked")
    return 0


def _reset(limit: float | None) -> int:
    data = _guard.reset_budget(limit)
    limit_val = float(data.get("limit_usd", 5.0))
    print(f"🔄 API budget reset: ${0:.2f} / ${limit_val:.2f}")
    if limit is not None:
        print(f"   new limit: ${limit:.2f}")
    return 0
