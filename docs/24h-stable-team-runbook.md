# 四队 24 小时稳定运行方案（C1，只读版）

更新时间：2026-05-22

## 0. 结论

当前四个团队已经具备“AI 员工体系”的底座：团队目录隔离、tmux 常驻窗口、router/watchdog、飞书群、任务卡、Base 老板驾驶舱。下一阶段不是继续证明 Codex/Claude 单次能力，而是把系统升级成：

> 老板不在线时，团队也能被巡视、被记录、被分级、被汇报；只有在明确授权后才自动唤醒、派活、重启或改生产。

C1 阶段只做 **只读巡视 + 中文老板简报 + 夜班计划**，不自动发飞书、不改任务、不重启、不部署。

---

## 1. 当前四队真实状态

| 团队 | 当前判断 | 关键事实 | 主要风险 |
|---|---|---|---|
| Product Lab | 黄色观察 | router/watchdog 在线；manager 已回执；仍有 2 个活跃任务 | 多数 worker 心跳 22h-1d 过期，说明“窗口在但员工未持续产出” |
| 工作分身 | 红色故障/黄色恢复中 | router/watchdog 在线；T-165 UI 任务在推进 | manager 反复 Codex 429，老板消息无法稳定收口 |
| TODO002 | 黄色观察 | router/watchdog 在线；manager 已完成 T-89 并补 artifact | 跨团队 T-87 等待对方收口；部分任务缺 Founder OS 字段 |
| WebsiteChuhai | 黄色偏红 | router/watchdog 在线；manager 已回执；QiaChat 盲测/AI 味/部署任务活跃 | 活跃任务多，部分任务没有“今天最小证据动作”，容易忙而不闭环 |

---

## 2. 24 小时稳定干的分层架构

### 第 1 层：生命体征
必须知道团队是不是“活着”。

- tmux session 是否存在
- manager pane 是否存在
- worker pane 是否存在
- router 是否 alive
- watchdog 是否 alive
- manager/worker heartbeat 是否过期
- 429、登录、权限、网络错误是否出现

### 第 2 层：任务事实
必须知道团队是不是“在干正确的事”。

- 未闭环任务数量
- 每个任务的 assignee/status/artifact
- 是否有 `founder_stage`
- 是否有 `stage_exit_evidence`
- 是否有 `evidence_action`
- 是否有 `non_goal`

### 第 3 层：证据产物
必须知道团队是不是“产出了能验收的东西”。

- 最近 artifact / report / knowledge-base 文档
- 图片、截图、PR、部署链接、测试结果
- manager 是否做过综合判断，而不是 worker 半成品直接算完成

### 第 4 层：老板驾驶舱
必须把上面三层翻译成老板能看的状态。

- 绿色正常：在线、有任务、有新证据、无老板阻塞
- 黄色观察：在线但心跳过期/证据不足/任务字段不完整
- 红色故障：router/watchdog 掉线、manager 卡死、429 长时间未恢复、老板消息未收口、生产权限/登录阻塞

---

## 3. C1 只读巡视规则

频率建议：

- 白天：每 30 分钟跑一次
- 夜间：每 60 分钟跑一次
- 老板发“调研现在情况/看看团队状态”时：手动立即跑一次

C1 允许读取：

- `claudeteam health --json`
- `claudeteam task list --active`
- `state/tasks.json`
- `state/facts/heartbeats.json`
- `state/manager-watch.json`（若存在）
- 最近 artifacts/reports/docs/knowledge-base 中的 Markdown 产物

C1 禁止执行：

- `claudeteam say`
- `claudeteam send`
- `claudeteam task create/update/done`
- `claudeteam up/down/reset/hire/fire`
- git push / deploy / 生产 API 写入
- 飞书群主动发消息
- Base 写入（除非老板单独确认）

---

## 4. 卡住分级

| 等级 | 触发条件 | C1 动作 | 后续需要确认 |
|---|---|---|---|
| L0 观察 | 单个 worker 心跳过期，但任务无老板催办 | 写入报告 | 不需要 |
| L1 提醒候选 | manager 心跳超过 30 分钟或活跃任务无 evidence_action | 报告里建议提醒 | 是否允许给 manager 发最小提醒 |
| L2 老板关注 | 老板消息未收口、manager 429、任务超过 SLA 无证据 | 报告里标红 | 是否允许直接发群/私聊老板 |
| L3 恢复候选 | router/watchdog 掉线、pane 死亡、登录失效 | 报告里列恢复命令 | 是否允许重启/重新登录/切模型 |
| L4 停止线 | 生产部署、付款、授权、删除、外发敏感信息 | 只报告，不执行 | 必须老板明确确认 |

---

## 5. 每个活跃任务必须补齐的 Founder OS 四字段

所有长期任务都要补：

1. **当前阶段**：idea / mvp / launch / scale
2. **出口证据**：做到什么证据才算能验收
3. **今天最小证据动作**：今天最小推进一步是什么
4. **不做什么**：防止夜班越界、扩散、乱改生产

没有这四字段的任务，只能算“任务存在”，不能算“稳定推进”。

---

## 6. 从 C1 到真正 24h 自主的升级路线

### C1：只读巡视（当前阶段）
- 执行：
  ```bash
  cd /Users/wsm/Project/ClaudeTeam
  PYTHONPATH=src python3 -m claudeteam.cli fleet-health \
    --root /Users/wsm/Project \
    --report-dir runtime-health
  ```
- 生成 `runtime-health/fleet-status.md`
- 生成 `runtime-health/daily-boss-brief.md`
- 生成 `runtime-health/night-shift-plan.md`
- 生成 `runtime-health/dashboard.html`
- 不触发任何外部动作

### C2：定时本地刷新
- 用 cron/launchd 每 30 分钟运行只读脚本
- 只写本地文件
- 连续 24 小时验证报告准确性

### C3：给老板定时推送
- 只把简报发给老板，不唤醒团队
- 早/中/晚/夜班四次即可
- 需要老板确认

### C4：最小唤醒
- manager 超过阈值未回执时，允许发一条固定格式 wakeup
- 不派新任务，不改任务状态
- 需要老板确认

### C5：自动派活/恢复
- 只有 C1-C4 连续稳定 72 小时后再考虑
- 才允许按策略重启 router/watchdog、切备用模型、补发任务、Base 写入

---

## 7. 当前建议

先跑 C1 一天。验收标准：

- 四队状态每次都能生成中文报告
- 报告能识别 Codex 429、心跳过期、老板消息未收口、任务字段缺失
- 报告不会误把“进程在线”当成“任务完成”
- 报告不会自动发消息或改任务
- 老板能从报告直接决定：放着、催主管、授权恢复、还是亲自决策
