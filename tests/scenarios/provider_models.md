# `claudeteam switch model models`

## Given

- A team directory already has provider routing configured.
- The active provider or target preset includes a valid OpenAI-compatible
  `base_url`.
- If the preset does not embed an auth token, the team has a usable
  `state/codex-home/<agent>/auth.json` or the operator has a valid
  `~/.codex/auth.json`.

## When

Fetch the active provider's model list and save a snapshot:

```bash
claudeteam switch model models --save
```

Fetch a specific backup preset:

```bash
claudeteam switch model models --preset zyapi-backup --save
```

## Then

- The command should call the provider's OpenAI-compatible models endpoint:

```text
<base_url>/models          # if base_url already ends with /v1
<base_url>/v1/models       # otherwise
```

- The command prints:
  - `source`
  - `base_url`
  - `models_url`
  - `models_count`
  - fetched model ids
  - which configured models are verified / missing

- `--save` writes a durable snapshot to:

```text
state/provider-models.json
```

- That snapshot becomes the team's current record of available models, so
  workers and managers do not rely on stale handwritten lists.

## Current Zyapi Example

As of the 2026-05-25 operator screenshot, the `zyapi` Codex lane showed:

- `gpt-5.3-codex`
- `gpt-5.4`
- `gpt-5.4-mini`
- `gpt-5.5`
- `gpt-image-2`

Treat this as a human snapshot only. The command above is the source of truth
when the provider later changes its available models.
