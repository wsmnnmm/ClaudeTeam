---
name: mentor-score-loop
description: >-
  Use when a Feishu/team task should be refined through AI mentor feedback before
  implementation: ask AI Liu Xiaopai/Yiren via TODO002, iterate the same
  conversation until full score or "达到预期可以开发", then implement once, ask the
  mentor to validate, and close only after TODO002 and the source team both
  confirm handoff. Best for architecture, SOP, SPEC, team-process, product,
  growth, and high-risk workflow upgrades; skip for trivial or time-critical
  tasks where mentor review adds no value.
---

# Mentor Score Loop

## When To Use

Use this skill when the boss or manager asks to:

- ask AI 刘小排 / AI 亦仁 for a score, review, or "打满分";
- improve a SPEC, SOP, architecture, team process, prompt, or product plan before implementation;
- avoid guessing, wasted rework, or "先做了再说" on ambiguous/high-blast-radius work;
- validate that an implementation really matches the mentor-approved plan.

Do not use it for one-command fixes, routine status checks, tiny code changes, urgent blockers, or sensitive actions that first need explicit boss authorization.

## Roles

- Source team manager owns the real problem, context packet, SPEC translation, implementation, final boss reply, and rollout decision.
- TODO002 cloud manager owns DeepSea mentor workstation execution, mentor cards, loop-state, and return handoff.
- The mentor gives review and constraints; the mentor is not the task owner.

## Trigger Gate

Before asking the mentor, the source manager must decide:

- **Conversation mode**: same mentor + same project + same topic + new evidence means follow up in the same thread; new project/topic, dirty context, cross-mentor switch, or boss explicitly wants a clean start means new conversation.
- **Mentor boundary**: AI 刘小排 and AI 亦仁 are separate entrances. Never put one mentor's name into the other mentor-visible prompt unless comparing returned cards outside the mentor prompt.
- **Evidence readiness**: every screenshot/image needs a caption saying what it should prove. Local paths are audit indexes only; mentor-visible facts must be inline or uploaded.
- **Stop condition**: ask for a score and the single biggest gap. Stop iterating only when the mentor says `10 分`, `满分`, `达到预期可以开发`, or explicitly says further pursuit of 10 is not useful and real data is the next test.

## Workflow

1. Build a context packet:
   - real goal and boss value;
   - current SPEC/SOP/plan;
   - constraints and non-goals;
   - evidence and failed attempts;
   - proposed acceptance criteria and rollout scope;
   - exact question asking for score, biggest gap, smallest next action, key metric, and what not to do.
2. Deliver through TODO002, usually with:
   ```bash
   claudeteam mentor-request --mentor liu --target cloud --topic "<topic>" "<context>"
   ```
   Use `--file` for long evidence and `--image ... --image-caption ...` for images.
3. Require TODO002 receipt. "Asked the mentor" is not complete until the source side has at least one target-side proof: TODO002 inbox local_id, TODO002 task_id, Feishu message_id, or source manager explicit receipt of the returned handoff.
4. Iterate before implementation:
   - If score is below the stop condition, the source manager translates the mentor gap into a SPEC delta: owner, acceptance metric, non-goal, and evidence needed.
   - Re-ask only after adding new SPEC/evidence. Keep the same mentor thread when the conversation-mode gate says followup.
   - Do not implement partial-score versions unless the boss explicitly orders it.
5. Implement once after the stop condition.
6. Ask the same mentor thread for implementation acceptance with concrete evidence: changed files, tests, screenshots/logs, rollout scope, and known warnings.
7. Close only after source manager translates the result into boss-facing language: verdict, evidence, what changed, tests, rollout scope, next checkpoint, and any boss action needed.

## TODO002 Return Gate

TODO002 must not silently hold the request.

- Within 3 minutes of receiving a real Feishu/team request: acknowledge accepted, dispatched, or blocked.
- If blocked for login, quota, browser, image mismatch, mentor answer quality, script error, or cloud/local mismatch: return the blocker within 5 minutes with attempted action, needed owner, and next retry time.
- After mentor loop completion: run the return handoff flow and keep retrying until the source team has receipt proof.
- A local TODO002 state entry alone is not return proof for a cloud/source team. Return proof must be visible to the source team or explicitly confirmed by its manager.

## Boss Reply Shape

Keep it short:

- mentor score path and final verdict;
- what changed because of mentor feedback;
- implementation/test evidence;
- rollout scope: local/cloud, which team, since when;
- next checkpoint and the exact metric to watch.

## Failure Policy

If a mentor request has no TODO002 receipt, do not say it is running. Say the real state: request package created, target not confirmed, retry/check in progress.

If the mentor answer is vague or does not score the SPEC, ask one focused followup for score/gap. If it remains unusable, record `导师回答无效` as a blocker and return to the boss with the next best verification plan.

## Acceptance Checklist

- Context packet created and delivered to the right mentor entrance.
- Conversation mode and reason recorded.
- Mentor score/gap captured in a mentor card.
- Re-asks happen only after new SPEC/evidence.
- Stop condition reached before implementation, unless boss overrides.
- Implementation acceptance asked after changes land.
- Tests or real verification evidence captured.
- TODO002 return proof and source manager receipt exist.
- Boss-visible closeout includes verdict, evidence, rollout scope, and next checkpoint.
