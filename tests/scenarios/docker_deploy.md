# Docker deployment

## 场景

把 ClaudeTeam 跑在容器里，state + lark-cli OAuth profile 通过 volume
持久化。容器本身不带任何 agent CLI（claude / codex / kimi）—— 这些
都需要单独 auth + licence；这一层由调用方派生镜像或 bind-mount 进来。

## 范围

- 类型：host-live (Docker)
- 凭证：host 上已有 `~/.lark-cli/profiles/<profile>.yaml` (lark-cli login 完成)
- 操作员：boss / devops

## Given

- Docker engine 24+ 在 host 上能跑
- host 上有 `~/teams/projectA/`（含 `team.json` + `runtime_config.json`，
  例如通过 `claudeteam init` 在该目录里生成过）
- host 上有效 `~/.lark-cli/profiles/<profile>.yaml`，profile 的 chat_id
  与 runtime_config.json 对得上
- host 上至少有一个 agent CLI（claude / codex / kimi）— 进容器要么
  通过派生镜像 RUN install，要么 bind-mount `/usr/local/bin/claude`
  这种二进制路径

## When

```bash
# 1. Build 基础镜像
docker compose build

# 2. 把 host 的 team-data 链给容器（只 init 一次）
mkdir -p team-data
cp ~/teams/projectA/team.json ~/teams/projectA/runtime_config.json team-data/

# 3. 起容器（detached）
docker compose up -d

# 4. 进容器把 team 拉起来
docker compose exec claudeteam claudeteam install-hooks
docker compose exec claudeteam claudeteam up
docker compose exec claudeteam claudeteam health

# 5. 容器内 attach tmux 看 panes
docker compose exec claudeteam tmux attach -t ClaudeTeam
```

## Then

`claudeteam health` 输出绿:

- ✅ `team.json` / `runtime_config.json` 走 /data 卷
- ✅ `chat_id` / `lark_profile` 来自挂载的 runtime_config
- ✅ tmux session 起在容器里
- ⚠️ 每个 agent pane 状态取决于该 CLI 是否在容器 `$PATH` —— 派生
  镜像 / bind-mount / 都没做的话会是 `pane up but CLI not ready yet`
- ✅ router / watchdog 的 pid 文件落到 `/data/state/router.pid` /
  `/data/state/watchdog.pid`
- ✅ 退出 `docker compose down` 后再 `up`，team-data 持久化、`claudeteam health`
  能立即读出上次的 cursor / status 历史

## Why this is here

CLAUDE.md item 18 (Dockerfile + compose) 的最小可行实现。两条原则：

1. **基础镜像不带 agent CLI** — claude / codex / kimi 各有 auth +
   licence + 体积考虑，硬塞进基础镜像就把 ClaudeTeam 绑死在某条
   provider 流水线上。派生镜像里 add 自己想跑的就行。

2. **CMD 是 sleep infinity，不是 `claudeteam up`** — `up` 让 tmux 起
   detached 后立刻退出 host 进程，容器会因为 PID 1 退出而停掉。改成
   sleep 让容器活着，`docker compose exec` 每次手动驱动 lifecycle
   命令；这跟 host 上的操作 pattern 一致（user 自己 `claudeteam up`），
   减少行为分叉。

## Known caveats

- **macOS host: lark-cli auth doesn't carry into container** — round-59
  smoke caught this. lark-cli stores app secrets + user OAuth tokens
  in the macOS keychain (`source: keychain` in config.json). The
  container can mount `~/.lark-cli/config.json` but CANNOT reach the
  host's macOS keychain. Result: `lark-cli ... --as user` fails with
  `Error: need_user_authorization`; `--as bot` fails with
  `Error: TAT API error: [10003] invalid param`. Workaround:
    1. Run claudeteam on a Linux host (keychain doesn't apply, secrets
       go in `~/.lark-cli/config.json` directly).
    2. Or run an interactive `lark-cli login` *inside* the container
       once at first boot, persisting tokens via the `/root/.lark-cli`
       volume.
  Not a blocker for non-Feishu-touching tests (`claudeteam health`,
  `claudeteam team`, local inbox flow).

## Out of scope

- **多容器编排**：每个 agent 各自一容器、router 单独一容器 etc. ——
  现在的 ClaudeTeam runtime 把 router/watchdog/panes 共享一个 tmux
  session，改成多容器就要重新设计 IPC，留给 future 工作。
- **CI/CD 集成**：smoke conductor 在容器里跑、push 镜像到 registry
  这些都不在 B.3 范围内。手动 build + run 就够最小可行。
- **Windows host**：Docker Desktop 上 host 网络只是部分模拟，lark-cli
  long-poll 可能要换 bridge + port-publish。Linux 上按上面 pattern
  直接能跑；macOS 上 build 需要 `docker build --network host`，并
  受限于 keychain 问题（见 Known caveats）。
