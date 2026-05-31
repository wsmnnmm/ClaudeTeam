# Manager 翻译包：ClaudeTeam / AI团队自学习进化能力强不强

- 状态：manager_translation
- Owner：ClaudeTeam/manager
- 类型：product
- 对话模式：新开导师对话
- 状态文件：/srv/ai/projects/todo002-study-coach/knowledge-base/mentor-loops/2026-05-23T212726-claudeteam-ai团队自学习进化能力强不强/loop-state.json

## 先读边界

- 导师卡不是任务完成，只是顾问输入。
- manager 必须把建议翻译成 owner、完成证据、验收指标和不做清单。
- 没有执行证据时，不允许二次问同一个问题。
- 老板只看方向选择、真实验证结果和风险例外。

## 导师卡

- AI 刘小排：/srv/ai/projects/todo002-study-coach/knowledge-base/mentor-loops/2026-05-23T212726-claudeteam-ai团队自学习进化能力强不强/mentor-cards/2026-05-23T212854-claudeteam-ai团队自学习进化能力强不强-ai-liuxiaopai-review.md

## 图片证据

- 无

## 任务翻译表

| 动作 | Owner | 完成证据 | 验收指标 | 不做什么 | 截止时间 |
|---|---|---|---|---|---|
| 待拆解 |  |  |  |  |  |

## 老板只看草稿

1. 是否继续：
2. 下一步最小验证：
3. 需要老板拍板：
4. 最大风险/反证：

## 推进命令

```bash
node scripts/mentor-loop-advance.cjs /srv/ai/projects/todo002-study-coach/knowledge-base/mentor-loops/2026-05-23T212726-claudeteam-ai团队自学习进化能力强不强 \
  --state execution \
  --evidence /path/to/artifact-or-pr.md \
  --note "manager 已拆成任务卡，worker 开始执行" \
  --next "等待第一份执行证据"
```
