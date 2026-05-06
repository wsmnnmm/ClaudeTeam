# Round C — 真任务派发端到端

## 场景

打通"老板 → manager → workers → 汇总"的完整循环。这是 ClaudeTeam 存在的
意义——之前所有 round（A 修身份初始化 / B 修图片消息、压缩后重读身份、
多团队、容器）都在打基础设施；Round C 验证基础设施真的能扛住一个真实
任务、能分派、能跟踪、能汇总、能交付。

姊妹剧本：之前所有本机端到端冒烟都验证**路由正确性**（消息到不到、走没走对路）；
Round C 验证**任务能不能完成**（agent 跨多回合协作、manager 跟踪进度、
最终交给老板一份汇总）。

## 范围

- 类型：本机端到端（飞书 + tmux + 真 CLI pane + 真 LLM 派工）
- 凭证：当前部署使用的 lark-cli profile + 飞书群 chat_id（不写死，从
  `runtime_config.json` 读）
- 操作员：老板（人工驱动）
- 预期时长：30-60 分钟（取决于任务复杂度）

```bash
# 凭证占位，跑前替换或脚本里读：
PROFILE="$(python3 -c 'import json; print(json.load(open("runtime_config.json")).get("lark_profile",""))')"
CHAT="$(python3 -c 'import json; print(json.load(open("runtime_config.json"))["chat_id"])')"
```

## 前置条件

- 之前所有 B 系列都做完（提交 `ee0dc2f` 之后）
- `claudeteam up` 跑起来 N 个 pane（N 取决于 team.json 配置——默认 3：
  manager + worker_cc + worker_codex；如果你按需补了 worker_kimi 则 4）
- `claudeteam health` 输出全绿（kimi 还在 quota 限速时容许 ⚠️）
- 每个 agent 都自报家门过一次（identity 初始化 prompt 完成）
- 收件箱 / cursor / status 全部初始化为空

可选：

- 用 `claudeteam switch` 把 state_dir 切到独立的 RoundC 团队目录，
  避免污染之前 RoundB 的状态
- 会话名建议 `RoundC` 或 `Smoke-2026-05-03b` 这种带日期的

## 操作 — 真任务

老板从飞书群里发：

```
@manager 我有个任务给团队：把当前 ClaudeTeam 的 README 翻译成英文，
存到 README.en.md。要求:
- 保留原结构和代码块
- 术语 (CLI / pane / chat) 不译
- 完成后由你汇总各 worker 的差异，给我一份 review 报告

请把任务拆成对应数量的子块，分配给团队里的每个 worker，并跟踪到完成。
```

> R174 之后这条话只到 manager 收件箱（不论是否带 `@manager`）。
> manager 解读完后由它自己 `claudeteam send <worker> manager "..."` 派单。

manager 收到后预期行为：

1. **拆任务**：把 README 拆成跟 worker 数对应的 N 块（默认 3 worker → 3 块：
   quickstart / commands / what's missing），写到 `claudeteam task create`
2. **派单**：`claudeteam send worker_cc manager "翻译 quickstart 部分..."`
   等 N 条
3. **群里通报**：每派一份就 `claudeteam say manager "已派 worker_X 翻译 ..."`
4. **跟踪**：每隔几分钟 `claudeteam team` / `claudeteam task list` 看进度
5. **汇总**：N 个 worker 都交了之后，diff 译文风格 / 术语一致性，给老板
   一份 markdown 报告

每个 worker 收到收件箱新行 → 起手干活 → 完成后
`claudeteam say worker_cc "PR 1: README quickstart 翻译完成，存到 README.en.part1.md"`
在群里报到。

`say` 这条卡又会被 router 按 R174 例外分支路由回 manager 收件箱
（worker 卡片 → manager），manager 借此知道某 worker 完工了。

## 期望 — 阶段性 gate（跑的时候填）

| Gate | 现象 | 判定 |
| --- | --- | --- |
| **G1 manager 拆任务** | `claudeteam task list` 显示 N 条 T-XX，assignee 各对应一个 worker | ⏳ |
| **G2 manager 派单可见** | 群里 manager 卡片显示「已分派给 worker_cc/codex/kimi 各一份子任务」 | ⏳ |
| **G3 worker 收单** | 每个 worker pane 都能看到自己收件箱的新行，并在群里 say 一句「收到，开始翻译 X」 | ⏳ |
| **G4 进度自报** | 30 分钟内每个 worker 至少 say 一次进度（如「译完 quickstart，开始 commands」） | ⏳ |
| **G5 worker 完工** | 每个 worker `claudeteam task done T-XX` + `claudeteam say <自己> "PR X: ... 完成"` | ⏳ |
| **G6 manager 看见完工** | 每个 worker 的 say 卡都通过 R174 例外路回 manager 收件箱，manager pane 处理 | ⏳ |
| **G7 manager 汇总** | manager 在群里发完整 review 报告：每个 worker 译文风格特点 + 术语统一情况 + 推荐 merge 哪一份 | ⏳ |
| **G8 时间盒** | 全程 ≤ 60 分钟（含 LLM 思考与工具调用的等待） | ⏳ |
| **G9 无操作员介入** | 老板第一句任务 prompt 之后，无需人工救场（manager 不需 reidentify、worker 不需 stop+rehire） | ⏳ |
| **G10 catchup 抗中断** | 中途故意 SIGTERM router 一次，重启后 cursor 接续读、零消息丢失 | ⏳ |
| **G11 manager 主动报错** | 如果某个 worker 卡 quota 或长时间没动，manager 主动在群里说「X agent 似乎卡住，建议 reidentify」 | ⏳ |

## 这篇剧本的真正价值

CLAUDE.md 工作单 #20 的最后一项。是整个 ClaudeTeam 设计的「真人考」——
不是测路由对不对（A/B 系列已经测过），是测 **agent 能不能真协作干活**。
具体看：

- manager 是不是真在拆任务（vs 直接转发给一个 worker）
- worker 是不是真按收件箱走（vs 路径乱）
- 进度更新是不是真用 `say` 而不是 `send` 写收件箱（这是 Round B G5.a
  留下的 ⚠️）
- manager 汇总是不是真有 review 价值（vs 简单 concat）
- worker 卡 → manager 的 R174 例外路径是不是真让 manager 看见 worker 完工
  （这条决定 manager 能不能闭环；如果坏了，manager 会一直等，只能靠老板
  人工催）

如果 G7 manager 汇总质量差，下一轮要回头改 identity.md 里 manager 的
"汇总"段落（让指令更具体），而不是改 router 代码。这是测 **prompt 工程**，
路由器已经基本完工。

## 已知风险

1. **kimi 配额 429**：Round B G2.d 留下的，kimi 这条线在那次冒烟里就没
   ack 过。如果还没换配额，kimi 这一份会卡住，G3/G4/G5 会有 1 个 ⚠️。
   可接受——不影响 cc / codex 的协作验证。如果你的部署没 worker_kimi，
   忽略这条
2. **worker 用 `send` 而不是 `say`**：Round B G5.a 看到的 LLM 行为问题。
   identity.md 已经在 `_WORKDIR_RULE` 之后加了 send vs say 的说明
   （提交 `246c2f1` + `490e00d`），但 LLM 还是可能犯。如果 G3/G4 出现
   这种偏差，记下来作为 prompt 工程改进点——不算路由器 bug
3. **macOS host：claude 凭证可能过期**——claude 续期 token 时只更新
   keychain，每个 agent home 下的 `state/agent-home/<agent>/.credentials.json`
   是当时快照。Round C 跑 30-60 分钟期间一般没事，但跨天再跑就可能
   "Not logged in"。临时解：`claudeteam down && claudeteam up` 让
   lifecycle 重新从 keychain 物化
4. **lark-cli 速度（已修）**：早先记录"约 73 秒往返"是错的，那是 npx 包查找
   开销不是网络。提交 `feishu/lark.resolve_cli_prefix`（R86）改成直连
   binary 后实测每次发送约 0.6 秒（macOS 主机）。9 次 say 累计 < 10s，
   不再是时间盒主导项。
   - 验证当前部署是否走快路径：
     ```bash
     time lark-cli --profile "$PROFILE" im +chat-search --as bot --query x
     ```
     秒回即正确。如果约 73 秒说明 resolver 落到 npx 兜底，需要
     `npm i -g @larksuite/cli` 或显式 `CLAUDETEAM_LARK_CLI_BIN=/path` 修正

## 不在范围

- **多 manager**：现在团队只一个 manager。多 manager 协作不在本 round
- **真改代码 + 真 PR**：Round C 任务限定在文档/翻译类，避免 worker 真
  `git push`。代码 PR 类放到 Round D
- **跨群任务**：所有 say 都在同一个群里，不测一对一私聊

## 后续 Round D 候选

- 真代码 PR：老板派一个真 bug fix，worker 真改代码 + 真跑测试 + 真
  push 到 fork branch + 给出 PR 链接
- 多任务并发：老板同时派 5 个任务，看 manager 调度 / 优先级
- worker 之间的 peer review：worker_codex 完工后让 worker_cc review
  （需要 manager 编排成 2 层收件箱）
