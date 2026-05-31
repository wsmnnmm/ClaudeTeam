# Scenario: Boss Correction Regression Cases

## Given

- The boss has corrected repeated AI-team failure modes.
- Each correction should become a bad/good regression sample.

## When

Run:

```bash
PYTHONPATH=src python3 -m claudeteam.cli correction-cases \
  --out runtime-health/correction-cases.md
```

## Then

- At least ten historical correction cases are checked.
- Each bad sample must be caught by the expected gate.
- Each good sample must avoid that expected false positive.
- The command exits non-zero if a historical correction can regress.
