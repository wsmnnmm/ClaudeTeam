# tests/scenarios/ — 烟测剧本索引

每篇 `.md` 是一份"操作员能照着跑、看到现象就能勾对错"的回归剧本。
不是单元测试（那些在 `tests/unit/`，跑 `python3 tests/run.py`）；
这里的剧本要真起 tmux pane、真发飞书消息、真看群里有没有卡——
单元测试覆盖不到的"真世界副作用"在这里收。

## 我刚部署完，想 1 分钟过一遍

→ **[host_smoke.md](host_smoke.md)** — macOS host 部署的最短验证路径：
9 条斜杠 + 2 条普通文本 + R174 验证。每一步带可复制的命令和判定。

新部署强烈建议先把这一篇跑绿，再考虑下面的细分主题。

## 按主题分组

### 部署与多团队
| 文件 | 范围 |
|---|---|
| [host_smoke.md](host_smoke.md) | macOS 本机部署一分钟冒烟（推荐入口） |
| [init_bootstrap.md](init_bootstrap.md) | `claudeteam init` 写 team.json 和 runtime_config.json |
| [docker_deploy.md](docker_deploy.md) | Docker compose 路径（容器部署，区别于本机部署） |
| [team_switch.md](team_switch.md) | 多份部署之间通过 `claudeteam switch` 切换环境 |

### 团队生命周期
| 文件 | 范围 |
|---|---|
| [team_lifecycle.md](team_lifecycle.md) | `start / hire / fire` 端到端 |
| [team_down_and_reset.md](team_down_and_reset.md) | `down` 与 `reset` 两条停机路径 |
| [spawn_cmd_per_cli.md](spawn_cmd_per_cli.md) | 每个 CLI 适配器生成的拉起命令字符串 |
| [identity_render.md](identity_render.md) | `agents/<name>/identity.md` 渲染 |
| [lazy_wake.md](lazy_wake.md) | 懒启动 worker 收到首条消息时拉起 CLI |
| [reidentify.md](reidentify.md) | 重新注入身份（compact / clear 之后） |

### 消息路由
| 文件 | 范围 |
|---|---|
| [local_message_cycle.md](local_message_cycle.md) | `send → inbox → read` 本地链路（不经飞书） |
| [router_event_to_pane.md](router_event_to_pane.md) | 飞书 → router → 收件箱与 pane 注入（核心端到端） |
| [router_catchup.md](router_catchup.md) | 路由重启时续读，不丢消息 |
| [orphan_subscribe_reap.md](orphan_subscribe_reap.md) | 看门狗清理残留的 `lark-cli +subscribe` 子进程 |
| [feishu_say_chat_send.md](feishu_say_chat_send.md) | `claudeteam say` 把一句话发到群 |
| [slash_matrix.md](slash_matrix.md) | 9 条斜杠命令的输出验收 |

### 状态、审计、健康
| 文件 | 范围 |
|---|---|
| [agent_status_and_audit.md](agent_status_and_audit.md) | `status` 上报与 `log` 审计 |
| [team_overview_and_workspace.md](team_overview_and_workspace.md) | `team` / `workspace` 读侧命令 |
| [health_check.md](health_check.md) | `claudeteam health` 部署快照 |

### 任务卡片
| 文件 | 范围 |
|---|---|
| [task_lifecycle.md](task_lifecycle.md) | `claudeteam task` 五条子命令 |

### 用量与版本
| 文件 | 范围 |
|---|---|
| [usage_snapshot.md](usage_snapshot.md) | `claudeteam usage` 包装 ccusage |
| [version_check.md](version_check.md) | `claudeteam version` |

### 真任务协作
| 文件 | 范围 |
|---|---|
| [round_c_real_task.md](round_c_real_task.md) | 老板 → manager → workers → 汇总（最完整端到端） |

### 杂货铺（待拆）
| 文件 | 范围 |
|---|---|
| [cards_memory_and_speed.md](cards_memory_and_speed.md) | 卡片样式、按 agent 记忆、看门狗告警、lark-cli 速度——四个主题缝在一起，待拆 |

## 已知滞后于代码的剧本

代码改了但没回头修文档。跑剧本前先核对这张表：

| 文件 | 滞后内容 | 涉及提交 |
|---|---|---|
| `slash_matrix.md` 的 R3-R8 | 仍写 `@worker_cc` / `@team` 分流。**实际 R174 之后所有人话只路由到 manager** | `9e43309 feat(router,identity): manager 成为唯一接口` |
| `router_event_to_pane.md` | 描述里"@ 提到的 worker 进对应 pane"。同样 R174 已改 | 同上 |
| `round_c_real_task.md` | 假设 4 个 pane 的团队（含 worker_kimi），任务拆 3 份。当前默认部署是 3 个 pane，没有 kimi | `claudeteam init` 默认配置 |
| `round_c_real_task.md` 的凭证字段 | profile 与 chat_id 写死成 `cli_a961274ccb385cc4` / `oc_989...`，与当前部署不一致 | 各部署互相独立 |
| 全部本机相关剧本 | macOS 特有路径（keychain、HOME 兜底、`~/.lark-cli/`）没有文档落盘 | `780fd08 fix(host-deploy)` |
| 缺失主题 | `state/router.log` 和 `state/watchdog.log` 落盘行为是新加的 | `c0996a5 feat(watchdog): 守护进程日志写入文件` |

## 命名规则

- 文件名 `<主题>.md`，全小写、下划线分隔
- 标题用一句话描述范围，**不要**写成"Round X"。这种命名会让文件按时间序而不是主题序排，失去索引价值
- 推荐统一模板：`## 范围` / `## 前置条件` / `## 操作` / `## 期望` / `## 已知风险` / `## 不在范围`

## 新增一篇剧本

1. 在 `commands/X.py` 或 `feishu/Y.py` 落码的同时写 `tests/scenarios/<主题>.md`（CLAUDE.md 规则 #2：每条公共命令必须配剧本）
2. 把入口加到本 README 对应主题分组
3. 如果它取代或废弃了已有剧本，把被替换的那篇标进"已知滞后"
4. 不要新建 `_v2` 或 `_round_X` 后缀文件——直接改原文件，git 历史会保留版本
