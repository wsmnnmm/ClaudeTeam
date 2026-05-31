# ClaudeTeam -> TODO002 AI导师请求：五队黄色观察简报和运行改造打分

- Source team: ClaudeTeam
- Source dir: /Users/wsm/Project/ClaudeTeam
- Source manager: manager
- Target mentors: AI 刘小排 (liu)
- Kind: ops
- Owner: ClaudeTeam/manager

## 老板/manager 原始上下文

请云上 TODO002 通过 AI 刘小排导师网关执行 mentor-score-loop：先给这份五队黄色观察简报/SPEC 打分；如果不到 10 分，围绕唯一最大缺口追问和迭代；达到 10 分或明确达到预期可开发后回传导师卡、loop-state、manager handoff。注意：这是真实导师任务，不是同步测试，3 分钟内需回执 accepted/dispatched/blocked。

## 证据正文

# 请教 AI 刘小排：ClaudeTeam 五队黄色观察简报和下一步运行改造

刘小排你好，我想请你帮我严格打分。

项目：ClaudeTeam
主题：五队舰队黄色观察简报和下一步运行改造
当前 owner：ClaudeTeam / manager

## 真实目标

老板有五个 AI 团队：Product Lab、工作分身、TODO002、WebsiteChuhai、Traffic Ops。现在五队 router/watchdog 都活着，但经常出现黄色观察：心跳过期、任务多、证据字段不够、闭环不足。

老板不想要“看起来很勤奋的巡检简报”，而是要一个能指导今天行动的团队运行系统：知道哪队该先验收，哪队该收敛，哪队只补心跳和证据，什么时候不能再派新活。

请你给这个简报和下一步策略打分，并指出怎么改到 10 分。

## 老板给出的五队简报

**ClaudeTeam 五队舰队简报｜09:56 只读巡视**

结论：**五队全在、路由和 watchdog 都活着；但全部是「黄色观察」**。不是宕机问题，主要是：心跳过期、任务过多、证据字段/闭环不足。

| 团队 | 状态 | 未闭环 | 主要情况 |
|---|---:|---:|---|
| Product Lab | 黄色 | 6 | OpenClaw/Base、名师课堂等仍在推进；manager + 3 工人心跳过期 |
| 工作分身 | 黄色 | 11 | 服务在线且无心跳预警，但任务堆太多，容易“忙而不闭环” |
| TODO002 | 黄色 | 0 | 任务面干净，但 8 个 worker 心跳过期；知识库今天仍有产物 |
| WebsiteChuhai | 黄色 | 4 | 出海 SOP、QiaChat、护手霜打样等在列；manager/工人心跳过期 |
| Traffic Ops | 黄色 | 2 | 深圈活动获客帖、MasterGo icon 任务在跑；多人心跳过期，含 1 个 provider/API 异常 |

**老板最该看 3 件事：**

1. **Traffic Ops 是当前最贴近业务变现的一队**
   最近证据集中在 `T-5 深圈俱乐部 AI 活动获客帖`，已有文案、风险、视觉去 AI 味审查包。建议优先验收它，不要让它只停在内部产物。

2. **工作分身在线最好，但任务最散**
   11 个未闭环，尤其 `T-165 / T-179 学测 UI` 相关链路很多。建议收敛为：今天只盯一个“可截图验收”的 UI 闭环。

3. **Product Lab 继续卡在 Base / OpenClaw 权限与验收闭环**
   有新产物 `openclaw-natural-run-20260526-0812.md`，但还在待验收。下一步应先确认 Base 写权限/产物价值，再决定是否继续跑自动发现。

**当前无明显红色故障：**
五队 `router OK / watchdog OK`，`manager-watch` 没有新的老板告警。

**建议下一步：**
先不要大规模派新活。今天最小动作是：
- **Traffic Ops：验收 T-5 获客帖包，决定能否进入人工发布前检查。**
- **工作分身：砍掉分散视角，只让它交一个最终 UI 截图/差异说明。**
- **其余队：只做心跳补齐和证据补字段，不扩新任务。**

## 我刚做的机器核验

我用当前 ClaudeTeam 的 `fleet-health` 对五个本地团队做了只读核验，结果是：

- Product Lab：YELLOW，warn=4，主要是 manager / worker_builder / worker_ops / worker_rescue 心跳过期。
- 工作分身：GREEN，warn=0；但老板简报里认为它应是黄色，因为任务 11 个未闭环，说明当前机器 health 没把“任务堆积/忙而不闭环”纳入黄色判定。
- TODO002：YELLOW，warn=8，多个 worker 心跳过期。
- WebsiteChuhai：YELLOW，warn=5，manager 和 workers 心跳过期。
- Traffic Ops：YELLOW，warn=8，worker_rescue 有 provider/API 异常，多个 worker 心跳过期。
- 汇总：机器 `fleet-health` 看到的是 green=1, yellow=4, red=0；老板视角是五队全黄。

## 当前我准备改造的方向

我不想只改文案。想把这份简报升级成一个可执行的“黄色观察到行动”的机制：

1. 黄色不只看 health warn，还要看任务债务：未闭环数量、待验收数量、无 artifact / 无截图 / 无下一步 owner。
2. 每个黄色团队必须输出一个动作类型：验收优先、收敛优先、心跳补齐、权限/blocker、证据补字段、暂停扩新任务。
3. 老板只看 3 个可决策项：今天先验收哪个产物、哪个团队不要再扩散、哪个卡点需要老板授权。
4. 只读巡视不能自动派活；但可以生成给 manager 的候选指令/验收卡，等待老板确认或 manager 自行执行。
5. 工作分身这种“服务在线但任务太散”的情况不能被机器判成绿色。

## 我希望你判断

1. 这份老板简报和改造方向，现在可以打几分？
2. 如果不能 10 分，唯一最关键缺口是什么？
3. 怎么把它改成 10 分的 SPEC？
4. 最小实现动作是什么？应该改 fleet-health、cockpit-brief、manager 身份规则、还是新增 SOP/skill？
5. 验收标准是什么？哪些指标证明它不是又多了一份文档？

如果已经足够，请直接说“10 分，可以一次性开发落地”。
如果不能，请只指出最关键缺口和下一版必须补什么，不要泛泛建议。

边界：
- 不要建议今天大规模派新活。
- 不要把黄色观察都升级成红色。
- 不要让老板看一堆后台字段。
- 不要把“心跳过期”当成唯一健康指标。

## 图片证据

- 无图片；请只基于内联正文提问。

## TODO002 执行要求

- TODO002 是导师网关，负责 DeepSea 浏览器、mentor-loop、顾问卡和 loop-state。
- 如果本包选择单导师，只生成该导师的问题文件，不要在导师可见问题里提另一位导师。
- 如果图片说明与图片内容不一致、图片看不清、或疑似旧图，先打 blocker，不要继续上传。
- 本地路径只作内部索引；导师可见内容必须来自内联正文或实际上传图片。
- 完成导师 loop 后，必须运行 `scripts/mentor-loop-return.cjs --run-dir <loop-run-dir> --source-team ClaudeTeam --source-dir /Users/wsm/Project/ClaudeTeam`，直到拿到源团队 inbox local_id、源团队群消息 id 或源 manager 明确确认。
- 导师卡返回后，源团队 manager 负责翻译成任务卡、门禁、SOP 补丁或老板决策卡。
