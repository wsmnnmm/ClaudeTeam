---
name: cross-team-flow
description: Stabilize cross-team AI-agent collaboration across ClaudeTeam/Codex teams, including local and cloud teams, AI mentor/advisor feedback loops, team handoffs, Feishu/Base cockpit updates, and process packaging. Use when coordinating multiple teams, turning a successful workflow into a reusable skill/SOP, checking whether a cross-team process really ran, preventing local/cloud environment mismatches, or closing a multi-team task without context loss.
---

# Cross-Team Flow

## Principle

Treat "cross-team flow passed" as a reusable operating system, not as a one-off
success with a specific mentor, bot, or project. The goal is stable collaboration:
right owner, right SOP/skill, right workstation, visible evidence, and a clean
boss decision surface.

## Three-Layer Check

Before dispatching or declaring success, identify all three layers:

1. **Employee layer**: Which team owns the outcome? Which manager is accountable?
   Which workers are supporting, and what are their narrow responsibilities?
2. **SOP/skill layer**: Which protocol is being used? Is it written as a durable
   skill/SOP, or only remembered in chat? If it is new, package it before reuse.
3. **Workstation layer**: Where must the work actually run: local machine, cloud,
   persistent browser, Feishu bot, Base, repo, or external service? Do not use a
   local browser or local path for a cloud-only workflow unless explicitly acting
   as local support.

If any layer is missing, report the missing layer as a blocker and repair it
before claiming the process is reusable.

## Workflow

1. **Name the mission lane**
   - Source signal: boss message, mentor feedback, Feishu reply, Base edit, task card,
     external page, or artifact.
   - Primary owner: one manager/team only.
   - Supporting teams: only those with a concrete artifact or verification role.
   - Boss value: what decision, leverage, or time release this unlocks.

2. **Build a context packet**
   - Use `references/context-packet.md` for the compact packet shape.
   - Include task ids, message ids, prior upgrades, active blockers, exact runtime
     paths, evidence links, and the requested output shape.
   - For Feishu replies, preserve parent-message context. A message like "这个是什么意思"
     is invalid without its parent summary.

3. **Dispatch with ownership**
   - Manager owns orchestration and acceptance; workers own bounded execution.
   - Send one task per worker with goal, facts, known exclusions, boundary, expected
     artifact, and acceptance criteria.
   - Avoid all-hands unless all teams truly need to act. Cross-team does not mean
     everyone must touch every task.

4. **Adapt to runtime**
   - Local team: verify local state dir, repo, auth, browser, and Feishu profile.
   - Cloud team: verify remote state dir, cloud router/watchdog health, cloud browser
     persistence, cloud env files, and cloud paths.
   - Never assume an AI mentor or external bot can read a server path. If an external
     advisor is involved, send the digest/prompt through the reachable channel and
     keep the raw evidence locally.

5. **Verify with evidence**
   - Use `references/acceptance-gate.md`.
   - Separate: code changed, tests passed, synced to cloud, daemon restarted,
     real Feishu/Base data verified, manager memory updated, cockpit updated.
   - "Feedback arrived" proves the channel works once. It does not prove the process
     is stable until a second scenario or explicit guardrail is recorded.

6. **Close the loop**
   - Boss-visible reply should be short: conclusion, evidence, next action, need from boss.
   - Write durable memory to the exact team state path, not vague "ClaudeTeam memory".
   - If the result affects operations, update the cockpit/Base or task card so the
     boss can see it without hunting through group chat.

## Anti-Pitfalls

- Do not claim a cloud team ran something if the evidence came from local state.
- Do not let "AI mentor replied" become the artifact; extract reusable advice,
  assign owners, and create follow-up tasks.
- Do not route every cross-team question through the boss. Managers should hand off
  packets directly and return only decision-grade updates.
- Do not close on "已收到 / 已跑通 / 待确认" without task ledger state and evidence.
- Do not duplicate the same fact across multiple cards; update the canonical card.
- Do not spam progress. Report only new evidence, real blockers, deliverables,
  phase changes, or boss decisions needed.

## Installation

Canonical source for this skill in ClaudeTeam is:

`/Users/wsm/Project/ClaudeTeam/skills/cross-team-flow`

Use `scripts/install_to_codex_homes.py` to install it into local global Codex and
team-scoped Codex homes. ClaudeTeam also syncs bundled repo skills into Codex homes
when Codex panes are provisioned.
