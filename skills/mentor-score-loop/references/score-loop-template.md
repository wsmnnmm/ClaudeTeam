# Mentor Score Loop Prompt Template

Use this when the source manager needs to ask a mentor to score a SPEC or implementation.

## SPEC Review

```text
刘小排你好，我想请你帮我严格打分。

项目：
主题：
当前 owner：

真实目标：

当前 SPEC / SOP / 架构方案：

约束和不做什么：

已有证据 / 失败记录：

我希望你判断：
1. 这个版本可以打几分？
2. 如果不能 10 分，唯一最关键缺口是什么？
3. 最小下一步动作是什么？
4. 最关键指标是什么？
5. 哪些事现在不要做？

如果已经足够，请直接说“10 分，可以一次性开发落地”。
如果理论上不该追求 10，请直接说“达到预期可以开发，剩余风险只能靠真实数据解决”。
```

## Implementation Acceptance

```text
这是上一轮你确认可开发的方案的实现验收。

已实现内容：

测试/验证证据：

 rollout 范围：

已知 warning / 未覆盖项：

请你判断：
1. 是否达到上一轮 SPEC 预期？
2. 是否可以进入灰度/全量？
3. 灰度期间最该盯哪三个数？

如果可以，请直接说“达到预期，可以灰度/全量”。
如果不可以，请指出唯一最关键缺口。
```
