# Errors

## [ERR-20260517-001] codex_provider_preset_model_override

**Logged**: 2026-05-17T17:13:58+08:00
**Priority**: high
**Status**: fixed
**Area**: infra

### Summary
Codex provider presets could write the correct project-local Codex config but still spawn panes with the stale requested model.

### Error
```text
Codex pane env/config resolved OPENAI_MODEL=gpt-5.4, but spawn command still appended --model gpt-5.2.
Backup provider did not support gpt-5.2, so quota/provider failover stayed broken at runtime.
```

### Context
- Provider failover was being configured through `state/provider-presets.json` and per-agent overrides.
- `codex_provider_env_for_agent()` honored `OPENAI_MODEL`, while `effective_model_for_agent()` only translated Anthropic aliases.
- `lifecycle.provision_pane()` and `lazy_spawn_cmd()` pass the effective model into `CodexCliAdapter.spawn_cmd()`, so the stale requested model won at process start.

### Suggested Fix
For `codex-cli` agents, let `effective_model_for_agent()` prefer non-empty `OPENAI_MODEL` from the resolved provider env. Keep regression coverage on both model resolution and the actual provisioned spawn command.

### Metadata
- Reproducible: yes
- Related Files: `src/claudeteam/runtime/providers.py`, `tests/unit/test_runtime_config.py`, `tests/unit/test_runtime_lifecycle.py`

---

## [ERR-20260517-002] codex_provider_secret_in_tmux_spawn_line

**Logged**: 2026-05-17T17:39:08+08:00
**Priority**: high
**Status**: fixed
**Area**: infra

### Summary
Codex agents could expose provider secrets in tmux pane scrollback because provider env was appended to the shell spawn prefix.

### Error
```text
Codex already had project-local config/auth under CODEX_HOME, but pane_env_prefix still appended provider env to the visible tmux send-keys command.
```

### Context
- This is unnecessary for `codex-cli` agents because `_ensure_codex_home()` writes `config.toml` and `auth.json`.
- The visible spawn line can remain in tmux scrollback, making later pane inspection risky.

### Suggested Fix
For `codex-cli` agents, keep `CODEX_HOME` and operational env in the pane prefix but do not append provider env. Regression tests should assert that Codex pane prefixes omit provider URL/model/key values while still creating the Codex home config.

### Metadata
- Reproducible: yes
- Related Files: `src/claudeteam/runtime/lifecycle.py`, `tests/unit/test_runtime_lifecycle.py`

---

## [ERR-20260512-001] feishu_bot_creator_chromium_download_timeout

**Logged**: 2026-05-12T23:50:00+08:00
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
`npm install` for `scripts/feishu_bot_creator` failed during Playwright Chromium download because Google/CDN download timed out.

### Error
```text
Error: Request to https://storage.googleapis.com/chrome-for-testing-public/148.0.7778.96/mac-arm64/chrome-mac-arm64.zip timed out after 30000ms
Downloading Chrome for Testing 148.0.7778.96 ... from https://cdn.playwright.dev/builds/cft/148.0.7778.96/mac-arm64/chrome-mac-arm64.zip
```

### Context
- Machine already has `/Applications/Google Chrome.app`.
- The bot creator only needed Playwright Node modules; bundled Chromium was optional in this local setup.

### Suggested Fix
Allow `create_feishu_bot.js` to launch system Chrome on macOS when available, and install Node dependencies with `npm install --ignore-scripts` if the browser download is flaky.

### Metadata
- Reproducible: yes
- Related Files: `/Users/wsm/Project/ClaudeTeam/scripts/feishu_bot_creator/create_feishu_bot.js`

---

## [ERR-20260513-001] feishu_bot_creator_chinese_ui_selectors

**Logged**: 2026-05-13T00:25:00+08:00
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
Feishu Open Platform was running in Chinese UI, while the bot creator script only matched English button labels in several stages.

### Error
```text
create-app: waiting for getByRole('button', { name: 'Create Custom App' })
add-bot: waiting for getByRole('button', { name: 'Add' }).first()
import-scopes: waiting for getByRole('button', { name: 'Batch import/export scopes' })
events: waiting for locator('text=Subscription mode')
callbacks: waiting for getByText('Callback Configuration')
publish: waiting for getByRole('button', { name: 'Create Version' }).first()
```

### Context
- Creating `WSM-work-assistant` bot for the work assistant ClaudeTeam.
- Script succeeded after adding Chinese/English regex selectors and using system Chrome.
- `import-scopes` was manually marked complete after user intervention.

### Suggested Fix
Keep Feishu bot creator selectors bilingual by default. For future UI automation, prefer regexes such as `Create Version|创建版本` and make optional setup steps non-fatal when the page is already configured.

### Metadata
- Reproducible: yes
- Related Files: `/Users/wsm/Project/ClaudeTeam/scripts/feishu_bot_creator/create_feishu_bot.js`

---

## [ERR-20260513-002] feishu_tenant_token_cross_app_cache

**Logged**: 2026-05-13T23:22:09+08:00
**Priority**: high
**Status**: fixed
**Area**: infra

### Summary
ClaudeTeam Feishu sends could use the wrong bot identity when multiple apps shared the global tenant-token cache.

### Error
```text
TODO-002 team config used app_id cli_aa8e3fa21af89cb2, but ClaudeTeam cards could be sent with a different Feishu bot identity because /tmp/claudeteam_tenant_token.json had no app_id scoping.
```

### Context
- Multiple independent Feishu bots run on the same machine.
- `src/claudeteam/feishu/lark.py` cached tenant tokens in one global `/tmp/claudeteam_tenant_token.json`.
- Reusing a fresh token from another app made `claudeteam say` pair TODO-002 env vars with the wrong cached token.

### Suggested Fix
Scope the token cache by `app_id`, store `app_id` in the cache payload, and reject cache hits whose `app_id` does not match the active Feishu app.

### Metadata
- Reproducible: yes
- Related Files: `src/claudeteam/feishu/lark.py`, `tests/unit/test_feishu_lark.py`

---

## [ERR-20260513-003] claudeteam_pane_wrong_config_file

**Logged**: 2026-05-13T23:53:24+08:00
**Priority**: high
**Status**: fixed
**Area**: infra

### Summary
TODO-002 agents received messages but `claudeteam say` replied to the wrong Feishu chat because panes inherited another team's `CLAUDETEAM_CONFIG_FILE`.

### Error
```text
HTTP 400: Bot/User can NOT be out of the chat.
CLAUDETEAM_CONFIG_FILE=/Users/wsm/Project/work-assistant-team/claudeteam.toml
```

### Context
- `CLAUDETEAM_STATE_DIR` pointed at TODO-002, so inbox and heartbeats looked healthy.
- `CLAUDETEAM_CONFIG_FILE` was not baked into the pane spawn prefix, so Codex shell-outs could inherit a stale config file from another project.
- The result was a misleading state: router received the boss message, manager processed it, but the Feishu send used another team's `chat_id`.

### Suggested Fix
Always include `CLAUDETEAM_CONFIG_FILE=<current deployment claudeteam.toml>` in `pane_env_prefix`, alongside state/runtime/team files.

### Metadata
- Reproducible: yes
- Related Files: `src/claudeteam/runtime/lifecycle.py`, `tests/unit/test_runtime_lifecycle.py`

---
