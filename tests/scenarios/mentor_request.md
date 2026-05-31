# Scenario: AI Mentor Request From Any Team

## Given

- A ClaudeTeam-backed project is running with a manager in its Feishu group.
- TODO002 is the AI mentor gateway and owns the logged-in DeepSea browser.
- The source manager has enough context to describe the issue in one evidence pack.

## When

The boss says in the team group:

```text
这个去问一下刘小排，为什么 AI 员工只发路径，怎么改成老板能看的交付？
```

The manager should run:

```bash
claudeteam mentor-request \
  --mentor liu \
  --topic "AI员工路径型交付如何改成人类可消费交付" \
  "老板反馈：AI 员工只发 artifacts 路径，飞书/微信里看不到结论，导致老板必须追问。"
```

If a screenshot is included, every image must include a caption:

```bash
claudeteam mentor-request \
  --mentor liu \
  --topic "AI员工路径型交付如何改成人类可消费交付" \
  --image artifacts/evidence/path-only.png \
  --image-caption "截图应显示 manager 在群里只发送 artifacts 路径，没有状态、核心产出和下一步。" \
  "老板反馈：路径不是可消费交付。"
```

## Then

- A request package is created under `artifacts/cross-team/mentor-requests/`.
- The package brief names the selected mentor entrance and includes the source team, owner, context, evidence, and image captions.
- The request is delivered to TODO002 manager inbox or, if the target is `dry-run`, the command prints the package path without sending.
- TODO002 asks the mentor through its own DeepSea workstation and returns mentor cards plus manager handoff.

## Acceptance

- Saying "问刘小排" routes to `liu`; saying "问亦仁" routes to `yiren`; saying both routes to separate mentor entrances.
- The source manager does not ask the boss to manually format the request.
- No image is accepted without a caption describing what the screenshot should show.
- Local paths are only audit indexes; mentor-visible facts are inlined or uploaded as images.
