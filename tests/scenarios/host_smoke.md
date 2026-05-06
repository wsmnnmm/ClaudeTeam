# macOS 本机部署冒烟测试 — 一分钟版

## 目的

刚部署完，想用一分钟过一遍证明能用。覆盖：

- 部署上线（venv 激活、`claudeteam up`、`claudeteam health`）
- 用户 OAuth（设备授权流程，只跑一次终身有效）
- 9 条斜杠命令全覆盖
- 普通文本路由（验证 R174「manager 是唯一接口」契约）
- worker 反向路由（worker 卡片自动转回 manager 收件箱）

不覆盖：容器部署（看 [docker_deploy.md](docker_deploy.md)）、Round C 真任务协作（看 [round_c_real_task.md](round_c_real_task.md)）。

## 适用范围

- 平台：macOS（Apple Silicon 或 Intel）。Linux 主机大部分通用，但 keychain 部分要换成文件路径
- 已装：Python 3.10+（macOS 上推荐 `/opt/homebrew/bin/python3.14`）、tmux、node + npx、`claude` 或 `codex` 在 PATH 中
- 已建：飞书自建 App，开放平台后台开了 `im:message` 权限并启用了 `im.message.receive_v1` 长连接事件订阅
- 机器人已加入目标群，群的 `chat_id` 已知

## 0. 前置环境变量（每次新开终端都要设）

```bash
cd /path/to/ClaudeTeam
source .venv/bin/activate
export CLAUDETEAM_STATE_DIR="$PWD/state"
export LARK_CLI_NO_PROXY=1
export CLAUDETEAM_LARK_SEND_AS=bot
export PYTHONUNBUFFERED=1
```

## 1. 团队上线

```bash
claudeteam up        # 起 tmux 会话 + router + watchdog
claudeteam health    # 三个 agent ✅，router 与 watchdog 都活着
```

**通过条件**：health 输出全绿（最多容忍 `lark_profile blank` 一条 ⚠️，不致命）。

**失败排查**：

- "claude: not found"——CLI 适配器找不到二进制，检查 `$PATH`
- "pane up but CLI not ready"——常见原因是 codex 弹更新提示。`tmux capture-pane -t ClaudeTeam:worker_codex -p` 看一眼，按 `3 Enter` 选「Skip until next version」即可

## 2. 用户 OAuth（一次性）

如果 `lark-cli auth list` 显示「No logged-in users」，跑一次：

```bash
LARK_CLI_NO_PROXY=1 lark-cli auth login --domain im --recommend --no-wait --json
```

输出里的 `verification_url` 就是浏览器要打开的地址，登录飞书账号点「授权」。
然后用返回的 `device_code` 完成：

```bash
LARK_CLI_NO_PROXY=1 lark-cli auth login --device-code <从上一步拷贝>
```

授权成功后 token 写进 macOS keychain（service `lark-cli-credentials`，账号是你的 open_id），永久有效（自动续期）。

之后冒烟就可以用 `--as user` 模拟你自己发消息：

```bash
LARK_CLI_NO_PROXY=1 lark-cli im +messages-send \
  --chat-id <你的 chat_id> --text "/team" --as user
```

## 3. 斜杠命令矩阵（9 条 + 1 条边界用例）

每条都用 `--as user` 触发，等 router 接收 → 看群里有没有期望的卡。

```bash
CHAT="oc_xxxxx"   # 你部署用的 chat_id
SEND() { LARK_CLI_NO_PROXY=1 lark-cli im +messages-send --chat-id "$CHAT" --text "$1" --as user --format json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("data",{}).get("message_id",d))'; }

SEND "/help"                  # 期望 🆘 命令清单卡
SEND "/team"                  # 期望 👥 三 agent 状态卡
SEND "/health"                # 期望 🩺 服务器 CPU/内存/磁盘卡
SEND "/usage"                 # 期望 📊 用量卡（约 3-5 秒，慢）
SEND "/tmux"                  # 期望 📺 manager 默认 10 行
SEND "/tmux worker_cc"        # 期望 📺 worker_cc 10 行
SEND "/tmux worker_codex 25"  # 期望 📺 worker_codex 25 行
SEND "/tmux foobar"           # 期望「⚠️ 未知 agent」
SEND "/foo"                   # 期望「⚠️ 未知斜杠命令，建议 /help」
```

**通过条件**：每条都在 10 秒内有对应卡片落地。可以用下面这条拉群历史核对：

```bash
LARK_CLI_NO_PROXY=1 lark-cli im +chat-messages-list --chat-id "$CHAT" --as bot --page-size 12 --format json
```

**失败排查**：

- 某条没回——看 `state/router.log`（提交 `c0996a5` 之后才有），定位是不是 `[slash]` 入口之后 `[send_card] result=None`
- 卡片标题对不上发的命令——看 [slash_matrix.md](slash_matrix.md) 的失败标准表

### 状态变更类（按需）

下面 4 条会真改 worker 状态，只在你愿意承担副作用时跑：

```bash
SEND "/send worker_cc smoke ping"   # 直接注入 pane，worker_cc 会回「收到」
SEND "/compact worker_cc"           # 触发对话压缩，约 30-45 秒不可用
SEND "/clear worker_cc"             # 清掉历史对话上下文
SEND "/stop worker_cc"              # 杀 pane，需要 `claudeteam hire worker_cc` 复活
```

## 4. 普通文本路由（验证 R174）

证明所有人话最终都进 manager 的收件箱。

```bash
SEND "你好"                       # 无 @ 无前缀
SEND "@worker_cc 你在吗"          # 显式 @worker_cc
SEND "@team 全员同步进度"         # 广播触发词
SEND "全体注意"                   # 中文广播触发词
```

**通过条件**：4 条全都进 manager 的收件箱，**任何一条**都不应进 worker_cc 或 worker_codex 的收件箱。

```bash
claudeteam inbox manager       # 应有 4 条新未读
claudeteam inbox worker_cc     # 应有 0 条新（除非上一节 /send 测过）
claudeteam inbox worker_codex  # 应有 0 条新
```

**失败排查**：

- worker_cc 收件箱里冒出「你在吗」——R174 没生效，回去看 `feishu/router.classify_event` 是不是被回退了
- 没人收到——router 没在跑或飞书事件订阅断了；重启 router 后看 `state/router.log` 是否出现 `[event] action=route`

## 5. Worker → manager 反向路由（R174 的例外分支）

证明 worker 自己发的卡能让 manager 看到。

```bash
# 在 worker_cc pane 里跑（或直接命令行也行）：
claudeteam say worker_cc "反向路由冒烟测试" --card
```

**通过条件**：

1. 群里能看到 💎 worker_cc 的卡（卡片头是 worker 配色）
2. **manager 的收件箱多一条**，发件人是 worker_cc，内容是卡片文本
3. manager pane 看到这条新收件箱并开始处理

```bash
claudeteam inbox manager | head    # 最新一条来源应是 worker_cc
```

**失败排查**：

- manager 收件箱没这条——R174 的「worker 卡片回路 manager」分支没生效，看 `feishu/router._card_sender_agent`

## 6. 收尾

冒烟通过则不需清理；如果想回到干净状态：

```bash
claudeteam down
```

只停 pane 和守护进程，不会删收件箱、日志、游标。要彻底清空：`claudeteam reset`（看 [team_down_and_reset.md](team_down_and_reset.md)）。

## 已知的本机特有怪现象

1. **`/usage` 的 Claude Code 段会显示「读取失败」**——macOS 上 claude OAuth 存在 keychain，不在文件里。ccusage 找不到 `~/.claude/.credentials.json`。Codex 与 Kimi 段正常
2. **重新部署后 worker pane 可能「Not logged in」**——claude 续期 token 时只更新 keychain，每个 agent home 下的 `state/agent-home/<agent>/.claude/.credentials.json` 是当时快照，过几天会过期。临时解：`claudeteam down && claudeteam up`，让 lifecycle 从 keychain 重新物化一遍
3. **codex 启动可能弹更新框**——挡住 ready marker 60 秒超时。手动 `tmux send-keys -t ClaudeTeam:worker_codex 3 Enter` 选 Skip-until-next，再 `claudeteam reidentify --all`
4. **第一次 user OAuth 之后，每个新 shell 仍要 `export` 那 4 个环境变量**——没持久化的话 `claudeteam say` 偶尔会走 user 身份失败

## 不在范围

- 容器部署专属问题（`FEISHU_APP_ID` / tenant_access_token 自动注入）：看 [docker_deploy.md](docker_deploy.md)
- 多份部署互相切换：看 [team_switch.md](team_switch.md)
- manager 拆任务派 worker、worker 完工汇报、manager 写 review 报告（真协作）：看 [round_c_real_task.md](round_c_real_task.md)
- agent 之间互相发信（`claudeteam send worker_a worker_b "..."`）：看 [local_message_cycle.md](local_message_cycle.md)
