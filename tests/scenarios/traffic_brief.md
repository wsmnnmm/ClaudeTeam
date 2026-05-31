# Scenario: Traffic Data Assistant Brief

## Given

- The boss wants the first AI traffic employee to stay narrow: record platform
  data, produce a daily brief, and surface anomalies.
- Hermes should answer `今日流量简报` from a lightweight local file, without
  opening Feishu Base or any heavy browser tab.

## When

Write or append JSONL rows to the traffic ledger:

```json
{"date":"2026-05-24","platform":"小红书","content":"深圳 AI 编程局","views":230,"comments":0,"private_messages":2,"add_wechat":0,"effective_leads":1,"notes":"有私信但未加微"}
```

Or let Hermes append one structured row through the command:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli traffic-brief \
  --ledger /Users/wsm/Project/ClaudeTeam/runtime-health/traffic/traffic-ledger.jsonl \
  --append-json '{"platform":"小红书","content":"深圳 AI 编程局","views":230,"comments":0,"private_messages":2,"add_wechat":0,"effective_leads":1}' \
  --out /Users/wsm/Project/ClaudeTeam/runtime-health/traffic-brief.md
```

Generate the brief:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli traffic-brief \
  --ledger /Users/wsm/Project/ClaudeTeam/runtime-health/traffic/traffic-ledger.jsonl \
  --out /Users/wsm/Project/ClaudeTeam/runtime-health/traffic-brief.md
```

For automation, use JSON:

```bash
PYTHONPATH=/Users/wsm/Project/ClaudeTeam/src \
python3 -m claudeteam.cli traffic-brief \
  --ledger /Users/wsm/Project/ClaudeTeam/runtime-health/traffic/traffic-ledger.jsonl \
  --json
```

## Then

- The brief shows only the small daily numbers: views, comments, private
  messages, WeChat adds, and effective leads.
- It warns about anomalies such as views with zero comments, or private
  messages that do not convert to WeChat adds.
- It keeps the role boundary explicit: AI records, reminds, organizes, and
  reviews; the human still owns final publishing, high-value private chats,
  persona, and platform-risk judgment.
- The command is local and read-only except for `--out`; it does not post to
  WeChat, Feishu, Base, or any platform.
