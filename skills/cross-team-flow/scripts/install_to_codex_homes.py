#!/usr/bin/env python3
"""Install this skill into global and team-scoped Codex homes.

The script is intentionally stdlib-only so it works on local macOS and cloud
Linux hosts. It copies the skill folder containing this script into:

- ~/.codex/skills/cross-team-flow when --global is set
- <state-dir>/codex-home/<agent>/skills/cross-team-flow for agents in
  claudeteam.toml when --team-dir is supplied. The state dir defaults to
  <team>/state and can be overridden with --state-dir for cloud runtimes.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from pathlib import Path


SKILL_NAME = "cross-team-flow"


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _copy_skill(dst_skills_dir: Path) -> Path:
    src = _skill_root()
    dst = dst_skills_dir / SKILL_NAME
    dst_skills_dir.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    return dst


def _agents_from_team(team_dir: Path) -> list[str]:
    cfg = team_dir / "claudeteam.toml"
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    agents = data.get("agents") or (data.get("team") or {}).get("agents") or {}
    return sorted(str(name) for name in agents)


def install_global() -> list[Path]:
    return [_copy_skill(Path.home() / ".codex" / "skills")]


def install_team(team_dir: Path, state_dir: Path | None = None) -> list[Path]:
    state = state_dir or team_dir / "state"
    installed: list[Path] = []
    for agent in _agents_from_team(team_dir):
        installed.append(_copy_skill(
            state / "codex-home" / agent / "skills",
        ))
    return installed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--global", dest="global_install", action="store_true")
    parser.add_argument("--team-dir", action="append", default=[],
                        help="ClaudeTeam team directory containing claudeteam.toml")
    parser.add_argument("--state-dir",
                        help="Override state dir for --team-dir, useful for cloud runtimes")
    args = parser.parse_args(argv)

    installed: list[Path] = []
    if args.global_install:
        installed.extend(install_global())
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else None
    if state_dir is not None and len(args.team_dir) > 1:
        parser.error("--state-dir can be used with only one --team-dir")
    for raw in args.team_dir:
        installed.extend(install_team(Path(raw).expanduser().resolve(), state_dir))
    if not installed:
        parser.error("choose --global and/or at least one --team-dir")
    for path in installed:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
