# 问 AI 刘小排：五个旗舰战队与 Claude 模型岗位打法

## 背景

我们正在把 ClaudeTeam 五个 AI 员工团队升级成“主管秒级真实响应 + 后台员工强执行”的旗舰战队体系。当前五队：

- work-assistant：工作分身团队，承接老板日常工作、跨团队调度、SPEC/证据/最终汇报。
- WebsiteChuhai：出海网站/视觉/客户样单团队。
- Product Lab：产品研究、增长、跨团队机制和产物验证团队。
- Traffic Ops：流量/平台资料/登录态/GEO/真实平台验证团队。
- TODO002-study-coach：学习/导师工位/刘小排与亦仁反馈链路团队，含本地与云上。

## 这轮真实改造进展

已落地：

1. 六个运行队列（五个本地团队 + TODO002 云上）全部启用 `router.first_response.enabled=true`。
2. 主管首响走独立真实模型通道，不等 manager pane 高思考链路。
3. 首响模型输出 `text + response_contract`。
4. `response_contract` 只保 6 类：
   - quick_answer
   - research
   - verification
   - dispatch
   - clarification
   - blocker
5. 契约字段只保：
   - type
   - next_step
6. 最终 `manager -> user` 发群前会轻量校验是否兑现契约；没覆盖会自动补一句“先把刚才首响承诺的下一步对齐：...”并记录日志。
7. 发现真实故障后做了针对性修复：
   - Traffic Ops provider 不允许 `/v1/messages`，改为 `/v1/responses` 并保留 fallback。
   - work-assistant 旧 messages 路径偶发 8 秒 read timeout，改为 responses 快通道。
8. 当前所有团队已切到：
   - `endpoint = "responses"`
   - `model = "haiku"`，映射到 `gpt-5.4-mini`
   - `max_tokens = 180`

## 真实数据

第一轮真实测试：

- Product Lab 首响成功：约 7.577s，契约兑现。
- TODO002 本地首响成功：约 6.240s，契约兑现。
- TODO002 云上首响成功：约 6.955s。
- WebsiteChuhai 补发后首响成功：入库到首响约 8.59s，契约兑现。
- work-assistant 失败：旧 `/v1/messages` read timeout，8.115s 后失败。
- Traffic Ops 失败：provider 400，明确不允许 `/v1/messages`，只允许 `/v1/responses` 等。

修复后真实探针：

- work-assistant 走 `/v1/responses` 真模型首响成功：约 1.94s。
- Traffic Ops 走 `/v1/responses` 真模型首响成功：约 5.05s。

测试：

- 全量测试：`1288 passed, 0 failed`
- 六个 router 已重启生效。

## 当前团队打法

已确定的原则：

- 主管必须“明确下场不干活”，只做决策、分析、派活、监控、调配和最终收口。
- 主管不能为了等 worker/长推理而错过老板 10 秒真实首响。
- worker 做具体执行：代码、浏览器、截图、资料收集、设计复核、API/部署等。
- 老板想要的是“真实模型秒回”，不是模板化假秒回。
- 速度目标：首响 <= 10 秒率 > 95%。
- 一致性目标：契约生成率 > 98%，契约兑现率 > 90%。

## 这次请刘小排回答的问题

请直接回答，不要泛泛讲管理原则：

1. 以这轮真实数据和修复结果看，现在是否可以称为“五个旗舰战队”或“接近最强五队”？请给 0-10 分，并说差的唯一最大缺口是什么。

2. 如果老板硬要上这三个 Claude 模型：
   - `claude-opus-4-7`
   - `claude-opus-4-6`
   - `claude-sonnet-4-6`

   请你按“最强打法”安排岗位。重点回答：
   - 哪些岗位/团队适合上 `claude-opus-4-7`
   - 哪些岗位/团队适合上 `claude-opus-4-6`
   - 哪些岗位/团队适合上 `claude-sonnet-4-6`
   - 哪些岗位仍不该上这些大模型，继续用快模型/便宜模型

3. 思考程度怎么安排？请给具体矩阵：
   - 主管首响
   - 主管最终决策
   - worker 执行
   - 研究/导师/SOP
   - 代码/浏览器/部署/视觉验收
   每类分别用 `minimal / low / medium / high / xhigh` 哪个档位。

4. 请明确一条反直觉建议：如果有钱上大模型，哪里反而不要上，为什么？

5. 如果只能先试运行一周，请给一个最小试运行方案：改哪几个岗位、看哪三个指标、什么结果说明值得继续扩大。
