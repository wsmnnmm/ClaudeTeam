# `claudeteam recycle` + provider failover

## Given

- A team is already running via `claudeteam up`.
- `claudeteam health` shows a live tmux session and at least one worker pane.
- The team has provider presets prepared:

```json
{
  "presets": {
    "flux-primary": {
      "ANTHROPIC_BASE_URL": "https://api.fluxincode.com/v1",
      "ANTHROPIC_AUTH_TOKEN": "sk-...",
      "ANTHROPIC_DEFAULT_SONNET_MODEL": "gpt-5.5"
    },
    "zyapi-backup": {
      "ANTHROPIC_BASE_URL": "https://zyapi.tuluo.top:8888/v1",
      "ANTHROPIC_AUTH_TOKEN": "sk-...",
      "ANTHROPIC_DEFAULT_SONNET_MODEL": "gpt-5.5"
    }
  }
}
```

- The team config has a failover lane similar to:

```toml
[provider_failover]
enabled = true
primary_preset = "flux-primary"
backup_preset = "zyapi-backup"
rescue_agent = "worker_rescue"
targets = ["manager"]
recycle_targets = ["manager"]
trigger_threshold = 1
trigger_window_s = 180
cooldown_s = 900
```

## When

1. Manually promote the backup preset:

```bash
claudeteam switch model preset --use zyapi-backup
```

2. Recreate only the selected panes so they pick up the new provider:

```bash
claudeteam recycle manager worker_frontend
```

3. If the backup also fails, send a minimal wake message to the rescue agent:

```bash
claudeteam send worker_rescue watchdog "Provider failover rescue check" 高 --no-task
```

## Then

- `claudeteam recycle` should print one line per recreated pane, such as:

```text
♻️  recycled: manager (codex-cli) → Team:manager
```

- `claudeteam health` should show the recycled panes as ready again.
- The rest of the team should stay running; this is not a full `down && up`.
- If the rescue agent is `lazy`, the internal `send` should wake it without
  spending backup quota during healthy periods.
