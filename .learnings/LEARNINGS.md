## [LRN-20260513-001] correction

**Logged**: 2026-05-13T16:11:00+08:00
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
Do not describe ClaudeTeam's rate-limit guard as a short-message throttle.

### Details
The user asked why a Feishu image message received fast_ack but no follow-up. The root cause was not a deliberate debounce for slow manager reasoning. Router wrote the message to manager inbox, but Codex pane injection was skipped because `wake.is_rate_limited` thought the pane was rate-limited. For Codex CLI this preflight is fragile because the TUI keeps scrollback in the pane, so old errors or numeric fragments can look current and cause silent inbox-only delivery.

### Suggested Action
For Codex CLI workers, avoid marker-based pre-injection rate-limit skipping. Let Codex receive the prompt and surface real rate-limit errors directly, especially for boss follow-up messages.

### Metadata
- Source: user_feedback
- Related Files: src/claudeteam/agents/codex_cli.py
- Tags: claudeteam, codex-cli, feishu-router, inbox, rate-limit

---

## [LRN-20260513-002] correction

**Logged**: 2026-05-13T23:53:24+08:00
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
ClaudeTeam bring-up is not verified until a real agent receives a message and replies back to Feishu.

### Details
The user correctly pointed out that a visible manual `claudeteam say` and a green `claudeteam health` are insufficient. TODO-002 looked healthy and manual sends worked, but agent panes still failed with a 401 first, then with wrong-chat Feishu sends, because the actual agent execution environment differed from the operator shell.

### Suggested Action
For every new or repaired Feishu-backed team, run an end-to-end smoke: write or receive an inbox message, confirm the manager pane processes it, verify the reply appears in the Feishu group from the intended bot, and check the inbox is marked read.

### Metadata
- Source: user_feedback
- Related Files: `src/claudeteam/runtime/lifecycle.py`, `src/claudeteam/feishu/lark.py`
- Tags: claudeteam, feishu, codex-cli, e2e-smoke

---

## [LRN-20260514-001] correction

**Logged**: 2026-05-14T02:01:00+08:00
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
ClaudeTeam "进行中" status is not proof that an agent is actively working.

### Details
TODO-002 had multiple workers marked as 8-hour "进行中", but the tmux panes were idle at the Codex prompt and no files/messages had changed for about 40-50 minutes. The first long-task prompt caused agents to acknowledge ownership and then stop; there was no explicit next-round dispatch or manager巡视 cadence keeping the work moving.

### Suggested Action
For long-running teams, require manager to issue concrete next-round tasks with due checkpoints, inspect pane live state/heartbeat age, and wake idle workers that are marked 进行中 but have stale heartbeats. Health checks should warn on stale heartbeats instead of showing old heartbeats as effectively green.

### Metadata
- Source: user_feedback
- Related Files: `src/claudeteam/commands/health.py`, `tests/unit/test_commands_health.py`
- Tags: claudeteam, long-running-task, heartbeat, manager-cadence, todo002

---

## [LRN-20260514-002] correction

**Logged**: 2026-05-14T09:45:00+08:00
**Priority**: high
**Status**: pending
**Area**: product

### Summary
Long-task success must be judged against the user's intended outcome, not by volume of side artifacts.

### Details
TODO-002's overnight task produced many artifacts and fixed several tools, but the final result still missed the user's target: a stable study督学 workflow with course/community knowledge ingestion and daily product-lab evidence. The team drifted into Feishu publishing, image gateway, temporary cloud links, and status summaries. The created Feishu document was a narrow Product Lab exercise sheet, not the requested course-learning knowledge base and coaching loop.

### Suggested Action
For user-facing long tasks, require an acceptance checklist derived from the original request before work begins, then score final output against that checklist. Infrastructure blockers should be isolated as ops work and must not replace the core learning/product outcome.

### Metadata
- Source: user_feedback
- Related Files: `team-data/todo002-study-coach/reports/2026-05-14-long-task-audit.md`, `team-data/todo002-study-coach/claudeteam.toml`
- Tags: todo002, acceptance-criteria, outcome-drift, long-running-task, study-coach

---

## [LRN-20260515-001] correction

**Logged**: 2026-05-15T18:30:00+08:00
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
When a user says "给某个项目换 key", do not assume they mean the team provider presets.

### Details
The user provided a new Flux-compatible key and asked to configure "恰聊项目". I initially interpreted that as Product Lab / ClaudeTeam provider routing and rotated the local `product-lab/state/provider-presets.json` token before the user corrected me. The actual target was the deployed `qia-chat` app's `/srv/qia-chat/.env.production.local`.

### Suggested Action
For future key-rotation requests, first resolve the scope explicitly from project names and current deployment layout: application env (`qia-chat/.env.production.local`) vs team orchestration env (`product-lab/state/provider-presets.json`, `.env.local.d/*.env`). If the phrase names an app, inspect that app's runtime env path before touching team-level presets.

### Metadata
- Source: user_feedback
- Related Files: `product-lab/state/provider-presets.json`, `/srv/qia-chat/.env.production.local`
- Tags: secret-rotation, scope-clarity, qia-chat, product-lab

---
