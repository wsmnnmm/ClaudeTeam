# Router catchup-on-restart

## 场景
Router daemon 死掉时（kill / 主机重启 / OOM），lark-cli `event +subscribe` 流断了——这期间发到群里的消息只在 Feishu 服务端，本地一行都没收到。重启 router 后，旧的 `state_dir/router.cursor` 标记着上次跑到哪条消息；router 先调 `chat-messages-list` 把 cursor 之后的所有 message 拉回来，按时间正序灌到 `subscribe.process_lines`，应用相同的路由 → inbox + tmux inject 链路。然后再启动 live subscribe。

每条 ROUTE Decision apply 完都会更新 cursor。drop（dedup / cross_team / bot_self）保留旧 cursor，因为它们没副作用。

## 范围
- 类型：host-live （需要真 lark-cli + 真 chat_id + 真 OAuth profile）
- 凭证：lark-cli 的 user 身份（`--as user`）能读 chat history

## Given
- `runtime_config.json` 含真的 `chat_id` + `lark_profile`
- `team.json` 至少含 manager
- `CLAUDETEAM_STATE_DIR=$PWD/state`
- 旧的 `router.cursor`（如果存在）记录了某个早于待 catchup 消息的 timestamp
- `claudeteam start` 已经把 tmux 起好

## When

```bash
# 1) 起 router 收一条消息，正常处理
claudeteam router &
ROUTER_PID=$!
# 在群里发 "hello A"
sleep 5
cat $CLAUDETEAM_STATE_DIR/router.cursor   # 应有 hello A 的 message_id

# 2) 杀 router
kill $ROUTER_PID
wait $ROUTER_PID 2>/dev/null

# 3) router 不在期间，发 "missed B" + "@worker_codex check C"

# 4) 重起 router
claudeteam router &
ROUTER_PID=$!
sleep 10  # 等 catchup 跑完
```

## Then
1. router 启动日志含 `📥 catching up <N> missed message(s)`，N == 期间错过的条数
2. `claudeteam inbox manager` 含 "missed B"
3. `claudeteam inbox worker_codex` 含 "check C"
4. tmux pane 看到 inject 文本（manager / worker_codex 各自的 banner 之后能看到）
5. live subscribe 仍正常运转：再发一条新消息能即时被处理
6. `router.cursor` 已推进到最后一条处理过的 message_id（含 catchup 期间的）

## 反例
- `chat-messages-list` 失败（OAuth 过期）：日志含 `catchup fetch failed`，但 live subscribe 仍照常启动（不会因为 catchup 异常阻塞）
- cursor 文件 corrupt：read_cursor 返回 `{}`，等同首次启动 → 拉回 page_size 条最近消息（前 50 条）。dedup 兜底重复
- 群里没有新消息：`pending: 0`，直接进 live

## 证据（执行时填）

```
- T_first_router: …
- 期间错过条数 N: …
- T_restart_router: …
- catchup 日志行: …
- 各 inbox 是否含错过的消息: pass | fail
- router.cursor 末态: …
- 后续: …
```
