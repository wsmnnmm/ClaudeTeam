# Scenario: Boss Experience Audit

## Given

- ClaudeTeam has generated boss-visible outputs such as Feishu replies,
  Hermes boss briefs, mentor cards, or task handoff summaries.
- The operator wants to know whether these outputs are readable in WeChat /
  Feishu without opening local paths or Base tables.

## When

Run:

```bash
PYTHONPATH=src python3 -m claudeteam.cli boss-experience-audit \
  --root /Users/wsm/Project/ClaudeTeam \
  --out runtime-health/boss-experience-audit.md
```

For a focused check:

```bash
PYTHONPATH=src python3 -m claudeteam.cli boss-experience-audit \
  runtime-health/boss-brief.md
```

## Then

- The report flags path-only artifact handoffs.
- The report flags cockpit/Base field names such as `老板操作` or `老板决策`
  when they leak into boss-visible copy.
- The report flags mixed mentor prompts and image evidence without captions.
- A clean report exits `0`; a report with issues exits `1` so it can be used as
  a gate.
