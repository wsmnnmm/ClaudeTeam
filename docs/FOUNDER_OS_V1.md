# Founder OS v1

ClaudeTeam is no longer just a multi-agent task runner. For the user's
startup work, it should operate as an AI-native founder operating system:
stage-gated, evidence-driven, and boss-readable.

Source context: the user's local Chinese study edition of Anthropic's
Founder Playbook, mapped to the user's existing teams and Feishu cockpit.

## Hard Rule

No stage, no task. No evidence, no build. No system, no scale.

Every meaningful product/startup task must name:

- Current stage: `Idea`, `MVP`, `Launch`, or `Scale`.
- Stage exit evidence.
- Today's smallest evidence-producing action.
- What the team is deliberately not doing.
- Whether the boss must decide, authorize, pay, login, or talk to a user.

The daily operating question is:

> Today, which action most proves that someone truly needs, will use, or will pay for this?

## Stage Gates

### Idea / Problem Evidence

Goal: prove the problem is real, specific, frequent, and owned by a reachable
group of people.

Exit evidence:

- Who has the pain, how often, how severe, and how they cope today.
- Strong counterargument: why this could fail, who could beat us, why users
  may not buy.
- Human evidence: interviews, community posts, competitor complaints, trial
  intent, payment signals, or warm intros.

AI job:

- Turn fuzzy ideas into testable hypotheses.
- Attack assumptions before confirming them.
- Map competitors, buyer roles, market timing, and external trends.
- Review interview questions for leading or future-tense questions.

Do not:

- Treat a working demo as validation.
- Ask AI only for supporting evidence.
- Expand tasks before the problem evidence supports it.

### MVP / PMF Evidence

Goal: build the smallest core interaction that proves real users will use,
return, pay, or recommend.

Exit evidence:

- `CLAUDE.md`, scope doc, architecture constraints, and deliberate non-goals.
- Activation, retention, Day 7 / Day 30, payment, recommendation, and false
  positive metrics defined before launch.
- Security and data exposure review before any real user touches the app.

AI job:

- Claude Code executes decisions already made; it does not invent scope.
- Each session updates architecture decisions, assumptions, and scope changes.
- AI challenges traction: what would a skeptic say about these numbers?

Do not:

- Report MVP by feature count.
- Add nice-to-have features just because they are cheap to build.
- Ship user data through code that has not had a security review.

### Launch / Repeatable Growth

Goal: turn early traction into repeatable channels and operating systems
that do not depend on the founder remembering every task.

Exit evidence:

- Growth is explainable by channel, CAC, LTV, payback, and funnel.
- Product is production-ready: security, compliance, monitoring, and recovery
  path are acceptable for the current market.
- Support, bug triage, weekly reporting, sprint planning, and feedback loops
  run without the founder as the only router.

AI job:

- Run architecture audit and sort tech debt into release-blocking, parallel,
  and acceptable.
- Audit founder attention and convert repeat work into automation, delegation,
  or explicit founder-only decisions.
- Maintain product-management loops: spec template, bug decision tree, weekly
  metrics, and customer feedback synthesis.

Do not:

- Let the founder become the default entrypoint for every support issue,
  product decision, bug, and report.
- Confuse launch-day excitement with retention or payment.
- Expand into new markets before the original segment is stable.

### Scale / Defensible Compounding

Goal: compound domain knowledge, user data, integration depth, and workflow
lock-in into a moat.

Exit evidence:

- The company can run for a week without the founder watching every workflow.
- Enterprise buyers can inspect SLA, support, compliance, documentation, and
  monitoring.
- The team can answer why users stay if a funded competitor copies the visible
  features today.

AI job:

- Externalize founder knowledge into searchable context, skills, playbooks,
  edge-case tests, and product logic.
- Convert usage data into product feedback loops.
- Audit top customers for workflow integrations, automations, switching cost,
  and deeper lock-in opportunities.

Do not:

- Mistake more buttons for moat.
- Leave institutional knowledge in the founder's head.
- Treat GTM, support, compliance, and reporting as temporary fire drills.

## Team Map

- ClaudeTeam: operating-system substrate for routing, memory, cockpit sync,
  health checks, manager watchdog, and stable agent execution.
- Product Lab: primary venture lab for demand evidence, MVP, payment, and PMF.
- TODO002: founder training loop for course digestion, demand sniffing, and
  daily judgment practice.
- WebsiteChuhai: Launch/GTM engine for channels, outbound, content, SEO,
  directories, and overseas positioning.
- Work Assistant: real-world work sample engine for bug triage, delivery,
  quality gates, and operational discipline.

## Boss Cockpit Fields

At minimum, the boss cockpit should make these fields visible:

- Current stage.
- Stage exit evidence.
- Today's smallest evidence-producing action.
- Current action.
- Blocker.
- Boss next step.
- Deliberate non-goal.
- Next report time.

Pretty dashboards are secondary. If the boss cannot tell what to do next,
the cockpit failed.

## Daily Ritual

Every morning, the manager should produce one short decision card:

```text
Founder OS daily card
- Current stage:
- Stage exit evidence:
- Today's smallest evidence action:
- What we are not doing:
- Team owner:
- Boss next step:
- Next report:
```

Every evening, the manager should produce one evidence delta:

```text
Founder OS evidence delta
- New evidence:
- Evidence that contradicted us:
- Product / GTM / workflow decision changed:
- Artifact:
- Tomorrow's smallest evidence action:
```
