# Scenario: Boss Cockpit Fact Sync

## Given

- Four local team directories exist under `/Users/wsm/Project`.
- Each team has `claudeteam.toml`, `state/tasks.json`, and
  `state/facts/status.json`.
- The Feishu boss cockpit Base is reachable by the configured lark profile.

## When

Run a dry run first:

```bash
PYTHONPATH=src python3 -m claudeteam.cli cockpit-sync \
  --root /Users/wsm/Project --json
```

Then write to Feishu only after the rows look sane:

```bash
PYTHONPATH=src python3 -m claudeteam.cli cockpit-sync \
  --root /Users/wsm/Project \
  --write \
  --profile product-lab
```

To make the flow automatic, enable the watchdog owner in exactly one team
configuration:

```toml
[cockpit_sync]
enabled = true
root = "/Users/wsm/Project"
interval_s = 120
base_token = "Hjsibewe7aL9RmsYiUEcjq3bn3e"
table_id = "tblEyoEGZOZ0gfJr"
profile = "product-lab"
```

Restart that team once so its watchdog picks up the setting:

```bash
claudeteam down && claudeteam up
```

When a task has a local artifact and you want it to become boss-visible in the
Base attachment field, run an explicit upload pass:

```bash
PYTHONPATH=src python3 -m claudeteam.cli cockpit-sync \
  --root /Users/wsm/Project \
  --write \
  --profile product-lab \
  --task-table-id tblJ67mLhY9oM91G \
  --upload-artifacts \
  --artifact-field-id 老板可见产物
```

This upload step is not enabled in the watchdog loop by default; it is explicit
so repeated syncs do not keep attaching the same local file.

## Then

- The dry run emits one row per discovered ClaudeTeam directory.
- Each row contains `战场`, `当前动作`, `当前步骤`, `阶段`,
  `阶段出口证据`, `今天最小证据动作`, `不做什么`, `阻塞`, `老板分组`,
  `最后更新时间`, `事实来源`, and `本机可见活跃任务数`.
- A task that has not written Founder OS metadata is marked `状态分栏=待核验`
  and the cockpit asks the boss to backfill stage, exit evidence, smallest
  evidence action, and deliberate non-goal.
- The write path first lists existing records and updates by `战场`, so
  repeated runs do not create duplicate team cards.
- Task rows split evidence from boss-visible delivery:
  - `产物链接` is only populated for real `http(s)` links.
  - `真实产物链接` keeps the raw task artifact pointer for audit/debug.
  - `产物可见性` says whether the artifact is an openable URL, local pending
    upload, missing, or absent.
  - With `--upload-artifacts`, local files are uploaded to `老板可见产物`.
- If Feishu returns 403 / 91403 or lark-cli fails, the command exits non-zero
  and prints which team row failed.
- When `[cockpit_sync].enabled=true`, watchdog runs the same write path on
  `[cockpit_sync].interval_s`; failures are logged but do not kill the
  supervisor.

## Regression Checks

- A stale heartbeat or warning must become `状态分栏=待核验`.
- A red health issue must become `状态分栏=有阻塞`.
- A healthy team with active tasks must become `状态分栏=执行中`.
- The command must be safe by default: no Feishu write unless `--write` is set.
- The watchdog path must be safe by default: no Feishu write unless
  `[cockpit_sync].enabled=true` is set.
