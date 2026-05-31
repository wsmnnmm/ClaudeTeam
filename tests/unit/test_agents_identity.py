"""Tests for agents/identity.py — per-agent identity markdown rendering."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.agents import identity
from claudeteam.store import memory


# ── render() — template selection ─────────────────────────────────


def test_render_manager_uses_manager_template():
    """Round-85: manager identity rewritten in Chinese with reference/main's
    rich management discipline (角色边界 / 秒回闭环 / 巡视核实 / 集合指令铁律)."""
    text = identity.render("manager", role="团队主管",
                           cli="claude-code", model="opus")
    assert "团队主管" in text
    assert "manager" in text
    # Core management rules from main's manager.identity.md
    assert "管理分发铁律" in text
    # R174: manager is the sole interface to boss; all routing is
    # 老板 → manager → claudeteam send → workers. The identity now
    # spells out the dispatch flow + visibility into worker say replies.
    assert "唯一接口" in text
    assert "claudeteam send" in text
    # Argument-order contract carried over from rebuild's earlier version
    assert "claudeteam send <recipient> <sender>" in text
    assert "主管空转优先" in text
    assert "主管亲跑 vs 派 worker" in text
    assert "自然语言进度汇报优先" in text
    assert "飞书回复上下文" in text
    assert "禁止把“这个是什么意思”当孤立问题回答" in text


def test_render_manager_slim_identity_profile_keeps_core_rules_shorter():
    full = identity.render("manager", role="团队主管",
                           cli="claude-code", model="opus")
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "团队主管",
        "identity_profile": "slim",
    }}}
    with isolated_env(team=team):
        slim = identity.render("manager")
    assert len(slim) < len(full) * 0.55
    assert "不要让 Opus 做杂活" in slim
    assert "低成本执行" in slim
    assert "evidence-first-debugging" in slim
    assert "bin/ct send <recipient> manager" in slim
    assert "bin/ct say manager - --to user" in slim
    assert "按需读取的 SOP 索引" in slim
    assert "老板今天该做什么" not in slim


def test_render_worker_slim_identity_profile_uses_skill_index_not_full_protocol():
    full = identity.render("worker_cc", role="frontend",
                           cli="claude-code", model="sonnet")
    team = {"agents": {"worker_cc": {
        "cli": "claude-code",
        "model": "sonnet",
        "role": "frontend",
        "identity_profile": "slim",
    }}}
    with isolated_env(team=team):
        slim = identity.render("worker_cc")
    assert len(slim) < len(full) * 0.65
    assert "team worker" in slim
    assert "Superpowers 工作流内核" in slim
    assert "brainstorming" in slim
    assert "writing-plans" in slim
    assert "verification-before-completion" in slim
    assert "老板今天该做什么" not in slim
    assert "bin/ct send manager worker_cc" in slim


def test_render_worker_uses_worker_template():
    text = identity.render("worker_cc", role="frontend",
                           cli="claude-code", model="sonnet")
    assert "team worker" in text
    assert "Pick up tasks" in text
    assert "bin/ct task list --assignee worker_cc" in text
    assert "--artifact <path> --done" in text


# ── render() — substitutions ──────────────────────────────────────


def test_render_substitutes_name_role_cli_model():
    text = identity.render("worker_codex", role="backend",
                           cli="codex-cli", model="gpt-5.5")
    assert "worker_codex" in text
    assert "backend" in text
    assert "codex-cli" in text
    assert "gpt-5.5" in text


def test_manager_has_collective_dispatch_hard_constraint():
    """Boss-flagged 2026-05-06: main 分支主管 identity 里"硬约束：集合类
    指令必须 dispatch，不得代替汇总" 这段非常重要——每个 manager 都得
    学会。rebuild 派活流程提到了，但要作为带关键词触发器 + 强约束语
    的独立 hard-constraint 段呈现，不只是 R174 路由说明顺带带过。"""
    text = identity.render("manager", role="主管", cli="claude-code", model="opus")
    # 独立小节标题（强强约束）
    assert "硬约束" in text
    assert "集合类指令" in text or "集合类" in text
    # 触发关键词列表（main 的原 5 条 + rebuild 自己加的 @team / @all）
    for kw in ("全员", "all hands", "@team", "大家都", "每个人都"):
        assert kw in text, f"missing trigger keyword: {kw}"
    # 严厉约束语
    assert "绝不代替员工发汇总" in text
    assert "绝不一条 say 代替 N 次 send" in text


def test_render_argument_order_contract_present_in_manager():
    text = identity.render("manager", role="r", cli="c", model="m")
    assert "claudeteam send <recipient> <sender>" in text
    assert "claudeteam say <agent>" in text
    assert "bin/ct task list --assignee manager" in text
    assert "没 artifact 不准关单" in text
    assert "❌" in text and "✅" in text


def test_render_argument_order_contract_present_in_worker():
    text = identity.render("w", role="r", cli="c", model="m")
    assert "claudeteam send <recipient> <sender>" in text
    assert "claudeteam say <agent>" in text
    assert "bin/ct task list --assignee w" in text
    assert "❌" in text and "✅" in text


def test_render_warns_against_cd_in_both_templates():
    """REGRESSION: round 5 smoke caught worker_cc prefixing \`cd /repo &&\`
    on its first reply attempt, which broke chat_id resolution. Both
    templates must include an explicit "do not cd" rule."""
    for agent in ("manager", "w"):
        text = identity.render(agent, role="r", cli="c", model="m")
        assert "Team command isolation rule" in text
        assert "Do NOT" in text and "cd" in text
        assert "wrong Feishu group" in text


# ── render() — defaults from team.json ────────────────────────────


def test_render_pulls_defaults_from_team_json_when_args_omitted():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                   "role": "captain"}}}
    with isolated_env(team=team):
        text = identity.render("manager")
    assert "captain" in text
    assert "claude-code" in text
    assert "opus" in text


def test_render_falls_back_when_team_json_missing_fields():
    team = {"agents": {"w": {}}}
    with isolated_env(team=team):
        text = identity.render("w")
    # name is the agent name; cli defaults to claude-code; model empty
    assert "**w**" in text
    assert "claude-code" in text


# ── identity_path() / write() ─────────────────────────────────────


def test_identity_path_under_state_dir():
    with isolated_env() as tmp:
        p = identity.identity_path("worker_kimi")
        assert p == tmp / "state" / "agents" / "worker_kimi" / "identity.md"


def test_write_persists_file_and_creates_parents():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                    "role": "团队主管"}}}
    with isolated_env(team=team) as tmp:
        path = identity.write("manager")
        assert path.exists()
        assert path == tmp / "state" / "agents" / "manager" / "identity.md"
        text = path.read_text(encoding="utf-8")
        # Round-85: manager body now in Chinese, anchored on "管理分发铁律"
        assert "团队主管" in text
        assert "管理分发铁律" in text


def test_write_creates_team_safe_command_wrapper():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus"}}}
    with isolated_env(team=team) as tmp:
        path = identity.write("manager")
        wrapper = tmp / "state" / "bin" / "ct"
        pointer = tmp / "state" / "config-file.path"
        assert path.exists()
        assert wrapper.exists()
        assert pointer.read_text(encoding="utf-8").strip() == str(tmp / "claudeteam.toml")
        text = path.read_text(encoding="utf-8")
        assert str(wrapper) in text
        assert "do not use bare `claudeteam`" in text


# ── Step 2: specialty / tone / notes 字段 ───────────────────────


def test_render_includes_specialty_section_when_set():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "specialty": ["内容审核", "文案润色"],
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 专长" in text
    assert "内容审核" in text
    assert "文案润色" in text


def test_render_omits_specialty_section_when_unset():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 专长" not in text


def test_render_includes_tone_section_when_set():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "tone": "细致、礼貌、详尽",
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 风格" in text
    assert "细致、礼貌、详尽" in text


def test_render_includes_notes_section_when_set():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "notes": "擅长长文本审阅; 不擅长数据工作",
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 备注" in text
    assert "擅长长文本审阅" in text


def test_manager_renders_team_specialties_block():
    """Manager should see each non-manager agent's specialty so it can
    dispatch with awareness."""
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "策划",
                      "specialty": ["文案", "排版"]},
        "worker_codex": {"cli": "codex-cli", "model": "gpt-5.5", "role": "数据",
                         "specialty": ["SQL", "数据可视化"]},
    }}
    with isolated_env(team=team):
        text = identity.render("manager")
    assert "## 团队成员专长" in text
    assert "worker_cc" in text and "文案" in text
    assert "worker_codex" in text and "SQL" in text


def test_worker_does_not_get_team_specialties_block():
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "策划",
                      "specialty": ["文案"]},
    }}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 团队成员专长" not in text


def test_manager_omits_team_specialties_block_when_no_worker_has_specialty():
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "策划"},
    }}
    with isolated_env(team=team):
        text = identity.render("manager")
    # 没人有 specialty → block 也不出现
    assert "## 团队成员专长" not in text


# ── Step 4b: identity 模板教 LLM 用 --to ────────────────────


def test_manager_identity_teaches_to_user():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                    "role": "主管"}}}
    with isolated_env(team=team):
        text = identity.render("manager")
    # manager 必须看到 `--to user` 用法和 chat.publish 提示
    assert "--to user" in text
    assert "chat.publish" in text


def test_manager_identity_dispatch_step_uses_to_user():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                    "role": "主管"}}}
    with isolated_env(team=team):
        text = identity.render("manager")
    # 派活流程 step 3 例子要带 --to user，并走 stdin 安全路径
    assert "bin/ct say manager - --to user" in text
    assert "已派给 N 位" in text
    assert "stdin" in text


def test_worker_identity_teaches_both_to_targets():
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    # worker 要知道两个常见 --to 值
    assert "--to user" in text
    assert "--to manager" in text


def test_identity_requires_to_explicit():
    """两个 body 都要明确告诉 LLM "每条 say 都必须显式带 --to" — 避免 LLM
    偷懒省略。Step 4b 烟测发现 prompt 里"省略等价"豁免句让 LLM 不再带
    --to，于是改成强约束。"""
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "员工"},
    }}
    with isolated_env(team=team):
        mgr = identity.render("manager")
        wkr = identity.render("worker_cc")
    # 强约束句出现在两个 body 中
    assert "必须显式带" in mgr or "必须" in mgr and "--to" in mgr
    assert "必须显式带" in wkr or "必须" in wkr and "--to" in wkr
    # 不再有"省略等价"的豁免句
    assert "省略 `--to` 等价" not in mgr
    assert "省略 `--to` 等价" not in wkr


def test_render_includes_boss_first_flagship_protocol():
    """Boss-flagged 2026-05-19: every role needs the boss-first
    protocol so outputs stay boss-facing, discussion summaries stay
    useful, and sensitive assets stay out of plain memory/chat."""
    mgr = identity.render("manager", role="r", cli="c", model="m")
    wkr = identity.render("worker_cc", role="r", cli="c", model="m")
    for text in (mgr, wkr):
        assert "Boss-First Flagship Protocol" in text
        assert "老板今天该做什么" in text
        assert "大家在争什么" in text
        assert "Founder OS 阶段闸门" in text
        assert "当前阶段、阶段出口证据、今天最小证据动作、不做什么" in text
        assert "只记“存在性 + 检索路径 + 使用协议”" in text
        assert "Product Lab 看产品与收钱" in text
        assert "跨团队协作：cross-track 协议" in text
        assert "cross-track 协议" in text
        assert "claudeteam cross-track" in text
        assert "claudeteam mentor-request" in text
        assert "mentor-score-loop" in text
        assert "交接确认" in text
        assert "--image-caption" in text
        assert "claudeteam correction-cases" in text
        assert "claudeteam boss-experience-audit" in text
        assert "claudeteam evolution-health" in text
        assert "刘小排协作式排障三步" in text
        assert "先给证据" in text
        assert "可证伪原因" in text
        assert "evidence-first-debugging" in text


def test_write_overwrites_existing_file():
    """Round-88 caught: worker body now mentions 'oldest auto-drop' so a
    naive 'old' substring leaks. Pin the role line explicitly so the
    override is what's being tested."""
    team = {"agents": {"w": {"cli": "claude-code", "model": "opus",
                              "role": "FIRST_ROLE"}}}
    with isolated_env(team=team):
        path = identity.write("w")
        first = path.read_text(encoding="utf-8")
        assert "FIRST_ROLE" in first
        # render again with override
        identity.write("w", role="SECOND_ROLE")
        second = path.read_text(encoding="utf-8")
        assert "SECOND_ROLE" in second
        assert "FIRST_ROLE" not in second


def test_write_if_changed_reports_only_real_identity_changes():
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "主管",
    }}}
    with isolated_env(team=team):
        path, changed = identity.write_if_changed("manager")
        assert changed is True
        first = path.read_text(encoding="utf-8")

        path2, changed2 = identity.write_if_changed("manager")
        assert path2 == path
        assert changed2 is False

        team["agents"]["manager"]["identity_profile"] = "slim"
        # isolated_env wrote claudeteam.toml once; mutate by rewriting through
        # the helper's environment with a fresh isolated config.
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "主管",
        "identity_profile": "slim",
    }}}
    with isolated_env(team=team):
        path = identity.write("manager")
        path.write_text(first, encoding="utf-8")
        _, changed = identity.write_if_changed("manager")
        assert changed is True
        assert "Manager 瘦身" not in path.read_text(encoding="utf-8")
        assert "按需读取的 SOP 索引" in path.read_text(encoding="utf-8")


# ── init_prompt() — round-84 memory injection ─────────────────────


def test_init_prompt_omits_memory_section_when_empty():
    """Brand-new agent: no memory file, no extra section appended.
    Avoids confusing the agent with a `## 既往记忆` block that's empty."""
    with isolated_env():
        prompt = identity.init_prompt("manager")
        assert "bin/ct inbox manager" in prompt
        assert "既往记忆" not in prompt


def test_init_prompt_uses_absolute_identity_path():
    """The Read instruction must use an absolute path so panes whose
    CWD isn't the project root can still resolve the file. Container
    deploys spawn at /app; codex pane in 2026-05-07 docker smoke
    surfaced 'agents/worker_codex/identity.md was missing' because
    the relative form didn't resolve from /app."""
    with isolated_env():
        prompt = identity.init_prompt("worker_cc")
        # The path in the prompt must be absolute (starts with `/`)
        # AND must end at the canonical state-relative location.
        import re
        m = re.search(r"Read (\S+identity\.md)", prompt)
        assert m, f"prompt must contain `Read <path>identity.md`; got: {prompt[:200]}"
        path = m.group(1)
        assert path.startswith("/"), \
            f"identity path must be absolute, got relative: {path!r}"
        assert path.endswith("/agents/worker_cc/identity.md")


def test_init_prompt_uses_team_safe_wrapper():
    with isolated_env() as tmp:
        prompt = identity.init_prompt("worker_cc")
        wrapper = tmp / "state" / "bin" / "ct"
        assert wrapper.exists()
        assert str(wrapper) in prompt
        assert f"{wrapper} say worker_cc - --to user" in prompt
        assert "do not use bare `claudeteam`" in prompt


def test_init_prompt_includes_real_time_and_lightweight_context_rule():
    """Agents should wake with actual wall-clock context, but without
    dumping growing daily logs into the prompt."""
    with isolated_env():
        prompt = identity.init_prompt("worker_cc")
    assert "当前真实时间（本机本地时区）" in prompt
    assert "轻量上下文规则" in prompt
    assert "今天/上午/刚才/之前/还记得吗" in prompt
    assert "bin/ct recall worker_cc" in prompt
    assert "只带回查到的少量最新事实" in prompt


def test_init_prompt_teaches_to_explicit_say():
    """Step 4c: init prompt 也要强调 --to 必带。烟测 (step4-llm-1778077887)
    发现仅靠 identity body 不够 — LLM 处理 inbox 时直接看 init prompt 的
    say 例子。例子不带 --to → LLM 跟着省略。"""
    with isolated_env():
        prompt = identity.init_prompt("worker_cc")
    # 例子带 --to user
    assert "--to user" in prompt
    # 强约束语出现
    assert "MUST" in prompt or "必须" in prompt
    # 提示 manager / user 两个目标
    assert "manager" in prompt and "user" in prompt


def test_init_prompt_manager_targets_user_only_in_hint():
    """manager 的 init prompt 提示只列 --to user（manager 没有"对自己说"
    的场景）。"""
    with isolated_env():
        prompt = identity.init_prompt("manager")
    assert "--to user" in prompt


def test_init_prompt_manager_repeats_boss_first_gate():
    with isolated_env():
        prompt = identity.init_prompt("manager")
    assert "老板视角优先" in prompt
    assert "三层配置不是摆设" in prompt
    assert "员工手册 / 上岗 SOP / MCP / 工位权限" in prompt
    assert "卡点上报不等于停止解决" in prompt
    assert "老板动作边界" in prompt
    assert "大家在争什么" in prompt
    assert "只记存在性/检索路径/使用协议" in prompt
    assert "最终作战表" in prompt
    assert "截图附件证据" in prompt
    assert "禅道上有图，现在啥都没有了？" in prompt
    assert "旧结论立即标 stale" in prompt
    assert "同一事实不要换包装重复回传" in prompt
    assert "短回执" in prompt
    assert "task 状态、artifact_path、reviewed_by" in prompt
    assert "内部督办不等于老板汇报" in prompt
    assert "看到 [飞书回复上下文]" in prompt
    assert "先解释父消息/被回复内容" in prompt
    assert "图片/截图/学习卡/报告默认发飞书图片" in prompt
    assert "本地路径只作内部备份" in prompt
    assert "Founder OS" in prompt
    assert "Idea/MVP/Launch/Scale" in prompt
    assert "阶段出口证据" in prompt
    assert "manager 决策必须留痕" in prompt
    assert "claudeteam log manager decision" in prompt
    assert "AI 导师双入口" in prompt
    assert "AI 刘小排和 AI 亦仁分开提问" in prompt
    assert "规则有生命周期" in prompt
    assert "归档详细规则" in prompt
    assert "刘小排协作式排障三步" in prompt
    assert "不许愿式改代码" in prompt
    assert "proof package" in prompt


def test_init_prompt_manager_slim_profile_uses_short_redlines():
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "主管",
        "identity_profile": "slim",
    }}}
    with isolated_env(team=team):
        prompt = identity.init_prompt("manager")
    assert "Manager 瘦身红线" in prompt
    assert "Opus 只做判断" in prompt
    assert "准备 compact/recycle" in prompt
    assert "合并成一条 Bash" in prompt
    assert "禅道上有图，现在啥都没有了？" not in prompt
    assert "AI 刘小排和 AI 亦仁分开提问" not in prompt


def test_init_prompt_teaches_inbox_processing_after_R168():
    """R168: the prompt now tells agents to PROCESS unread messages
    (post a chat response when it's a status / 报道, mark each read),
    not just count them. Boss-flagged after the 全员报道 e2e where
    worker_cc read its inbox but didn't follow up with a chat reply.
    Step 4c: --no-card teaching dropped (R169 made it a no-op)."""
    with isolated_env():
        prompt = identity.init_prompt("worker_cc")
        # Per-message processing instruction
        assert "For EACH unread inbox message" in prompt
        # Tells agent to use the safe wrapper for status reports
        assert "bin/ct say worker_cc" in prompt
        # Tells agent to mark each message read
        assert "bin/ct read" in prompt
        assert "bin/ct status worker_cc 进行中" in prompt
        assert "only a takeover signal" in prompt
        assert "Do not stop at read/status" in prompt


def test_init_prompt_appends_memory_when_present():
    """After memory.append, the next init_prompt should include the
    memory block so a /clear-ed pane re-reads its prior context on wake."""
    with isolated_env():
        memory.append("manager", "task_assigned", "fix login bug", ref="om_1")
        memory.append("manager", "learning", "auth uses bcrypt")
        prompt = identity.init_prompt("manager")
        # Base reporting still present
        assert "bin/ct inbox manager" in prompt
        # Memory block present
        assert "## 既往记忆" in prompt
        assert "[task_assigned] fix login bug (ref=om_1)" in prompt
        assert "[learning] auth uses bcrypt" in prompt
        # Tail nudge tells agent what to do with the recall
        assert "继续之前未完成的工作" in prompt
