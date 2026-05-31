---
name: evidence-first-debugging
description: Evidence-first collaborative debugging and self-learning loop. Use when Codex or a ClaudeTeam agent is asked to fix, diagnose, optimize, or explain failures in code, payment, webhook, browser, API, Feishu, model/provider, UI, deployment, or team-process workflows, especially when the user says not to guess, not to blindly edit code, to find root cause, to add logs, or to improve the team's self-learning/evolution mechanism.
---

# Evidence First Debugging

## Core Rule

Do not begin with code changes. Begin with collaboration-grade evidence.

The failure is often not "the code is weak"; the failure is that the human and AI are operating by wishful prompting instead of evidence, hypotheses, and logs.

## Workflow

1. Give Evidence

   Collect the smallest proof that shows where the system really is. Use screenshots, logs, response bodies, database fields, webhook delivery records, Network callbacks, task ledgers, status files, or provider errors. If the user supplied an image or screenshot, inspect it and state what it proves before acting.

2. Hypothesize Before Changing

   Before editing, tell the user or manager the top 2-3 likely causes, ranked by probability, and the exact check that would confirm or reject each one. Do not loop on "still not working, try another edit" without a falsifiable hypothesis.

3. Add Logs To Locate The Break

   If the failure path is not obvious, add the narrowest temporary or permanent instrumentation that identifies the last good step and first bad step. Prefer structured logs, status fields, command output, screenshots, or trace IDs over prose guesses.

4. Make The Smallest Fix

   Change only what the evidence isolates. Keep unrelated refactors out. Preserve user changes. If multiple causes remain plausible, do one reversible probe or one small fix at a time.

5. Verify And Learn

   Re-run the failing path and capture proof. Then store the lesson in the right durable place: test, SOP, skill, team memory, correction case, or runbook. A fix is not fully done until the same class of mistake is harder to repeat.

## Collaboration Contract

- If the user says "还是不行", do not just edit again. Ask for or collect the missing evidence.
- If the task involves payment, auth, webhook, provider, browser, deployment, or Feishu, include live evidence from that external boundary.
- If logs are absent, adding log visibility is usually the first implementation task.
- If a manager delegates this to a worker, the task card must include: evidence to collect, hypotheses to test, log points, expected artifact, and stop condition.
- If a worker returns only a conclusion without evidence, the manager must reject it or request a proof package.

## Boss-Facing Shape

Keep status short:

```text
结论：现在不能先改代码，先定位证据链。
已证实：<1-3条证据>
最高概率原因：<原因1/原因2/原因3>
下一步：<最小验证/日志/修复动作>
需要老板：<没有就写“不需要”>
```

## Examples

- Payment bug: collect Creem backend payment status, webhook delivery result, `user.plan` database value, and browser Network callback response before editing plan logic.
- Feishu image failure: collect image size, lark-cli raw error, upload response, and retry with compressed image before changing routing.
- Browser/UI failure: capture URL, screenshot, console errors, Network failures, and current route state before changing layout or hardcoding data.
- Team manager timeout: collect inbox state, action guard state, pane state, failed `say` log, and watchdog timeline before changing manager rules.

## Stop Conditions

You can implement when at least one is true:

- The logs/evidence isolate the failing step.
- A reversible probe is the only practical way to isolate it.
- The user explicitly authorizes a best-effort fix despite missing evidence, and the missing evidence is documented as risk.
