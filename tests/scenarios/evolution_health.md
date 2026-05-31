# Scenario: AI Team Evolution Health

## Given

- Boss-experience gates and correction-case regression tests exist.
- The operator wants to know whether the team is becoming a self-learning
  system or only accumulating rules after boss corrections.

## When

Run:

```bash
PYTHONPATH=src python3 -m claudeteam.cli evolution-health \
  --root /Users/wsm/Project/ClaudeTeam \
  --out runtime-health/evolution-health.md
```

## Then

- The report includes correction-case pass rate.
- The report includes machine guardrail coverage.
- The report includes a proxy for proactive discovery.
- The next actions identify owners, evidence, metrics, and non-goals.
