# Context Packet

Use this packet when one team hands work to another team, a cloud runtime, or an
external AI advisor.

## Required Fields

- **Mission**: one sentence describing the outcome.
- **Boss value**: what decision, time release, revenue, quality, or risk reduction
  this supports.
- **Primary owner**: one accountable manager/team.
- **Supporting teams**: each support team and its bounded role.
- **Source signal**: Feishu message id, Base row, task id, doc link, artifact path,
  or external advisor response.
- **Current truth**: verified facts only; mark stale or unverified facts explicitly.
- **Prior upgrades**: relevant SOP/skill/memory already installed.
- **Runtime**: local or cloud, repo path, state dir, browser/CDP endpoint, Feishu
  profile, Base/table/view if relevant.
- **Constraints**: permissions, sensitive assets, login, timing, non-goals.
- **Expected artifact**: file/link/card/screenshot/report/task update.
- **Acceptance gate**: how the receiving team proves completion.

## Minimal Handoff Template

```markdown
任务: <one-line mission>
老板价值: <decision/leverage/time release>
主责: <team/manager>
协作: <team: bounded role>
来源: <message/task/base/artifact id>
已核事实:
- <fact + evidence>
待核/风险:
- <unknown or blocker>
运行工位: <local/cloud + path/profile/browser>
本轮只做: <scope>
交付物: <artifact shape>
验收: <specific evidence gate>
```

## Feishu Reply Rule

If the source signal is a reply message, include the parent message summary and
message id. A bare "这个是什么意思" is not a valid task packet.
