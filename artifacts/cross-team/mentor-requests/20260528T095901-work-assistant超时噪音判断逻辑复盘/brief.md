# work-assistant -> TODO002 AI导师请求：work-assistant超时噪音判断逻辑复盘

- Source team: work-assistant
- Source dir: /Users/wsm/Project/work-assistant-team
- Source manager: manager
- Target mentors: AI 刘小排 (liu)
- Kind: ops
- Owner: work-assistant/manager

## 老板/manager 原始上下文

刘小排你好，我想请你帮我判断一个很具体的团队运行问题。

项目是 ClaudeTeam 里的 work-assistant 团队。最近团队升级后，老板明显感觉响应更流畅，也能按正确流程处理 bug。但群里多了一些系统噪音：明明 manager 和 worker 都在干活，系统还是发了“需要主管确认：老板消息已读后未闭环”和“需要主管确认：T-187 派工后无首产物”。

我先只做排查，不改代码。真实证据如下：

5 月 28 日 09:19:39 老板发消息授权改代码，要求本地启动服务测试，并切到 192.168.1.27:10023。
09:19:44 manager 首响，4.2 秒回应。
09:21:33 manager 建 T-187 并派给 worker_frontend。
09:21:46 manager 已公开回群，说确认分支、本地 dev proxy、bug 组件，已派 worker_frontend，预计 10-15 分钟有结果。
09:21:56 action guard 才记录这条老板消息“已读”。
09:25:06 系统报“老板消息已读后未闭环”。
09:26:37 系统报“T-187 派工后无首产物”。
09:27:14 manager 记录 first_output_feedback：这是误报，worker_frontend 正在正常执行，已完成代码分析，正在改 index.vue，peek 确认 thinking+working。
09:36:51 T-187 artifact 文件已经真实写出来。
09:41:47 系统仍报“T-187 无首产物”。
09:42:27 manager 再次记录 first_output_feedback：代码已改完、artifact 已写、本地复测已过，但 API 网络断连导致 worker 回执未发出。
09:43:22 T-187 才正式完成入账。

我现在的判断是：这不是团队没干活，而是检测逻辑太“账本主义”，同时存在两个噪音来源。

第一，manager_action_guard 存在时序竞态。manager 在 09:21:46 已经回群闭环，但 action guard 是 09:21:56 才建已读记录。闭环动作发生在守卫建档之前，mark_boss_say 当时找不到 open record，后面记录创建后就一直被认为“已读未闭环”。

第二，first_output_watch 只认任务卡 artifact_path、worker 正式发给 manager 的带 artifact 消息、worker 自己日志里的有效证据或 blocker；不认 worker pane 是 thinking、不认 manager peek、不认 manager 的 first_output_feedback、不认 artifact 文件已经在目录里但还没通过回执写入任务卡。严格审计角度这不完全错，但对老板群来说太吵。

我准备的修法是四条：

一，保留严格证据门禁，但把告警分三层。内部私聊催促继续严；主管可见提醒继续要求三选一；老板群公开卡只有在“无 manager 进度、无 worker 证据、无误报反馈”同时满足时才发。目的不是放松纪律，而是减少老板群噪音。

二，补 manager_action_guard 的时序补偿。record_boss_read 创建老板消息记录时，要向前查这条消息之后、read_at 之前或前后一小段窗口内是否已经有 manager->user 的成功 say、公开 manager card、或 manager->worker 的真实派工。如果有，就直接把记录标 closed，避免“先回群后建档”的假超时。

三，补 first_output_feedback 的抑制逻辑。manager 记录 kind=误报 并带有具体事实时，不直接把任务判完成，但应该临时压制公开老板群告警，例如 5-10 分钟，同时要求 manager 在压制期内补正式 artifact、worker 回执或真实 blocker。若到期仍无账本证据，再升级。

四，补 artifact 自动桥接。worker 已在规范目录写出 artifact，但因为模型 API 网络断连没有成功回执时，watcher 可以在任务专属 artifact 目录里发现新文件，并把它作为“候选证据”提示 manager 或写入任务卡待验；但仍要做可用性验证，不能只靠文件名。

我想请你严格判断：

你满意这个分析和修法吗？按 10 分满分，你给几分？

如果不满意，请直接说不满意的原因，不要泛泛讲团队管理。请指出这四条里哪一条会带来新问题，或者还缺哪条关键修法。

我尤其想让你挑刺：
1. 三层告警会不会让系统变软，导致真实拖延被隐藏？
2. first_output_feedback 抑制会不会被 manager 当成“误报免死牌”？
3. artifact 自动桥接会不会把半成品文件误认成产物？
4. 时序补偿会不会把无效回群误判成闭环？

最后请给一个你愿意打 10 分的最小修改版：要具体到规则、验收用例和优先级。重点是减少老板群噪音，但不能牺牲真实超时/假进展的检测能力。

## 证据正文

- 暂无额外证据文件。

## 图片证据

- 无图片；请只基于内联正文提问。

## TODO002 执行要求

- TODO002 是导师网关，负责 DeepSea 浏览器、mentor-loop、顾问卡和 loop-state。
- 如果本包选择单导师，只生成该导师的问题文件，不要在导师可见问题里提另一位导师。
- 如果图片说明与图片内容不一致、图片看不清、或疑似旧图，先打 blocker，不要继续上传。
- 本地路径只作内部索引；导师可见内容必须来自内联正文或实际上传图片。
- 完成导师 loop 后，必须运行 `scripts/mentor-loop-return.cjs --run-dir <loop-run-dir> --source-team work-assistant --source-dir /Users/wsm/Project/work-assistant-team`，直到拿到源团队 inbox local_id、源团队群消息 id 或源 manager 明确确认。
- 导师卡返回后，源团队 manager 负责翻译成任务卡、门禁、SOP 补丁或老板决策卡。
