"""`claudeteam cross learnings` — shared cross-team experience pool.

Teams on the same machine automatically mirror "learning" type memories
to a shared pool. This command lets operators view and manage shared
learnings across teams.
"""
from __future__ import annotations

from claudeteam.store import cross_learnings
from claudeteam.util import error_exit, maybe_print_help, pop_bool_flag, reject_extra_args


USAGE = (
    "usage: claudeteam cross learnings [--team <name>] [--limit <n>]\n"
    "       claudeteam cross learnings --stats"
)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0

    do_stats = pop_bool_flag(rest, "--stats")
    team_filter = ""
    limit = 20
    filtered = [a for a in rest if not a.startswith("--")]
    for i, arg in enumerate(filtered):
        if arg == "--team" and i + 1 < len(filtered):
            team_filter = filtered[i + 1]
        elif arg == "--limit" and i + 1 < len(filtered):
            try:
                limit = int(filtered[i + 1])
            except ValueError:
                return error_exit(f"❌ --limit must be an integer, got: {filtered[i + 1]}")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc

    if do_stats:
        return _show_stats()

    return _list_learnings(team=team_filter, limit=limit)


def _list_learnings(*, team: str = "", limit: int = 20) -> int:
    rows = cross_learnings.list_shared(limit=limit, team=team)
    if not rows:
        print("(no shared learnings yet)")
        pool = cross_learnings._pool_file()
        print(f"pool: {pool}")
        return 0
    header = "shared learnings" + (f" (team={team})" if team else "")
    print(f"{len(rows)} {header}:")
    print()
    for i, row in enumerate(rows, 1):
        src_team = row.get("team", "?")
        agent = row.get("agent", "?")
        content = str(row.get("content", ""))
        ref = str(row.get("ref", ""))
        print(f"{i}. [{src_team}] {agent}: {content}")
        if ref:
            print(f"   ref: {ref}")
        print()
    print(f"pool: {cross_learnings._pool_file()}")
    return 0


def _show_stats() -> int:
    rows = cross_learnings.list_shared(limit=1000)
    teams: dict[str, int] = {}
    for row in rows:
        t = row.get("team", "?")
        teams[t] = teams.get(t, 0) + 1
    print(f"total shared learnings: {len(rows)}")
    print(f"teams contributing:")
    for t, count in sorted(teams.items()):
        print(f"  {t}: {count}")
    print(f"pool: {cross_learnings._pool_file()}")
    return 0
