"""Optional fleet registry loader.

Some deployments keep a boss-facing team registry outside the ClaudeTeam
repo.  The registry is read-only from this package: if a
`product-lab/scripts/team-registry.py` script exists under the supplied
project root, we can execute it with `--json` and consume its `teams` rows.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable


def default_script(root: Path) -> Path | None:
    """Return the conventional registry script under a project root."""
    candidate = root / "product-lab" / "scripts" / "team-registry.py"
    return candidate if candidate.exists() else None


def load(script: Path | None, *,
         run: Callable = subprocess.run,
         timeout_s: int = 20) -> list[dict]:
    """Load registry team rows from `script --json`.

    Failures degrade to an empty list because the registry is optional:
    cockpit sync and Founder OS audit must still work for plain local teams.
    """
    if script is None or not script.exists():
        return []
    try:
        proc = run(
            [sys.executable, str(script), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    rows = payload.get("teams", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
