# ClaudeTeam 云上团队稳定性说明

日期：2026-05-24
用途：给外部 AI 顾问/团队顾问复盘 ClaudeTeam 云上团队为什么仍有迟钝、不收口、偶发失稳，并讨论下一轮优化。

## 一句话结论

这不是单点故障。`反斜杠 / 引号 / 反引号` 确实是一个真实稳定性漏洞，但它更像“传输层尖刺”，不是这次 Product Lab 云上超时的唯一根因。

更完整的链路是：

1. 群消息进入系统后有 fast ack，所以老板会看到“收到”。
2. fast ack 不等于 manager 已经开始执行，只证明消息进入队列/路由。
3. manager 可能因为云端重启、模型/provider 轮班、旧上下文、pane 未就绪、任务抢占而迟迟没有真实动作。
4. manager-watch 会在超时后发橙卡，说明“老板消息还没有形成实质处理/收口”。
5. 当 manager 终于回群时，如果仍用 `claudeteam say manager "..." --to user` 这类 shell 双引号形式，消息中的反引号、`$`、反斜杠、URL query、Markdown 代码块可能在到达 `claudeteam say` 前被 shell 改写。
6. worker 执行到基础设施任务时，又可能撞到云端配置漂移，例如 systemd 指向的 sing-box config 路径已不存在。

所以要按“端到端链路”修，不要只修某一个提示词。

## 这次云上 Product Lab 的证据链

已观察到的现象：

- Product Lab 云团队并没有完全死掉：router/watchdog/tmux/manager/worker 仍可达。
- 群里先出现 fast ack，说明入口链路通了。
- 随后 manager-watch 发“老板消息长时间未收口”橙卡，说明 ack 之后 manager 没有在阈值内形成实质行动。
- manager 后来处理了 watchdog 提示，创建/推进了 VPN 订阅相关任务，并派给 `worker_ops`。
- manager 回群时使用了双引号包长消息，消息里出现了反引号包住的 `127.0.0.1:7890`，shell 把反引号当成命令替换，导致公开回复内容被破坏。
- `worker_ops` 后续发现更底层的 infra drift：`sing-box` 进程还在跑，但 systemd `ExecStart` 指向的 config 路径不存在。当前代理可能因为进程已加载旧配置而继续可用，但服务不是安全可重启状态。

判断：

- 橙卡不是误报；它捕捉的是“收到之后没有收口”的真实缺口。
- 引号/反引号不是最早的超时原因，但它会让已经迟到的回复再次出错，造成老板体感上的“不稳定、不专业”。
- 云端 provider 轮班/故障切换可能影响 manager ready 状态，但不应该直接导致 `app secret invalid`；后者通常是飞书 app_id/app_secret/profile/env 不匹配，除非轮班或重启过程中切到了错误环境变量。

## 为什么优化很多仍然不稳

ClaudeTeam 现在已经有很多局部优化：fast ack、watchdog、catchup、manager-watch、task ledger、identity、memory、card reply、worker 回执等。

但这些优化分布在不同层：

- 入口层：飞书订阅、catchup、fast ack。
- 编排层：manager identity、任务账本、派工规则。
- 执行层：worker pane、CLI 工具、云端文件和服务。
- 回复层：`claudeteam say`、飞书 card、chat.publish。
- 运维层：provider 轮班、router/watchdog 重启、代理/VPN、systemd。

只要其中一层仍然把“不可靠行为”当成默认路径，整体体感就会回落。例如：

- fast ack 让人以为团队已经在处理，但 manager 还没开始。
- watcher 能发现超时，但如果 watcher 提示里仍教 agent 用双引号发长消息，下一步仍会错。
- `claudeteam say` 已支持 stdin 安全发送，但 identity/hook/nudge 还示范双引号，agent 会继续复制旧习惯。
- 云端服务进程还活着，但 systemd 配置路径已漂移，下一次重启才爆雷。

## 反斜杠/引号到底怎么影响

风险最大的是 shell 在命令执行前会先解释内容：

- 反引号：`` `cmd` `` 会触发命令替换。
- `$VAR` / `$(cmd)` 会被展开。
- `\n` 可能变成字面量，也可能被不同工具二次解释。
- 双引号内部仍会发生 `$`、反引号、反斜杠解释。
- URL query 里的 `&`、Markdown 代码块、JSON、路径、中文引号混用，都容易让 agent 复制命令时变形。

因此公开消息、长消息、含 Markdown/URL/路径/特殊符号的消息，都应该走 stdin：

```bash
cat <<'EOF' | claudeteam say manager - --to user
这里写真正要发给老板的内容。
EOF
```

短句如“收到”可以用 `printf`：

```bash
printf '%s\n' '收到' | claudeteam say manager - --to user
```

## 已做的基座修补

本轮已把 agent 会看到的关键提示改成 stdin 安全路径：

- `src/claudeteam/agents/identity.py`
- `src/claudeteam/runtime/manager_watch.py`
- `src/claudeteam/feishu/deliver.py`
- `src/claudeteam/commands/send.py`
- `src/claudeteam/commands/install_hooks.py`

并补了对应测试：

- `tests/unit/test_agents_identity.py`
- `tests/unit/test_runtime_manager_watch.py`
- `tests/unit/test_feishu_deliver.py`
- `tests/unit/test_commands_send.py`
- `tests/unit/test_commands_install_hooks.py`

验证结果：

```text
tests: 1202 passed, 0 failed
```

## 建议下一步优化

1. 把“收到”拆成两种指标：
   - `inbound -> fast_ack`：入口是否通。
   - `fast_ack -> first_real_action`：manager 是否真的行动。

2. 给 manager-watch 增加更细的 public reason：
   - 未读。
   - 已读但未派工。
   - 已派工但 worker 无动作。
   - worker 有产物但 manager 未验收收口。
   - 云端 provider/restart 后 manager 未 ready。

3. 云端每次轮班/重启后跑 ready gate：
   - router alive。
   - watchdog alive。
   - manager pane active。
   - manager identity 已重新注入。
   - `claudeteam health --json` 通过。
   - 最近一条测试消息能从群进 manager inbox。

4. 对基础设施任务加 preflight：
   - 当前进程使用的 config 路径。
   - systemd `ExecStart` 指向的 config 路径。
   - 文件是否存在。
   - 是否安全可重启。
   - 回滚路径。

5. 把团队优劣比较从“感觉”改成日志指标：
   - 入口延迟。
   - manager 首动作延迟。
   - 派工完整度。
   - worker 首动作延迟。
   - artifact 质量。
   - manager 验收/收口延迟。
   - 公开回复是否包含结论、证据、下一步、需要老板。

## 给 AI 顾问的讨论问题

1. 这个系统的最大不稳定来源，是 agent 决策迟钝，还是 shell/云端传输层脆弱，还是运维漂移？
2. fast ack 是否应该在公开回复里更明确区分“已进入队列”和“manager 已开始处理”？
3. manager-watch 应该只提醒 manager，还是也要在群里给老板一个“已触发督办”的透明状态？
4. 是否应该把 `say`/`send` 的安全 stdin 模式进一步抽象成无 shell 的内部消息 API？
5. 如果要证明 `website-chuhai-team` 真的优于 `product-lab`，应该采集哪些同口径指标？
6. provider 轮班机制应该怎样与 team readiness gate 绑定，避免“模型切过去了但团队没醒好”？
