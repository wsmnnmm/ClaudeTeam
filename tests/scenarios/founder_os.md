# Scenario: Founder OS Stage Gates

## Given

- A ClaudeTeam-powered venture team is being used for startup work.
- The operator wants to decide whether a task belongs to Idea, MVP, Launch,
  or Scale before dispatching workers.

## When

Run:

```bash
PYTHONPATH=src python3 -m claudeteam.cli founder-os
```

For a specific stage:

```bash
PYTHONPATH=src python3 -m claudeteam.cli founder-os --stage mvp
```

For dashboards or automation:

```bash
PYTHONPATH=src python3 -m claudeteam.cli founder-os --json
```

To audit real task ledgers for missing Founder OS fields:

```bash
PYTHONPATH=src python3 -m claudeteam.cli founder-os \
  --audit-root /Users/wsm/Project
```

## Then

- The command prints the hard rule: no stage, no task; no evidence, no build;
  no system, no scale.
- Each stage names its goal, exit evidence, AI job, deliberate non-goals,
  boss question, and expected artifact.
- The JSON output contains `stages`, `team_roles`, and `cockpit_fields`.
- Invalid stage names fail clearly instead of silently falling back.
- The audit output checks only non-terminal tasks and lists missing
  `当前阶段`, `阶段出口证据`, `今天最小证据动作`, and `不做什么` task fields;
  cockpit writes the stage into the existing Feishu field named `阶段`.
- Audit findings do not invent data; unknown fields remain missing and must
  be backfilled by the responsible manager or reported as a blocker.

## Regression Checks

- `Idea` tasks must not be described as coding tasks before evidence exists.
- `MVP` output must mention `CLAUDE.md`, scope, metrics, and PMF evidence.
- `Launch` output must push support / bug triage / reporting away from the
  founder as bottleneck.
- `Scale` output must focus on domain knowledge, user data, integrations, and
  workflow lock-in rather than more features.
