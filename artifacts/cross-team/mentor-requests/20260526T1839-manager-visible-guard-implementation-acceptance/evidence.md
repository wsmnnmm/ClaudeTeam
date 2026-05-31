# ClaudeTeam manager 自然语言进度汇报机制实现验收包

## 验收目标

请 AI 刘小排继续同一主题判断：V3 方案已经进入实现，现在是否达到 10 分/可以灰度覆盖 Traffic Ops，再作为示例迁移到其他团队。

同一主题信息：

- thread-key: `claudeteam-manager-visible-guard`
- 上一轮 DeepSea 会话 URL: `https://scys.com/deepsea/2001/ai/20260526180701tlEIESa`
- 上一轮结论：V3 评分 9.5/10，可以进入开发；剩余 0.5 分是灰度验证，不是方案缺口。

## 这次实现做了什么

1. `src/claudeteam/commands/say.py`
   - 增加自然语言进度汇报的轻量校验。
   - manager -> user 的图片发送失败、UI/视觉验收缺图片、UI/视觉验收缺可点击 preview URL 时，不再让 manager 卡死在门禁错误里。
   - 这些场景先发一条自然语言进度更新，原正式回复写回 manager inbox，要求补证据后正式收口。
   - 收窄降级范围：空截图列表、只发本地路径、图片文件名没附件、视觉状态图强制门禁仍然硬拦，不允许被进度更新绕过。

2. `src/claudeteam/feishu/deliver.py`
   - 在老板追问“进展如何/现在怎么样/还没好吗/卡住了吗”等状态问题时，给 manager 注入主动提示。
   - 如果完整结论需要截图、图片、浏览器、上传、预览 URL、UI 验收或外部平台证据，第一步先回群自然语言进度更新。
   - 进度更新必须说清谁在做、做到哪、卡在哪、下次什么时候回；禁止说已完成/已通过/已验收。

3. `src/claudeteam/agents/identity.py`
   - manager 身份规则新增“自然语言进度汇报优先”。
   - 老板追问且任务涉及截图/图片/浏览器/上传/预览 URL/视觉 UI 验收/外部平台核验时，先短报进度，再补正式报告。

## 测试证据

本地执行：

```bash
python3 tests/run.py
```

结果：

```text
tests: 1266 passed, 0 failed
```

新增/覆盖的关键用例：

- manager UI/视觉确认缺图时，先发“进度更新”，原正式回复进入 manager inbox。
- manager UI/视觉确认带图但缺 preview URL 时，先发“进度更新”，不发原图。
- Feishu 图片发送失败时，降级为纯文本进度更新，并记录 `say_failed` + `say_progress_fallback`。
- 进度更新如果声称“已验收/已通过/已完成”，会被拒绝。
- 空图片列表、只发本地路径、图片文件名没附件、强制视觉状态图门禁仍然阻断，不允许绕过。
- 老板追问进度且父消息/上下文涉及截图/预览/UI 验收时，manager 注入提示里出现自然语言进度更新要求。

## 已知边界

- 这次没有做 watchdog 自动 recycle manager。
- 这次没有做复杂“正式收口闸门”。
- 进度更新不是最终验收结论；它只负责先让老板知道当前动作和下次回报时间。
- 最终正式报告仍依赖 manager/worker 后续补齐图片、截图、preview URL 或真实 blocker。

## 请刘小排验收

请只判断这次实现：

1. 是否达到你上一轮 V3 的预期？
2. 是否可以打 10 分，先灰度覆盖 Traffic Ops？
3. 如果不是 10 分，只指出最大 1 个缺口。
4. 灰度时最该看的 1 个指标是什么？
5. 下一步迁移到其他团队时，哪些需要按团队差异调整，哪些必须保持基座统一？
