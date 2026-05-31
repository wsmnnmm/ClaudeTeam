"""`claudeteam init [--session NAME] [--force] [--upgrade]`

First-time bootstrap: writes `claudeteam.toml` (the unified config file
that replaces team.json + runtime_config.json) with sensible defaults
and inline comments.

`--upgrade` mode: scans for legacy `team.json` + `runtime_config.json`
in cwd, merges them into a `claudeteam.toml`, leaves the originals as
backup. Lets existing deployments migrate without losing their team
config.

Refuses to overwrite an existing `claudeteam.toml` unless --force.
"""
from __future__ import annotations

from claudeteam.runtime import config as _config, paths
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag,
    reject_extra_args,
)


USAGE = "usage: claudeteam init [--session NAME] [--force] [--upgrade]"


# ── default schema as a string template (preserves comments) ─────


_DEFAULT_TOML_TEMPLATE = """\
# ClaudeTeam 配置（单文件替代 team.json + runtime_config.json）
# 每个字段都可被同名 env var 覆盖：
#   CLAUDETEAM_<PATH>_<KEY>  例 router.stale_event_threshold_s
#                            → CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S
# 优先级: env > 本文件 > 代码硬编码默认

# ── 部署常量（必填）─────────────────────────────────────────
chat_id      = ""                         # 飞书群 chat_id（机器人加群后用 lark-cli 取）
lark_profile = ""                         # lark-cli profile 名, 空字符串走默认
default_model = "opus"                    # team.json agent 没指定 model 时回退到这里

# ── [team]  团队成员 ──────────────────────────────────────
[team]
session = "{session}"

# 每个 agent 一个 [team.agents.<name>]
#   cli         必填  claude-code | codex-cli | gemini-cli | kimi-code | qwen-code
#   role        必填  渲染进 identity.md
#   model       可选  缺省走 default_model
#   specialty   可选  list of strings, manager 派单时参考
#   tone        可选  字符串, 渲染进 identity 影响 LLM 输出风格
#   notes       可选  字符串, 任意 prompt 加料
#   provider_preset 可选 员工级 provider preset 名, 从 state/provider-presets.json 解析
#   provider_env 可选  inline env 覆盖, 例如 ANTHROPIC_BASE_URL / AUTH_TOKEN / DEFAULT_*_MODEL
#   card_color  可选  飞书 v2 色: blue/green/red/yellow/purple/orange/grey
#   lazy        可选  true=首消息触发起 CLI; 默认 false
#   identity_profile 可选  slim=常驻短身份 + skill/SOP 按需加载; full=历史长身份
[team.agents.manager]
cli   = "claude-code"
model = "opus"
role  = "团队主管"
identity_profile = "slim"
card_color = "blue"

[team.agents.worker_cc]
cli   = "claude-code"
model = "sonnet"
role  = "Claude Code 员工"
identity_profile = "slim"
card_color = "green"

[team.agents.worker_codex]
cli   = "codex-cli"
model = "gpt-5.5"
role  = "Codex 员工"
identity_profile = "slim"
card_color = "purple"

# ── [chat.publish]  群里能看到什么消息 ─────────────────────
# sender→receiver 维度过滤; 角色: user (老板) / manager / worker
# 值: true=进群发卡  false=只走 send/inbox 不进群  "always"=不可关
# 默认全 true / "always" — 测试 / 早期阶段尽量多看到事实, 减少静默漏消息
# 的认知盲区。生产化后再针对噪声大的通道 (worker_to_manager 等) 调 false。
[chat.publish]
user_to_manager   = "always"
manager_to_user   = "always"
manager_to_worker = true
worker_to_manager = true
worker_to_user    = true
worker_to_worker  = true

# ── [limits]  消息长度上限 ────────────────────────────────
[limits]
max_card_body_chars         = 4000
auto_split_long_messages    = true
tmux_capture_default_lines  = 10
tmux_capture_max_lines      = 2000
inbox_unread_warn_threshold = 50

# ── [wake]  Pane 唤醒时序 ──────────────────────────────────
[wake]
lazy_wake_timeout_s    = 30
ready_marker_timeout_s = 60

# ── [router]  路由器守护进程 ───────────────────────────────
[router]
# stale_event_threshold_s — 多久没事件就 self-SIGTERM 让 watchdog 重生.
# 注释掉则用平台默认 (Darwin 120 / Linux 600). 显式设了就用你的值.
# 为什么 macOS 默认更紧: lark-cli 1.0.23 macOS WebSocket subscribe 会
# silent-drop 不重连; 紧阈值让 self-restart + catchup 在 ~2 min 内补回
# 漏的事件而不是 ~10 min. Linux WebSocket 稳定, 600s 避免空闲群被反复
# 重启 (180s 太紧, 1200s 太松, 都踩过坑).
# stale_event_threshold_s     = 600
catchup_poll_interval_s       = 30.0   # 显式 REST 心跳/补漏周期；老板消息最坏按此级别补进来
catchup_failure_reconnect_count = 3    # 连续心跳失败后让 router 重连
restart_on_catchup_miss       = true   # 回补抓到漏消息后重连 subscribe 快路径
catchup_miss_reconnect_grace_s = 5.0   # subscribe 空闲超过此值才因漏消息重连
lark_call_timeout_s            = 90     # 单次 lark-cli 调用超时
alarm_card_color               = "red"  # 守护进入 cooldown 时报警卡片颜色
seen_max_lines                 = 5000   # router.seen 去重表 trim 阈值
subscribe_watchdog_period_s    = 20.0   # 内部订阅子进程健康检查周期

[router.fast_ack]
enabled = true
max_age_s = 180
text = "收到，已进入主管前台。这只是自动入队回执，不代表已完成处理；我会先分诊话题/任务，再给事实、卡点和下一步。"

[router.first_response]
enabled = false      # true=老板->manager 时走独立真实模型首响通道，默认保守关闭
provider = "anthropic"
endpoint = "responses" # responses=OpenAI兼容快通道；失败时自动回退 messages/chat
model = "haiku"      # haiku/sonnet/opus alias 会从 provider env 解析
timeout_s = 8.0
max_age_s = 180
max_tokens = 180
max_chars = 180
temperature = 0.2
send_as_user = false
reply_to_original = false

[router.boss_preempt]
enabled = true       # 老板→manager 高优先级消息可中断 busy manager pane
keys    = "C-c"      # tmux send-keys 语法；必要时可改成 "Esc"

# ── [watchdog]  daemon 守护循环 ────────────────────────────
[watchdog]
check_interval_s        = 30    # 守护 tick 周期 (查 router 是否还活)
cred_check_interval_s   = 300   # 多久查一次 Claude OAuth 是否快过期
cred_refresh_ahead_s    = 1800  # 剩余 < 此值时强制 refresh OAuth

# ── [manager_watch]  manager 派工超时兜底 ──────────────────
[manager_watch]
enabled          = true   # manager 派给 worker 后, 由 watchdog 做超时兜底
check_interval_s = 30     # 多久扫描一次未闭环任务
overdue_s        = 600    # worker 超过多久无信号就私下提醒 manager
repeat_s         = 900    # 同一个未变化任务多久重复提醒一次
max_task_age_s   = 21600  # 首次上线只盯最近 6h 的任务, 避免历史旧卡刷屏
public_overdue_s = 1800   # 群里兜底卡阈值；待验收+已有产物只私下提醒 manager
chat_alert       = true   # true=严重超时才群里发兜底卡; false=只提醒 manager inbox/pane
card_color       = "orange"
boss_inbox_overdue_s = 300        # 老板消息快回后多久未读就重投给 manager
boss_inbox_repeat_s = 300         # 同一条老板未读消息多久重复提醒
boss_inbox_public_overdue_s = 600 # 超过多久在群里发“已自动重投”兜底卡
boss_inbox_max_age_s = 21600      # 首次上线只盯最近 6h 的老板消息

# ── [topic_digest]  每日话题恢复卡快照 ─────────────────────
[topic_digest]
enabled = true
interval_s = 86400
out_dir = "reports/topic-digests"
include_closed = false

# ── [cockpit_sync]  老板驾驶舱事实流 ───────────────────────
# off by default because this writes to a real Feishu Base.
# Enable it in exactly one "owner" team so multiple watchdogs do not race.
[cockpit_sync]
enabled    = false
root       = ""     # 多团队根目录；如 /Users/wsm/Project。空=只同步当前 team cwd
interval_s = 120    # watchdog 周期写回；事实变化也可手动跑 cockpit-sync
base_token = "Hjsibewe7aL9RmsYiUEcjq3bn3e"
table_id   = "tblEyoEGZOZ0gfJr"
agent_table_id = ""  # 可选：员工级明细表，如“员工状态明细”
task_table_id = "tblJ67mLhY9oM91G"  # 可选：老板任务流/任务卡片表
remote_state_dir = ""  # 可选：云上事实快照目录，如 product-lab/state/remote-teams
profile    = ""     # 空=使用本 team 的 lark_profile

# ── [base_intake]  多维表格编辑 -> 团队任务下发 ─────────────
# off by default. Enable only in the same cockpit owner team.
[base_intake]
enabled          = false
root             = ""     # 多团队根目录；通常与 cockpit_sync.root 一致
base_token       = "Hjsibewe7aL9RmsYiUEcjq3bn3e"
task_table_id    = "tblJ67mLhY9oM91G"
cockpit_table_id = "tblEyoEGZOZ0gfJr"
event_types      = ["drive.file.bitable_record_changed_v1"]
trigger_statuses = ["待下发", "老板已决策", "已确认", "执行", "立即执行"]
decision_fields  = ["老板决策", "决策指令", "执行指令"]
action_fields    = ["老板操作", "人工操作"]
writeback        = true
writeback_field  = "下发回执"
clear_action_after_dispatch = true

# ── [feishu]  飞书桥接 ─────────────────────────────────────
[feishu]
send_as          = "bot"
no_proxy         = true
cli_bin          = ""
broadcast_tokens = ["@team", "@all", "@everyone"]
"""


def _render_template(session: str) -> str:
    return _DEFAULT_TOML_TEMPLATE.format(session=session)


# ── --upgrade: merge legacy team.json + runtime_config.json ──────


def _upgrade_from_legacy(session: str) -> str:
    """Read existing team.json + runtime_config.json from cwd, merge
    into a single claudeteam.toml string. Caller is responsible for
    writing it.

    Strategy: start from the default template, override the relevant
    sections from legacy files. Comments preserved by string-substituting
    only known fields.
    """
    legacy_team = _config.load_team()                 # via legacy reader
    legacy_runtime = _config.load_runtime_config()    # via legacy reader

    template = _render_template(legacy_team.get("session") or session)

    # Replace chat_id / lark_profile lines
    if cid := legacy_runtime.get("chat_id"):
        template = template.replace(
            'chat_id      = ""                         #',
            f'chat_id      = "{cid}"  #', 1)
    if lp := legacy_runtime.get("lark_profile"):
        template = template.replace(
            'lark_profile = ""                         #',
            f'lark_profile = "{lp}"  #', 1)
    if dm := legacy_team.get("default_model"):
        if dm != "opus":
            template = template.replace(
                'default_model = "opus"',
                f'default_model = "{dm}"', 1)

    # Replace agent block. Drop the 3 default agents and rebuild from legacy.
    legacy_agents = legacy_team.get("agents", {})
    if legacy_agents:
        # Cut from "[team.agents.manager]" through next top-level section
        agents_start = template.find("[team.agents.manager]")
        agents_end = template.find("\n# ── [chat.publish]", agents_start)
        if agents_start != -1 and agents_end != -1:
            new_agent_block = ""
            for name, cfg in legacy_agents.items():
                lines = [f"[team.agents.{name}]"]
                lines.append(f'cli   = "{cfg.get("cli","claude-code")}"')
                if model := cfg.get("model"):
                    lines.append(f'model = "{model}"')
                if role := cfg.get("role"):
                    lines.append(f'role  = "{role}"')
                lines.append('identity_profile = "slim"')
                if cfg.get("lazy"):
                    lines.append("lazy  = true")
                # default card_color by name prefix
                color = ("blue" if name == "manager"
                         else "purple" if "codex" in name
                         else "orange" if "kimi" in name
                         else "yellow" if "gemini" in name
                         else "green")
                lines.append(f'card_color = "{color}"')
                new_agent_block += "\n".join(lines) + "\n\n"
            template = (template[:agents_start]
                        + new_agent_block.rstrip() + "\n"
                        + template[agents_end:])

    return template


# ── main ─────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    force = pop_bool_flag(rest, "--force")
    upgrade = pop_bool_flag(rest, "--upgrade")
    session = pop_flag(rest, "--session") or "ClaudeTeam"
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc

    cfg_path = paths.config_file()

    if cfg_path.exists() and not force:
        return error_exit(
            f"❌ {cfg_path} already exists; pass --force to overwrite")

    if upgrade:
        # Sanity check legacy files actually exist before running merge,
        # otherwise --upgrade gives no value over plain init.
        team_path = _config.team_file()
        rt_path = _config.runtime_config_file()
        if not team_path.exists() and not rt_path.exists():
            return error_exit(
                f"❌ --upgrade: neither {team_path.name} nor {rt_path.name} "
                f"found in cwd; nothing to migrate")
        content = _upgrade_from_legacy(session)
    else:
        content = _render_template(session)

    cfg_path.write_text(content, encoding="utf-8")
    print(f"✅ wrote {cfg_path}")
    print()
    if upgrade:
        team_path = _config.team_file()
        rt_path = _config.runtime_config_file()
        print(f"  legacy {team_path.name} + {rt_path.name} preserved as backup;")
        print(f"  remove them once you've verified `claudeteam health` is green.")
    else:
        print("Next:")
        print(f"  - edit {cfg_path.name} to set chat_id + adjust agents")
        print("  - claudeteam install-hooks   # write .claude/commands/*.md")
        print(f"  - claudeteam up              # tmux session '{session}' + router + watchdog")
        print("  - claudeteam health          # verify green")
    return 0
