# Scenario: Mentor Score Loop Before Implementation

## Given

- A source team manager receives a Feishu request that asks for mentor review, scoring, SPEC/SOP hardening, architecture change, or a high-risk team process upgrade.
- TODO002 cloud owns the DeepSea mentor workstation and the mentor-loop return handoff.

## When

The boss says:

```text
先问刘小排打分，问到满分或达到预期再一次性改，改完再问有没有达到预期。
```

The source manager should use the `mentor-score-loop` skill and create a TODO002 mentor request:

```bash
claudeteam mentor-request \
  --mentor liu \
  --target cloud \
  --topic "团队主管首产物门禁架构升级" \
  --file artifacts/spec.md \
  "请刘小排先严格打分；如果不到 10 分，只指出唯一最大缺口。达到 10 分或达到预期可以开发后，我们再一次性落地。"
```

## Then

- TODO002 must acknowledge receipt, dispatch, or blocker within the required window.
- Each re-ask must include new SPEC/evidence and a conversation reason.
- The source manager does not implement partial-score versions unless the boss overrides.
- After implementation, the source manager asks the same mentor thread for implementation acceptance.
- Completion requires TODO002 return proof and source manager handoff confirmation.

## Acceptance

- "问了导师" alone is not completion.
- A local TODO002 inbox entry alone is not cloud/source-team handoff proof.
- Final boss reply states score path, final mentor verdict, changed files or process, tests/verification, rollout scope, and next checkpoint.
