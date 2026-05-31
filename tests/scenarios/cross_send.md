# Cross-Team Dispatch Playbook

## Given

- Two ClaudeTeam team directories exist, each with its own `claudeteam.toml`
  and `state/` directory.
- The source team wants another team to produce a Strategy Package, Demand
  Evidence Pack, or execution receipt.
- The target team has a real `manager` agent.

## When

Run one of:

```bash
claudeteam cross-send website_chuhai manager product_lab_manager "Strategy Request..." 高
claudeteam cross-send todo002_cloud manager product_lab_manager "Demand Evidence Pack..." 高
```

If the operator has a direct target directory instead of a registry key:

```bash
claudeteam cross-send /path/to/target-team manager product_lab_manager "Request..." 高
```

## Then

- The command prints `cross-send: target=... resolved_target=manager`.
- The command prints the target-side `local_id=msg_...` and, unless
  `--no-task` was used, `task_id=T-...`.
- The target team's own `state/facts/inbox.json` contains the new message.
- The target team's own `state/tasks.json` contains the new task.
- The source team's `state/tasks.json` does not contain a fake assignee such
  as `WebsiteChuhai_manager` or `todo002_manager`.

## Regression Check

Plain local send must reject unknown local recipients when a team config has
agents:

```bash
claudeteam send WebsiteChuhai_manager manager "fake cross-team send"
```

Expected result:

- Exit code is non-zero.
- Error says `unknown local recipient`.
- Error tells the operator to use `claudeteam cross-send`.
