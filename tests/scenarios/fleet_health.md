# Scenario: Fleet Health Rollup

## Given

- At least two local ClaudeTeam-powered team directories exist under a root,
  for example `/Users/wsm/Project/product-lab` and
  `/Users/wsm/Project/work-assistant-team`.
- Each team has a `claudeteam.toml` and its normal `state/` directory.

## When

Run:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli fleet-health --root /Users/wsm/Project
```

For the C1 24-hour read-only patrol, write local reports without sending
Feishu messages or changing task state:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli fleet-health \
  --root /Users/wsm/Project \
  --report-dir runtime-health
```

## Then

- The command prints one row per discovered team.
- Each row is labelled `GREEN`, `YELLOW`, or `RED`.
- Red/yellow rows include the most important health lines, such as stuck
  `lark-cli` processes, stale heartbeats, missing router/watchdog, blank
  `lark_profile`, or missing binaries.
- The final summary shows total green/yellow/red counts.
- The command exits non-zero if any team is red.
- `--report-dir` writes `fleet-status.md`, `daily-boss-brief.md`,
  `night-shift-plan.md`, and `dashboard.html`.

## Regression Notes

This is the operator-facing check for multi-team drift. A plain
`claudeteam health` only explains one team; this scenario verifies the
fleet rollup that a boss can run before trusting cross-team coordination.
