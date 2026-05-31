# ClaudeTeam -> TODO002 AI导师请求：闭环审计十大漏洞修复+话题漂移检测评审

- Source team: ClaudeTeam
- Source dir: /Users/wsm/Project/ClaudeTeam
- Source manager: manager
- Target mentors: AI 刘小排 (liu)
- Kind: review
- Owner: ClaudeTeam/manager

## 老板/manager 原始上下文

请刘小排导师对ClaudeTeam今天的十大漏洞修复+话题漂移检测功能，从问题定位准确度、修复方案合理度、代码质量、测试覆盖充分度、系统思维五个维度打分（1-10），并给出综合评分和改进建议。

## 证据正文

# ClaudeTeam 闭环审计十大漏洞修复 + 话题漂移检测

## 时间范围
2026-05-31 单日完成全部修复

## 修复清单

### P0 阻断级
1. **网络盲点** — `watchdog.py` 新增 DNS+TCP 探测，飞书告警。根因：Watchdog 只看进程存活，不管网络通不通，VPN 断开后所有 agent 假活。
2. **Config 迁移杀手** — `lifecycle.py` 自动检测并迁移 `wire_api="chat"` → `"responses"`。根因：Codex 0.135.0 弃用旧字段，旧 `ccswitch.json` 导致所有 codex agent 启动失败。

### P1 紧急
3. **Bot 噪声污染** — `say.py` 内容 SHA256 指纹去重，30s 窗口内相同消息自动跳过。根因：257 条重复确认消息（"收到" × N），群聊变成日志流。
4. **任务状态非法值** — `tasks.py` 新增 `_STATUS_REPAIR_MAP`，读取时自动修复非法状态值。根因：work-assistant 有 28 个任务状态为"历史候选"（不在 VALID_STATUSES 内）。
5. **71% 任务取消率** — `tasks.py` update() 强制要求 artifact_path 才能标记"已完成"，封闭证据回路。根因：零"待验收"任务，任务创建后直接跳到"已取消"，无学习闭环。
6. **C4 唤醒死循环** — `manager_watch.py` 老板消息积压 ≥3 条时跳过 pane inject，发恢复指引卡片。根因：所有团队 manager 都有未读积累，operator 的 C4 唤醒被忽略，问题不收敛。

### P2 重要
7. **Quality Guard 误报** — `say.py` 新增 `_UI_WEAK_OK_MARKERS`（"没问题"/"OK"），不再触发图片要求，只拦截真正的验收声明。根因：`_boss_visible_quality_error()` 把不含截图的正常文本回复拦截为"缺少可视化证据"。
8. **Provider 故障切换炸全队** — `provider_failover.py` 改为 per-agent `provider_preset` override，只切换故障 agent。根因：一个 agent 的故障影响所有 agent。

### P3 改善
9. **跨团队经验隔离** — 新增 `store/cross_learnings.py` 跨团队共享池，memory "learning" 自动镜像，agent 唤醒时注入。根因：5 个本地团队完全独立运行，零经验共享。
10. **任务完成无证据验证** — `tasks.py` update() 验证 artifact 文件真实存在（URL 跳过），base_intake 用 `_force=True`。根因：`TERMINAL_STATUSES` 无任何证据门禁。

### 新增功能：话题漂移检测 & 引用回复关联
- **话题漂移检测**（`store/topics.py`）：Jaccard 词项相似度，重叠率 < 15% 且共享词 < 1 时自动创建新话题。短消息（< 15 字符）不触发漂移，避免"继续"类短追问被误判。
- **引用回复关联**（`store/topics.py` + `feishu/deliver.py`）：msg_id → topic_key 索引（48h TTL），飞书引用回复自动切回父消息所在话题。
- **话题名自动生成**：取首句，去 `#topic` 前缀和常见口语前缀（"对了"/"另外"/"还有"/"我想问"等）。

## 测试覆盖
- 修复前：1412 passed, 0 failed
- 修复后（含话题漂移）：1442 passed, 0 failed
- 新增测试：cross_learnings 14 + topics drift 18 + deliver topic integration 7 = +39 测试

## 闭环成熟度变化
```
感知层    80% → 90%
决策层    70% → 80%
工具层    80% → 90%
质量门禁  55% → 80%
学习机制  15% → 45%
─────────────────
综合      58% → 77%
```

## 评审请求
请刘小排导师对以上修复从以下维度打分（1-10）：
1. 问题定位准确度
2. 修复方案合理度
3. 代码质量
4. 测试覆盖充分度
5. 系统思维（是否只修表面 or 从根因解决）
6. 综合评分

## 图片证据

- 无图片；请只基于内联正文提问。

## TODO002 执行要求

- TODO002 是导师网关，负责 DeepSea 浏览器、mentor-loop、顾问卡和 loop-state。
- 如果本包选择单导师，只生成该导师的问题文件，不要在导师可见问题里提另一位导师。
- 如果图片说明与图片内容不一致、图片看不清、或疑似旧图，先打 blocker，不要继续上传。
- 本地路径只作内部索引；导师可见内容必须来自内联正文或实际上传图片。
- 完成导师 loop 后，必须运行 `scripts/mentor-loop-return.cjs --run-dir <loop-run-dir> --source-team ClaudeTeam --source-dir /Users/wsm/Project/ClaudeTeam`，直到拿到源团队 inbox local_id、源团队群消息 id 或源 manager 明确确认。
- 导师卡返回后，源团队 manager 负责翻译成任务卡、门禁、SOP 补丁或老板决策卡。
