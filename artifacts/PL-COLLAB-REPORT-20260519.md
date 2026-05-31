# Product Lab 协作调研报告 + 老板驾驶舱扫描（2026-05-19）

Date (Beijing): 2026-05-19

## TL;DR（给老板的 60 秒版）

1) **团队内部协作**：比之前更“可控、可验收”。核心变化是：**先对账 task → 证据动作 → 再回群**、以及派工时强制“上下文包 + artifact”。这让“口头完成”显著减少，能快速定位卡点（权限/联系人/环境）。

2) **外部协作（拿回执）**：今天已拿到 **TODO002-study-coach** 的可复述回执；**WebsiteChuhai** 与 **work-assistant-team** 的回执目前是“可触达但缺口未补齐”，需要补齐“对接人/初始化/权限”之一后才能闭环。

3) **老板驾驶舱（多维表格 + Dashboard）**：**配置已开启，但“自动回写”当前失败**。证据显示 `cockpit_sync` 在写入时 `record-upsert failed`，且 `lark-cli` 报 `not_found (api_error)`；历史上我们也确实遇到过 **Bitable 写入 403 Forbidden（只有读权限）**。因此结论是：**驾驶舱“可读”但“不可持续自动更新”**，需要补权限或修 profile/资源映射后才算“已做好”。

下面给“证据 + 现状 + 缺口 + 老板下一步动作”。

---

## 1) 调研范围与方法（只做扫描，不做长实现）

覆盖对象：

- **内部**：Product Lab 本地团队（本 repo / 本机运行态）
- **外部团队**：
  - 学习监督团队：TODO002-study-coach
  - 出海团队：WebsiteChuhai
  - 工作/云上适配：work-assistant-team / Product Lab 云上备战（按注册表定义）
- **系统层**：老板驾驶舱（Feishu Base + Dashboard + 机器人 profile）

方法（轻量可复测）：

- 以 `claudeteam health` / `claudeteam team` / `claudeteam task list` 作为“运行态与任务态”的事实源。
- 外部团队用“**1 句话回执**”标准：**是否可协作 + 最急缺口 + 对接人**。
- 驾驶舱以“**可写可读**”为验收标准：只读不算“做好更新”，必须至少能成功 upsert 一条记录（或明确进入阻塞并可观测）。

---

## 2) 内部协作战斗力（Product Lab）

### 当前状态快照（事实）

- 本地运行态：`claudeteam health` 显示 router/watchdog alive，chat_id/lark_profile 配置正确（见本地 health 输出）。
- 任务对账：`claudeteam task list --assignee manager` 当前仅有 1 个未完成任务：
  - **T-153 协作调研报告+驾驶舱扫描**（本文件即交付物）

### 战斗力结论（可解释）

做得更好的点（相对“之前”）：

- **证据动作门禁**：回群前必须做一次“真实动作”（看产物/跑命令/看日志），降低假阳性。
- **派工上下文包**：明确目标、边界、已知事实、交付 artifact，降低 worker 往返沟通成本。
- **对账优先**：先以 task card 为真，再处理 inbox，减少“一个问题多套口径”。

当前短板（仍会拖慢协作效率）：

- **跨团队回执链路**受制于“对接人/权限/团队初始化”。这类问题不靠团队更努力能解决，需要“明确联系人/开权限/初始化完成”。

---

## 3) 外部协作扫描（拿回执）

### 3.1 TODO002-study-coach（学习监督团队）

结论：**可协作（已拿到可复述回执）**。

- 1 句话回执（可复述）：
  - **可协作；最急缺口=把教程资产转成老板今天可执行、可验收的动作表；对接人=先找对方 manager 汇总。**
- 证据与留档：
  - `/Users/wsm/Project/product-lab/artifacts/T-151-todo002-study-coach-handshake.md`

对老板的可执行建议（今天能落地）：

- 让 TODO002 manager 把“教程资产”输出为 **1 页动作表**（按：今天最小证据动作 / 验收标准 / 预计耗时 / 负责人）。

### 3.2 WebsiteChuhai（出海团队）

结论：**“团队对象存在”，但“回执未闭环”（缺少明确对接人/回执口径）**。

我们知道的事实源：

- `docs/claudeteam/feishu-team-registry.md` 已登记 WebsiteChuhai 群与专用 profile（说明层面“已接入过”，但不等于今天已拿到回执）。

当前缺口（卡点归因）：

- 缺少 **“今天的对接人”**（可以是对方 manager 或指定群 @），导致“拿回执”无法闭环成 1 句可复述事实。

需要老板做的最小动作（可选其一）：

- 指定：WebsiteChuhai 对接人（一个名字或一个群里 @）。
- 或授权：让我用你指定的渠道发 1 句“回执请求”，并要求对方按 1 句话模板回。

### 3.3 work-assistant-team（工作/云上适配团队）

结论：**可协作（已拿到 1 句话可复述回执）**。

- 1 句话回执（可复述）：
  - **可协作范围=bug 作战表、图证复核、代码位点/前端排查、Founder OS 账本与自动化补账；最急缺口=禅道图证与可复现证据闭环仍在补齐；对接人=manager；SLA=15 分钟内给首次结论/阻塞。**

说明：

- 我已现场核验：以 work-assistant-team 的 env 运行 `claudeteam health/team` 正常（chat_id=oc_44c8...，profile=work-assistant，router/watchdog alive）。

---

## 4) 老板驾驶舱更新扫描（是否已做好？）

### 4.1 目标验收标准

“已做好”必须满足至少其一：

- **自动回写链路成功**（定时 upsert 能写入至少 1 条记录，且无持续错误）；或
- **人工回写可用**（明确谁、用什么身份、能写入/更新，且权限已通）；并且
- 任何失败必须能被观测到（日志里能定位到是 not_found / 403 / token 过期 等）。

### 4.2 我们今天看到的事实（证据）

1) 配置层：

- `claudeteam.toml` 已开启：
  - `[cockpit_sync] enabled = true`
  - `base_token = "Hjsibewe7aL9RmsYiUEcjq3bn3e"`
  - `table_id = "tblEyoEGZOZ0gfJr"`
  - `profile = "product-lab"`
  - 见：`/Users/wsm/Project/product-lab/claudeteam.toml`

2) 运行层（失败证据）：

- `state/watchdog.log` 里反复出现：
  - `record-upsert failed`
  - `cockpit-sync exited rc=1`
  - `lark-cli failed (rc=1): not_found (type=api_error)`
  - `write result: updated=0 created=0 failed=4`
  - 见：`/Users/wsm/Project/product-lab/state/watchdog.log`

3) 运行层（成功写入 smoke，最新）：

- 我在 **2026-05-19 23:01 CST** 手动执行了一次 cockpit-sync 写入：
  - `claudeteam cockpit-sync --root /Users/wsm/Project --write --profile product-lab`
  - 结果：`write result: updated=7 created=0 failed=0`
  - 这说明：**当前 profile=product-lab 的写入链路在“此刻”是可用的**；watchdog 里出现的 `not_found/record-upsert failed` 更可能是“历史错误 / 间歇性身份或资源映射问题”，需要进一步对齐 watchdog 当时用的身份与资源。

3) 历史“权限失败”证据（强相关）：

- `state/facts/logs.jsonl` 里曾明确记录：Bitable 写入返回 **403 Forbidden**（只有读权限）。
  - 见：`/Users/wsm/Project/product-lab/state/facts/logs.jsonl`（log 里含 “403 Forbidden” 字样）

### 4.3 结论（是否已做好）

- **驾驶舱的“文档/配置/注册表”层面：已准备好。**
- **驾驶舱的“回写能力”层面：此刻可用（手动写入 smoke 已成功），但 watchdog 历史失败仍需复盘原因，避免间歇性回退。**

### 4.4 下一步最小修复路径（按优先级）

P0（最可能）：

- **写权限/身份问题**：确保用于回写的 Feishu CLI App / profile 对该 Base 的 Bitable 记录具备写权限（增改记录）。

P1（并行核验）：

- **not_found 的来源**：确认 `profile=product-lab` 对应的 lark-cli 配置存在且能访问该 Base/table；若 profile 或资源 token 映射不一致，会导致 not_found。

P2（做成可验收）：

- 增加“单条写入 smoke”（一次 upsert）作为明确定义的验收动作：成功=“已做好更新”；失败=进入“阻塞”并给出具体错误码与需要谁处理。

---

## 5) 给老板的行动表（今天能做的最小动作）

1) **确认外部协作对接人**（减少来回）：
   - WebsiteChuhai：给一个对接人或允许我在指定群发“1 句话回执请求”并要求对方按模板回复。
   - work-assistant-team：给一个对接人，或明确这轮是否必须拿“今日回执”。

2) **驾驶舱回写权限**：
   - 明确：用于回写的 app/profile 是哪个（当前配置是 `profile=product-lab`）。
   - 给该身份对 Base `Hjsibewe7aL9RmsYiUEcjq3bn3e` 的 Bitable 写权限（记录增改）。

---

## 6) 备注（供团队内部复测）

- 外部团队与驾驶舱的“事实源/映射关系”，优先以：
  - `docs/claudeteam/feishu-team-registry.md`
  - `scripts/team-registry.py`
  为准。
