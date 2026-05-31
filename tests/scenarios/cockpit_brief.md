# Scenario: Lightweight Boss Brief

## Given

- Local ClaudeTeam team directories exist under `/Users/wsm/Project`.
- The Feishu Base boss cockpit may exist, but the boss does not want to keep the
  heavy Base web tab open.
- Hermes or another assistant needs a small, WeChat-readable status surface.

## When

Run a local read-only brief:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli cockpit-brief --root /Users/wsm/Project
```

For Hermes, write the same brief to a stable local artifact:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli cockpit-brief \
  --root /Users/wsm/Project \
  --out /Users/wsm/Project/ClaudeTeam/runtime-health/boss-brief.md
```

For automation or another agent, use JSON:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli cockpit-brief --root /Users/wsm/Project --json
```

For a dry-run approval loop, provide an approval URL base:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli cockpit-brief \
  --root /Users/wsm/Project \
  --approval-base-url https://brief.local/approve
```

## Then

- The command prints a short `老板简报` instead of a wide table.
- `本次待批动作（最多 3 个）` appears near the top when there are actionable
  yellow/active teams. Each item includes a stable action id, action type,
  reason, approval phrase or approval link, and a dry-run manager instruction.
- `老板只看` contains at most the configured number of decision or recheck lines.
- Stale worker heartbeats are labelled as `心跳待重核` and must not be described
  as Feishu bot, CLI, or App Secret authorization breakage.
- Red health issues and real Feishu/Lark configuration warnings stay visible as
  boss attention items.
- The command is read-only by default: it does not write Feishu Base, mutate
  tasks, restart teams, or dispatch work.
- `--out` writes only the rendered brief file, so Hermes can read/send it
  without opening the Feishu Base web page.

## Regression Checks

- A team with `状态分栏=有阻塞` appears before normal active teams.
- A team missing Founder OS fields appears as `要老板拍板` or `要团队补证据`,
  not as a green active team.
- A healthy active team appears under `执行中`.
- The JSON payload includes `summary`, `boss_brief`, and compact `teams` cards.
- The JSON payload includes `pending_approvals`; a team with many unclosed tasks
  can appear there even when health is green, so "busy but not closing" does not
  disappear behind process-health checks.
