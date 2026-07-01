<p align="center">
  <a href="DEPLOYMENT.md">English</a> · <b>简体中文</b> · <a href="DEPLOYMENT_docker_zh.md">Docker 部署 →</a>
</p>

# 部署指南（Host）

把一个 ClaudeTeam 团队跑起来——**跟着下面 4 步一条条敲就行**。配置、模型后端、故障排查
在后面。要用 Docker / 服务器部署 → 另见 [Docker 部署](DEPLOYMENT_docker_zh.md)。

> **让 coding agent 替你部署？** 告诉它：*读这份文档，一步步带我走；遇到要选的（用哪些
> agent CLI？我有没有飞书 App？）**先问我、别瞎猜**。*

---

## 开始之前

装这几样（`pip` 装不了的）：

- **Python 3.9+** —— macOS 自带的 `/usr/bin/python3`（3.9）就够，不用额外装。
  Debian/Ubuntu 还要 `sudo apt install -y python3-venv`。
- **tmux** —— 每个 agent 一个窗口。
- **node + npx (18+)** —— 跑 `lark-cli`（发消息）+ 飞书 sidecar（注册机器人 + 收事件）。
- **至少 1 个 agent CLI** —— 装个 `claude` 就够（默认团队全用它）；想混用
  `codex` / `gemini` / `qwen` / … 是**可选**的（见 [适配表](../README_zh.md#多-cli-适配)）。
- 一个**飞书 / Lark 账号** —— `--quick` 扫一下就能建号（群里 @ 机器人即可）；想要"群里免 @"
  就去掉 `--quick`，用浏览器自动化建**企业自建应用**（需桌面浏览器）。

> 💡 agent **复用你本地已有的登录**：`claude` 在本机登录过，团队里的 claude agent 直接拿来
> 用，**不用再单独登录**。其它 CLI 同理——本机登录过就行。

---

## 第 1 步 · 装齐

```bash
# 代码 + claudeteam 命令（-e 是 editable 安装：始终跟着你的代码走，不会卡在旧版本）
git clone https://github.com/zylMozart/ClaudeTeam.git && cd ClaudeTeam
python3 -m venv .venv && source .venv/bin/activate    # macOS 系统自带的 3.9 就行
pip install -e .

# pip 装不了的外部工具：
#   macOS ： brew install tmux node && npm i -g @larksuite/cli @anthropic-ai/claude-code
#   Debian： sudo apt install -y tmux nodejs npm && npm i -g @larksuite/cli @anthropic-ai/claude-code
```

> 只装/用你需要的 agent CLI。默认团队全是 `claude-code`，所以装个 `claude` 就能跑通；
> `codex` 等其它 CLI 按需再装。

## 第 2 步 · 配团队

```bash
claudeteam init --no-connect      # 写出 claudeteam.toml（默认：manager + 1 个 claude worker）
$EDITOR claudeteam.toml           # 按你装/登录了哪些 CLI 调整 agent（见下）
```

打开 `claudeteam.toml`，`[team.agents.*]` 就是你的团队。默认两个 `claude-code`，**装了
claude 就能直接用**。想加别的 CLI 的 worker（你本机**装了、登录了**才加）—— 解开 init 写
好的注释例子、改改即可：

```toml
[team.agents.worker_codex]
cli   = "codex-cli"     # 本机装了 codex 再加；没有就别管它
model = "gpt-5.5"
role  = "Codex 员工"
```

> 嫌从零配麻烦？[`templates/`](../templates/) 里有现成的领域团队（软件开发 / 科研 / 营销 /
> 数据 / 内容），拷过来改改。`claudeteam reidentify <agent> --print` 能在 `up` 前**预览**某个
> agent 渲染出来的身份。

## 第 3 步 · 连飞书（扫一下，建好机器人 + 群）

```bash
claudeteam feishu connect --quick     # 扫一次码就行，哪台机器都能跑（群里要 @ 机器人）
```

**`--quick` 最省事**：扫一个二维码、零控制台、**任何机器都能跑**（含无头服务器）。命令会建好
机器人应用 + 团队群（把你拉进去）+ 凭证 + `chat_id`（写回 `claudeteam.toml`）。唯一代价：个人版
（PersonalAgent）应用拿不到 `im:message.group_msg`，所以**群里得 @ 机器人**才会回——私聊不受
影响，想先快速跑通也够用。

**想让机器人在群里【免 @】？** 去掉 `--quick`：

```bash
claudeteam feishu connect             # 浏览器自动建一个「群里免 @」的企业自建应用
```

不带参数时，它开一个**真实（有界面）浏览器**，驱动飞书控制台**创建 + 授权 + 订阅 + 发布**一个
持有 `im:message.group_msg` 的企业自建应用——于是机器人**群里不 @ 也能收**。只需扫**一次**登录
二维码，随后 7 个控制台步骤自动跑完；界面一旦变了就**自动退回 `--manual`**。**需要桌面浏览器**
（见下方无头服务器说明）。另有 **`--manual`**：控制台一步步手动引导（贴 App ID/Secret、点一键
授权 deep-link、发版）——浏览器自动化跑不了、或你更想自己点时的最稳兜底。

> ⚠️ **无头服务器**：不带参数的浏览器自动化要**桌面浏览器**，在无头主机上跑不了——要么用
> `--quick`（群里 @ 机器人），要么先在一台**有桌面的机器**上跑 `claudeteam feishu connect` 建好
> 应用 + 群，再把凭证（`state/feishu_app.json`）+ `chat_id` 拷到服务器。（无头 + 终端二维码在规划中。）

<details>
<summary><b><code>--manual</code> 控制台手动引导</b>（浏览器自动化跑不了、或你更想自己点的时候）</summary>

`claudeteam feishu connect --manual` 会在控制台一步步引导你：

1. **建应用** —— 开 <https://open.feishu.cn/app> → 创建企业自建应用 → 加**机器人** →
   复制 **App ID + App Secret**，命令提示时贴回终端。
2. **一键授权** —— 点命令打印的那条 deep-link（已把全部 7 个权限、含
   `im:message.group_msg` 勾好）→ 确认。
3. **事件** —— 事件与回调 → 订阅方式 = **使用长连接** → 加事件**接收消息**。
4. **发版** —— 应用发布 → 创建版本 → 申请发布 → **批准**（你是管理员就直接批；个人版免审核）。
5. 按**回车** —— 命令验证权限、建群、把凭证存到 `state/feishu_app.json`（0600）+ 写 `chat_id`。

</details>

> 想一条命令搞定第 2+3 步（用默认团队、不改 agent）？`claudeteam init --quick`——写默认配置 +
> 扫码连飞书（想群里免 @ 就用 `claudeteam init`，走浏览器自动化）。

## 第 4 步 · 起团队 + 验证

```bash
claudeteam install-hooks      # 装斜杠命令钩子（必须在 up 之前）
claudeteam up                 # 起 tmux 团队 + router + watchdog
claudeteam health             # 基础设施自检：binaries / env / tmux / router / watchdog
```

**真正起来了的判据是看飞书群**：一次全新 `up` 后，主管会**发起全员点名**、每个 worker 逐一
在群里汇报。看到这个 = 成。然后 `@manager 你好` → 约 30 秒内回复。

> ⚠️ **`health` 全绿 ≠ 团队能用**——它只查基础设施（进程/tmux/守护），不查每个 agent 的 CLI
> 是否真登录可用。**以群里点名为准**。点不动？多半是某个 agent CLI 本机没登录（跑一下
> `claude` 登录）、或那个 CLI 没装。可选手动探针（群里输入）：`/health`（每 agent + router +
> watchdog 卡片）、`/team`（每 agent ♥ 心跳 < 30 秒）。

**拆除：** `claudeteam down`（停掉、保留状态）· `claudeteam reset`（连状态一起清）。

---

## 配置：`claudeteam.toml`

单个 TOML 文件（Cargo 风格、可写注释）——`claudeteam init` 生成它，注释就地说明。App 凭证
**不**在这里（它们在 `state/feishu_app.json`）；这里只有 `chat_id` + 团队布局。

```toml
chat_id      = "oc_..."                       # 飞书群 chat_id（由 `feishu connect` 写入）
lark_profile = ""                             # lark-cli profile 名；"" = 默认
default_model = "opus"                        # agent 没指定 model 时的回退

[team]
session = "ClaudeTeam"                        # tmux session 名

[team.agents.manager]
cli = "claude-code"                           # claude-code | codex-cli | gemini-cli | kimi-code | qwen-code
                                              #   | minimax | opencode | codewhale | openclaw | trae | hermes | pi
role = "团队主管"                             # 渲染进 identity.md
model = "opus"
specialty  = ["调度", "审阅"]                 # 可选——manager 派单 prompt 里会看到
tone       = "稳重克制"                       # 可选——影响 LLM 语气
notes      = "always answer in Chinese"       # 可选——自由形式的 prompt 加料
playbook   = "manager.md"                     # 可选——角色指令 .md（→ 该 agent 的 CLAUDE.md/AGENTS.md）
card_color = "blue"
publish_overrides = { worker_to_user = false } # 单 agent 覆盖 [chat.publish]

[chat.publish]                                # 谁对谁可见的群过滤
user_to_manager   = "always"                  # 老板 → 主管（必达）
manager_to_user   = "always"                  # 主管 → 老板（必达）
manager_to_worker = true                      # 群里显示派单卡
worker_to_manager = true                      # 群里显示 worker 进度
worker_to_user    = true                      # 群里显示 worker 完成
worker_to_worker  = true                      # 群里显示 worker 之间的互 ping
```

默认全开（什么都可见）——等团队噪声需要削减时，再把单个键翻成 `false`。**覆盖优先级**
（高者胜）：`env` > `claudeteam.toml` > 代码默认（见 `runtime/tunables.py`）。

**团队模板** —— 与其从零写团队，不如从 [`templates/`](../templates/) 里的领域模板起步
（software-dev / automated-research / marketing-growth / data-analysis / content-ops）：
一个现成的 `claudeteam.toml` + 每个 agent 一份角色 **playbook** `.md`。某个 agent 的
`playbook` 文件会成为它身份的主体——它原生的 `CLAUDE.md` / `AGENTS.md`——叠加在团队协议
之上，于是每个 agent 一上线就知道自己该干什么，而不只是一行头衔。把某个文件夹的内容拷到
你的 `claudeteam.toml` 旁边（`playbook` 路径相对它解析）改一改即可。任何领域都能自己写，
就是一个 `.md`。改完用 `claudeteam reidentify <agent> --print` 可以**预览**某个 agent
渲染出来的身份（role + playbook + 团队协议,打到 stdout、不用起团队),`up` 之前就能验。

---

## Agent CLIs

每个 agent 跑一个 coding CLI —— 你用哪些就装哪些（ClaudeTeam 只要它在 PATH 上）。默认团队全是
`claude-code`，装个 `claude` 就能跑。

| 适配器 | `cli` | 安装 |
| --- | --- | --- |
| Claude Code | `claude-code` | `npm i -g @anthropic-ai/claude-code` |
| Codex CLI | `codex-cli` | `npm i -g @openai/codex` |
| Kimi Code | `kimi-code` | `uv tool install kimi-cli` |
| Gemini CLI | `gemini-cli` | `npm i -g @google/gemini-cli` |
| Qwen Code | `qwen-code` | `npm i -g qwen-code` |
| MiniMax Mini-Agent | `minimax` | `uv tool install "git+https://github.com/MiniMax-AI/Mini-Agent.git"` |
| opencode | `opencode` | `npm i -g opencode-ai` |
| CodeWhale | `codewhale` | `npm i -g codewhale` |
| OpenClaw | `openclaw` | `npm i -g openclaw` · 需 Node ≥ 22 |
| Trae | `trae` | `uv tool install --with docker --with pexpect "git+https://github.com/bytedance/trae-agent.git"` |
| Hermes | `hermes` | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash -s -- --skip-setup` |
| Pi | `pi` | `npm i -g @mariozechner/pi-coding-agent` |

后七个是 **OpenAI 兼容**（BYOK）—— 凭证 + 端点见下。

---

## 每个 agent 的模型后端（凭证 + 端点）

**首次启动这些都不用** —— 默认那两个 agent 跑在你的 Claude Code OAuth 上（复用本机登录）。
只有当你把某个 agent 切到非 Anthropic 后端时才来看这一节。

适配器是**provider 无关的**——DeepSeek/OpenAI/等等没有任何东西被写死在里面。后端由
env + config 选定：

- **凭证** —— 由 `runtime/agent_auth` 解析，优先级 **token > login > api_key**（高者
  存在就覆盖低者）。密钥放在一个 gitignore 的 env 文件（`$CLAUDETEAM_SECRETS_FILE`，
  默认 `<state_dir>/.env`）或进程 env——**绝不**放 `claudeteam.toml`。单 agent 覆盖：
  `<AGENT>_<VAR>`（例 `WORKER_PI_OPENAI_API_KEY`）。
  - **claude-code / codex / kimi** —— 各自的 token/login/api_key 变量。
  - **其它所有 CLI**（minimax、opencode、codewhale、openclaw、trae、hermes、pi）——
    走 **api_key** 那档：设 `OPENAI_API_KEY`。
- **端点** —— `OPENAI_BASE_URL`（例 `https://api.openai.com/v1` 或一个自建 vLLM/Ollama
  URL——任意 OpenAI 兼容 API）。**模型** —— 每个 `[team.agents.<name>]` 里的 `model` 字段。
- **Provider 名**（仅当某个 CLI 需要它来选一个 OpenAI 兼容的 *chat/completions* 客户端
  时）：`CLAUDETEAM_TRAE_PROVIDER`（默认 `openrouter`）、`CLAUDETEAM_PI_PROVIDER` /
  `CLAUDETEAM_CODEWHALE_PROVIDER`（默认 `openai`）。
- 一个**跑在非 Anthropic 后端上的 claude-code 主管**用 Anthropic 兼容变量：
  `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`（+ `ANTHROPIC_MODEL` /
  `ANTHROPIC_DEFAULT_*_MODEL`）。

示例 —— 把 OpenAI 兼容的 worker（以及可选的、跑在非 Anthropic 后端上的 claude-code
主管）指向**任意** provider，通过 `docker -e` 或宿主 shell。把 host 换成你实际用的
（某个托管 API 或本地 server）：

```bash
OPENAI_BASE_URL=https://your-provider.example/v1   # provider 的 base URL
OPENAI_API_KEY=sk-...                              # 你的 key
# 跑在非 Anthropic 后端上的 claude-code 主管用 Anthropic 兼容变量：
ANTHROPIC_BASE_URL=https://your-provider.example/anthropic
ANTHROPIC_AUTH_TOKEN=sk-...
```

各 CLI 的 `CLAUDETEAM_<CLI>_PROVIDER` 变量（见上）用来选 chat/completions 客户端——
除非你的 provider 需要特定值，否则保持默认即可。各 CLI 的具体配置见
`tests/scenarios/<cli>.md`。

---

## agent 之间通信：`send` vs `say`

| 命令 | 干什么 | 到得了 worker 的 pane 吗？ |
| --- | --- | --- |
| `claudeteam send <to> <from> <msg>` | inbox 记一行 **+** 注入 tmux pane | **是**——直接唤醒收件人 |
| `claudeteam say <agent> "<msg>" --to <role>` | 发进飞书会话（受 `[chat.publish]` 约束） | 仅当 router 把它转回来时 |
| 飞书群 → router → `deliver.apply` | 入站会话 → inbox 一行 + 注入 pane | **是**——老板/主管输入会唤醒 worker |

**`say` 永远带 `--to`**：`--to user` = 回老板；`--to manager` = 内部进度；
`--to worker_<name>` = 同级 ping。不带的话会回退成 `user`，并让 publish 过滤失效。

---

## 多团队隔离

state 存在**每个团队 `claudeteam.toml` 旁边的 `state/` 目录**里——配置文件的位置*就是*
团队身份。再跑一个团队不需要任何特殊 env，把每个团队放在各自的目录里即可：

```bash
cd /path/to/team-a && claudeteam up
# 另一个 shell：
cd /path/to/team-b && claudeteam up
```

每个团队各自保留 `team-a/state/` 和 `team-b/state/`，agent、状态、收件箱互不串味。
想把 state 放到别处（比如 Docker 卷），用 `CLAUDETEAM_STATE_DIR` 覆盖即可。

每个团队仍需**自己的飞书 App**（独立 app_id/secret）——多团队共用一个会导致凭证泄漏 +
事件路由冲突。

---

## 命令

**运维 CLI** —— `claudeteam --help` 按分组列出全部（它自维护；比这里任何表都可信）。
日常那几个：`up` / `down` / `health` / `team` / `peek <agent>` / `usage` /
`reidentify` / `remember` / `recall` / `switch`。

**会话侧斜杠命令**（`install-hooks` 之后，在 manager pane 里被识别；老板也能发——它们
零 LLM、经 router 直接派发）：

| 斜杠 | 干什么 |
| --- | --- |
| `/help` | 列出全部斜杠命令（卡片） |
| `/team` | 所有 agent 的 pane 实时状态 |
| `/health` | 服务器 CPU / 内存 / 磁盘 卡片 |
| `/usage` | Token/额度用量（ccusage / codex / kimi） |
| `/tmux [agent] [N]` | 抓某个 pane 的最后 N 行 |
| `/send <agent> <msg>` | 往某个 pane 注入一条消息 |
| `/compact [agent]` | 压缩 CLI 上下文 + 排期重新 identify |
| `/stop [agent]` | 打断 agent（Esc；pane 仍活着） |
| `/clear <agent>` | `/clear` 这个 CLI + 重注入身份 |
| `/task [all]` | 只读任务看板 |
| `/shutdown [confirm]` | pane 下线，保留 router/watchdog 以便 `/restart` |
| `/restart` | 重启整个团队（≈ down→up） |
| `/login <cli> [agent]` | 触发某个 CLI 重新登录；显示验证 URL/码 |

---

## 常见故障

### `claudeteam feishu connect` 没反应 / 报"已取消"

终端非交互（管道 / 非 TTY）或你按了 Ctrl-C，会得到"已取消（无输入 / 非交互终端）"——在一个
**可交互的终端**里重跑即可。不带参数的（浏览器自动化）模式要**桌面浏览器**，无头服务器上跑不了
——在那种机器上请到一台有桌面的机器上跑，再把 `state/feishu_app.json` + `chat_id` 拷过去
（或改用不需要浏览器的 `--quick` / `--manual`）。控制台界面在它脚下变了，它会自动退回
`--manual`；你也可以直接指定 `--manual` 自己点一遍。`--quick` 会先打印二维码再等你扫。

### `up` 后群里发消息没反应 / router 反复重启

多半是 **sidecar 的 WebSocket(长连接)没建起来**——router 起 sidecar 收事件，sidecar 连不上
就吐错退出，router 跟着退出、watchdog 再拉起，如此循环。router 日志里会看到
`⚠️ subscribe child exited` 紧跟一段 **`↳ sidecar 最后输出` + `↳ 诊断`**，照它两条修：
1. **该应用没开长连接订阅** → 飞书开发者后台 → 事件与回调 → 订阅方式 → 改成「使用长连接接收
   事件/回调」(不是 Webhook URL)，保存。(`--quick` 一般自动配好；手动建的应用要查这项。)
2. **HTTPS_PROXY 代理挡了 WebSocket** → 启动前 `export LARK_CLI_NO_PROXY=1`，或写进
   `$CLAUDETEAM_SECRETS_FILE`(默认 `<state>/.env`)/ shell profile。

入站只要 sidecar 连上就通；若 sidecar 连上了、群里还是没反应，才去查 manager 的 `claude`
登录态（见下条）。

### pane 里 `claude: not found` / `codex: not found`

pane 继承启动它的那个 shell 的 `$PATH`。如果你开了个新终端、忘了
`source .venv/bin/activate`，pane 就没有项目 venv。从一个能解析到 agent CLI 的 shell
重新 `up`。

### claude pane 报 "Not logged in"（macOS host）

每个 pane 有自己的 `~/.claude/.credentials.json` 快照（每 agent 的 home 隔离），它从你
本机登录态种出来、相对 keychain 可能过期。修法：`claudeteam down && up` 重新落一份。

### `router.log` 每 ~120 秒打印 "no live events … rotating subscribe"

**通常是正常的、不是故障——尤其 macOS 上。** 空闲会话上 WebSocket 会安静下来；router
自己 self-SIGTERM（`_watch_subscribe_health`），watchdog 重生它，catchup 从飞书 REST
API 把漏掉的重新拉回来——这套恢复循环**就是设计**。平台相关的空闲阈值是 Darwin 120 秒
/ Linux 600 秒（用 toml 里的 `router.stale_event_threshold_s` 或
`CLAUDETEAM_ROUTER_STALE_S` 覆盖）。两种形态：

- `ℹ️ no live events for Ns — rotating subscribe (none inbound yet …)` —— 空闲，预期。
- `⚠️ live events stopped after Ns idle …` —— 事件本来在流、然后停了（值得注意，Linux 上尤其）。

日志永远不会打印 "I received your message"——改信 `claudeteam health` 的 `inbound:` 行
+ 一条真实群消息。如果 `⚠️` *一直*出现，找找是不是有第二个 sidecar 在偷事件：
`ps -ef | grep -E "feishu_channel/sidecar\.js run" | grep -v grep`。

### `up` 后主管在同一条锚定消息上打转

catchup 会重放所有比 cursor 新的消息（带一个 `state/router.seen` 去重集，自动在 5000
条处裁剪）。还在重复？删掉 `state/router.seen`，把 `state/router.cursor` 往前推到
“现在”，让下次 catchup 跳过更旧的。

### pane 里 `say` 报 HTTP 400 "Bot/User can NOT be out of the chat"

从你启动的 shell 里 `say` 能成，但同样的调用从 pane 内部就失败。原因：一个早就存在的
tmux **server**（来自更早一次 `up`、不同 checkout）持有它最初的全局 env，
`tmux new-session` 继承的是*那个*、不是你当前 shell 的。lifecycle 前缀现在已按 spawn-cmd
嵌入凭证，所以干净状态下不该触发。若仍然触发：

```bash
tmux ls 2>/dev/null
ps -ef | grep -E "claudeteam (router|watchdog)|feishu_channel/sidecar\.js" | grep -v grep
claudeteam down
tmux kill-session -t ClaudeTeam        # 没有别的 tmux 活儿就用 `tmux kill-server`
claudeteam up
```

### `say` / sidecar 找不到 App 凭证

出站卡片失败，或 sidecar 退出抱怨没有 app id/secret。凭证只从一个源解析：
`state/feishu_app.json`（由 `feishu connect` 写入，0600），`feishu/lark.py:subprocess_env()`
读它，往 sidecar（入站）和 lark-cli（出站）注入 `FEISHU_APP_ID`/`SECRET` + 一个 tenant
token。`ls -l state/feishu_app.json`（期望 `-rw-------`）；缺了就重跑
`claudeteam feishu connect`。

### `worker_codex`（或别的 codex agent）显示 "pane up but CLI not ready yet"

Codex 有时开机带一个 "update available" 提示，挡住了 ready 标记：

```bash
tmux send-keys -t ClaudeTeam:worker_codex 3 Enter   # "Skip until next version"
claudeteam reidentify worker_codex
```

---

## 文件都在哪

```
src/claudeteam/
├── cli.py             单一 console-scripts 入口；只做派发
├── commands/          每个子命令一个模块（各 ~30-300 LOC）
├── store/             本地文件状态（inbox、status、logs、tasks、memory）
├── agents/            CliAdapter 基类 + 各 CLI 适配器 + 身份渲染
├── runtime/           config / paths / tmux / watchdog / pidlock / wake / lifecycle / tunables
└── feishu/            lark-cli 封装 + chat + router + slash + deliver + subscribe + catchup
scripts/feishu_channel/  @larksuite/channel sidecar（注册 + 入站）
tests/                 unit/ + integration/（stdlib runner）+ scenarios/（运维 playbook）
```

`CLAUDE.md`（项目根）放着开发规则 + 当前工作单——改代码前先读它。

---

## 卡住了？发现 bug？

active development 中——我们**12 小时内回应**。

- 🐛 **GitHub issue** —— [开一个](https://github.com/zylMozart/ClaudeTeam/issues/new/choose)。
  附上 OS、部署模式（host / Docker）、失败命令的输出（`feishu connect` 的问题就附
  sidecar 的 stderr）。
- 💬 **微信社区群** —— 扫 [README](../README_zh.md#遇到问题--提-bug) 里的二维码。

如果你是替用户跑部署的 AI agent、在真正尝试恢复后某步仍失败——把这一节抛给用户，后面
有真人维护者可联系。
