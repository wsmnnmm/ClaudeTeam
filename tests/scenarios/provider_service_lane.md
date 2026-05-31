# `claudeteam switch model service`

## Given

- A team has `state/provider-presets.json` with provider presets for the
  available service lanes, for example:

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
    },
    "onekey-backup": {
      "ANTHROPIC_BASE_URL": "https://onekey.dualseason.com/v1",
      "ANTHROPIC_AUTH_TOKEN": "sk-...",
      "ANTHROPIC_DEFAULT_SONNET_MODEL": "gpt-5.5"
    }
  }
}
```

- Some workers may still have older `provider_preset` values in
  `claudeteam.toml`; the service lane must override the backend URL/key
  without editing every worker.

## When

Manually make `zyapi` the team-wide main service:

```bash
claudeteam switch model service --use zyapi
claudeteam recycle manager worker_ops
```

Or let the command probe all three lanes and pick the fastest healthy one:

```bash
claudeteam switch model service --auto --order flux,zyapi,onekey
claudeteam recycle manager
```

Configure watchdog failover with a three-lane fallback chain:

```toml
[provider_failover]
enabled = true
primary_preset = "flux-primary"
backup_presets = ["zyapi-backup", "onekey-backup"]
targets = ["manager"]
recycle_targets = ["manager"]
trigger_threshold = 1
trigger_window_s = 180
cooldown_s = 900
```

## Then

- `state/provider-service.json` records the active service, source preset,
  base URL/key overlay, reason, and timestamp.
- `claudeteam switch model` shows the active service and each agent's
  effective model after the service override is applied.
- Recycled panes regenerate `state/codex-home/<agent>/config.toml` with the
  selected service backend even if their old `provider_preset` name still says
  `flux-*`.
- If the active service later fails in the pane, watchdog promotes the next
  preset in `backup_presets` before waking a rescue agent.

## Not Allowed

- Do not paste provider keys into Feishu or public docs.
- Do not assume a preset name is truthful; verify the actual `base_url`.
- Do not full-restart every team when only one or two active panes need to
  pick up a new service lane.
