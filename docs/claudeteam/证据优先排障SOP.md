# 证据优先排障 SOP

来源：刘小排导师学习卡，2026-05-26。

## 一句话

问题不一定在代码，常常在“人和 AI 协作方式”。不要用“还是不行，继续改”让 AI 猜；先给证据、先列假设、再加 log 定位，最后才改代码。

## 触发场景

- 老板说：不行、又卡了、响应慢、没回、为什么失败、到底哪里坏了、不要凭感觉。
- 支付 / webhook / auth / provider / Feishu / 浏览器 / UI / 部署 / 团队流程异常。
- worker 连续两轮只给结论、路径或解释，没有可验证证据。

## 三步协作法

1. 给证据

   必须先收集能说明现场状态的证据：后台状态、webhook 是否触发、数据库字段、Network 回调、URL、截图、console、原始错误、inbox、action guard、watchdog 时间线。

2. 先假设再动手

   改代码前列出 2-3 个最可能原因，每个原因必须有一个确认/排除动作。不能只说“我再试试”。

3. 加 log 验证

   不知道断在哪里时，先加最窄日志或探针，让日志告诉团队崩在第几步。log 可以是结构化日志、状态字段、截图、trace id、命令输出或工具原始返回。

## Manager 门禁

- manager 可以亲自做 30-60 秒轻量探针，但超过 1 分钟必须派 worker。
- 派工必须写清：要收集什么证据、要验证哪几个假设、要补哪些 log、产物路径和停止条件。
- worker 只给结论没有 proof package，manager 必须退回补证据。
- 对老板汇报只说：结论、已证实、最高概率原因、下一步、是否需要老板。

## 常见证据包

- Creem/支付：后台支付状态、webhook delivery、`user.plan` 字段、浏览器 Network 回调、服务端日志。
- Feishu：lark-cli 原始错误、message_id、图片大小、上传结果、机器人 profile、chat_id。
- 浏览器/UI：真实 URL、截图、console error、Network error、路由状态、真实接口数据。
- 团队卡顿：manager inbox、pane 状态、action guard、watchdog 时间线、最近 `say_failed` / `say_blocked` 日志。

## 沉淀要求

修完后至少沉淀一项：测试、SOP、skill、durable memory、correction case、runbook。目标是下次同类错由系统先拦住，而不是靠老板再次纠偏。
