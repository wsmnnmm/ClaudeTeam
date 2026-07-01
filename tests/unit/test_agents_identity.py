"""Tests for agents/identity.py — per-agent identity markdown rendering."""
from __future__ import annotations

import tempfile
from pathlib import Path

from helpers import attr_patch, isolated_env, run_cli
from claudeteam.agents import identity
from claudeteam.agents import base, claude_code
from claudeteam.agents.codex_cli import CodexCliAdapter
from claudeteam.store import memory, tasks
from claudeteam.runtime import paths


# ── render() — template selection ─────────────────────────────────


def test_render_manager_uses_manager_template():
    """Manager identity carries the full management discipline in English
    (role boundaries / instant-reply-and-close-the-loop / inspect-and-verify
    / the collective-order iron rule)."""
    text = identity.render("manager", role="Team Manager",
                           cli="claude-code", model="opus")
    assert "Team Manager" in text
    assert "manager" in text
    # Core management rules from the manager identity
    assert "集合指令" in text
    # manager is the sole interface to boss; all routing is
    # boss → manager → claudeteam send → workers. The identity now
    # spells out the dispatch flow + visibility into worker say replies.
    assert "manager" in text
    assert "claudeteam send" in text
    # Argument-order contract carried over from an earlier version
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


def test_render_manager_slim_identity_uses_natural_boss_visible_examples():
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "团队主管",
        "identity_profile": "slim",
    }}}
    with isolated_env(team=team):
        slim = identity.render("manager")
    assert "cat <<'EOF' | bin/ct say manager - --to user\n结论：" not in slim
    assert "结论：\n证据：\n下一步：\n需要老板：" not in slim
    assert "默认 4 行骨架" not in slim
    assert "不要把它们写成 `结论：`、`证据：`、`下一步：`、`需要老板：` 这种字段名发给老板" in slim
    assert "我先接住这件事" in slim


def test_render_manager_slim_identity_includes_process_anchor_for_multi_stage_work():
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "团队主管",
        "identity_profile": "slim",
    }}}
    with isolated_env(team=team):
        slim = identity.render("manager")
    assert "Xiaoqi-Process-Anchor" not in slim
    assert "process_anchor" in slim
    assert "current_phase" in slim
    assert "artifact_purpose" in slim
    assert "last_boss_correction_id" in slim
    assert "discard_previous_plan" in slim


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
    assert "集合指令" in text
    # 触发关键词列表（main 的原 5 条 + rebuild 自己加的 @team / @all）
    for kw in ("全员", "all hands", "@team", "@all", "大家都", "each person"):
        assert kw in text, f"missing trigger keyword: {kw}"
    # 严厉约束语
    assert "绝不代替员工发汇总" in text
    assert "对每个非-manager agent 跑一次 `send`" in text


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
    """REGRESSION: worker_cc prefixing \`cd /repo &&\` on its first reply
    attempt broke chat_id resolution. Both templates must include an
    explicit "do not cd" rule."""
    for agent in ("manager", "w"):
        text = identity.render(agent, role="r", cli="c", model="m")
        assert "Team command isolation rule" in text
        assert "Do NOT" in text and "cd" in text
        assert "wrong Feishu group" in text


def test_team_principles_survive_in_native_memory_projection():
    """The principles must live in the always-loaded native file too, not
    just identity.md — that's the channel that survives /compact for
    claude-code workers."""
    nt = identity.native_memory_text("worker_cc", role="worker",
                                     cli="claude-code", model="m")
    assert "## Team principles" in nt and "--intent" in nt


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
                                    "role": "Team Manager"}}}
    with isolated_env(team=team) as tmp:
        path = identity.write("manager")
        assert path.exists()
        assert path == tmp / "state" / "agents" / "manager" / "identity.md"
        text = path.read_text(encoding="utf-8")
        # manager body carries the dispatch/quality guardrails.
        assert "Team Manager" in text
        assert "集合指令" in text


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
    assert "## Specialty" in text
    assert "内容审核" in text
    assert "文案润色" in text


def test_render_includes_notes_section_when_set():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "notes": "擅长长文本审阅; 不擅长数据工作",
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## Notes" in text
    assert "擅长长文本审阅" in text


# ── playbook: a role instruction file that becomes the agent's identity body ──


def test_render_includes_playbook_when_set():
    """`playbook = "<file>"` projects a self-contained role doc into the agent's
    identity (after a divider), layered ON TOP of the team-protocol body — the
    mechanism domain templates use to ship rich per-role instructions."""
    from claudeteam.runtime import paths
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "playbook": "backend.md"}}}
    with isolated_env(team=team):
        (paths.config_file().parent / "backend.md").write_text(
            "# 后端工程师\n\n负责 API 与数据。PLAYBOOK_MARKER", encoding="utf-8")
        text = identity.render("worker_cc")
    assert "PLAYBOOK_MARKER" in text
    assert "后端工程师" in text
    assert "claudeteam send" in text   # team-protocol body still there, not replaced


def test_render_playbook_missing_file_degrades():
    """A playbook path that doesn't resolve must not crash the spawn — it just
    renders no playbook section."""
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "role": "员工", "playbook": "does-not-exist.md"}}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")   # must not raise
    assert "员工" in text


def test_render_resolves_config_fields_with_explicit_role():
    """REGRESSION: the lifecycle provision path renders identity via
    `write(role, cli, model)` — role/cli/model passed explicitly. That used to
    skip the config read and silently drop specialty/tone/notes/playbook. render
    must resolve config-backed fields in that path too."""
    from claudeteam.runtime import paths
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "specialty": ["SPECMARK"], "notes": "NOTEMARK", "playbook": "pb.md"}}}
    with isolated_env(team=team):
        (paths.config_file().parent / "pb.md").write_text("PLAYMARK", encoding="utf-8")
        # explicit role/cli/model — exactly what lifecycle.write passes
        text = identity.render("worker_cc", role="员工", cli="claude-code", model="sonnet")
    assert "SPECMARK" in text   # specialty no longer dropped on the lifecycle path
    assert "NOTEMARK" in text   # notes no longer dropped
    assert "PLAYMARK" in text   # playbook resolves too


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
    assert "## Team members' specialties" in text
    assert "worker_cc" in text and "文案" in text
    assert "worker_codex" in text and "SQL" in text


def test_manager_omits_team_specialties_block_when_no_worker_has_specialty():
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "策划"},
    }}
    with isolated_env(team=team):
        text = identity.render("manager")
    # 没人有 specialty → block 也不出现
    assert "## Team members' specialties" not in text


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
    assert "各自回报后我来收口" in text
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
    """Base identity keeps the generic boss-first protocol only.

    Business/team-specific overlays (mentor routing, Product Lab examples,
    TODO002 handoff details, Feishu permission specifics) must not leak into
    the base ClaudeTeam prompt by default.
    """
    mgr = identity.render("manager", role="r", cli="c", model="m")
    wkr = identity.render("worker_cc", role="r", cli="c", model="m")
    for text in (mgr, wkr):
        assert "Boss-First Flagship Protocol" in text
        assert "老板今天该做什么" in text
        assert "大家在争什么" in text
        assert "Founder OS 阶段闸门" in text
        assert "当前阶段、阶段出口证据、今天最小证据动作、不做什么" in text
        assert "只记“存在性 + 检索路径 + 使用协议”" in text
        assert "跨团队协作：cross-track 协议" in text
        assert "cross-track 协议" in text
        assert "claudeteam cross-track" in text
        assert "claudeteam correction-cases" in text
        assert "claudeteam boss-experience-audit" in text
        assert "claudeteam evolution-health" in text
        assert "先给证据" in text
        assert "可证伪原因" in text
        assert "evidence-first-debugging" in text
        assert "Product Lab 看产品与收钱" not in text
        assert "claudeteam mentor-request" not in text
        assert "mentor-score-loop" not in text
        assert "交接确认" not in text
        assert "--image-caption" not in text
        assert "刘小排协作式排障三步" not in text


def test_render_can_opt_into_flagship_overlay():
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "主管",
        "identity_overlay": "flagship",
    }}}
    with isolated_env(team=team):
        text = identity.render("manager")
    assert "claudeteam mentor-request" in text
    assert "mentor-score-loop" in text
    assert "交接确认" in text
    assert "--image-caption" in text
    assert "Product Lab 看产品与收钱" in text
    assert "刘小排协作式排障三步" in text


def test_write_overwrites_existing_file():
    """Worker body mentions 'oldest auto-drop' so a naive 'old' substring
    leaks. Pin the role line explicitly so the override is what's being
    tested."""
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
    deploys spawn at /app, where the relative form surfaced
    'agents/worker_codex/identity.md was missing' because it didn't
    resolve from /app."""
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
    assert "规则有生命周期" in prompt
    assert "归档详细规则" in prompt
    assert "proof package" in prompt
    assert "AI 导师双入口" not in prompt
    assert "AI 刘小排和 AI 亦仁分开提问" not in prompt
    assert "刘小排协作式排障三步" not in prompt
    assert "不许愿式改代码" not in prompt
    assert "禅道上有图，现在啥都没有了？" not in prompt
    assert "给老板新建飞书云文档时默认直接开编辑权限" not in prompt


def test_init_prompt_manager_flagship_overlay_restores_business_redlines():
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "主管",
        "identity_overlay": "flagship",
    }}}
    with isolated_env(team=team):
        prompt = identity.init_prompt("manager")
    assert "AI 导师双入口" in prompt
    assert "AI 刘小排和 AI 亦仁分开提问" in prompt
    assert "刘小排协作式排障三步" in prompt
    assert "不许愿式改代码" in prompt
    assert "禅道上有图，现在啥都没有了？" in prompt
    assert "给老板新建飞书云文档时默认直接开编辑权限" in prompt


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
    """The prompt tells agents to PROCESS unread messages (post a chat
    response when it's a status / 报道, mark each read), not just count
    them — surfaced when an agent read its inbox but didn't follow up
    with a chat reply. Step 4c: --no-card teaching dropped (it became a
    no-op)."""
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


# ── Phase C: CLI-native memory file (claude's ~/.claude/CLAUDE.md) ──


def test_claude_adapter_native_memory_path_is_in_agent_home():
    """claude points at ~/.claude/CLAUDE.md inside the agent's isolated
    HOME — so each agent gets its own native file with no collision."""
    path = claude_code.ClaudeCodeAdapter().native_memory_path("worker_cc")
    assert path is not None
    assert path.endswith("/worker_cc/home/.claude/CLAUDE.md")
    # Same HOME root the spawn_cmd uses → claude actually reads it.
    assert path.startswith(paths.agent_home("worker_cc"))


def test_native_memory_text_combines_identity_policy_and_digest():
    """The native file body = identity + standing remember policy +
    current memory digest, so all three load natively each session."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        memory.append("worker_cc", "decision", "use redis for sessions")
        text = identity.native_memory_text("worker_cc")
    assert "team worker" in text                  # identity body
    assert "Memory maintenance" in text                       # policy heading
    assert "claudeteam remember" in text          # policy teaches the command
    assert "[decision] use redis for sessions" in text  # memory digest


def test_native_memory_text_omits_digest_when_no_memory():
    """Brand-new agent: policy is present but no `## 既往记忆` block
    (avoid injecting an empty section)."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        text = identity.native_memory_text("worker_cc")
    assert "Memory maintenance" in text
    assert "## 既往记忆" not in text


# ── intent anchor (anti-drift double-insurance) ───────────────────


def _suspend_free_in_progress(assignee, title, intent_id):
    """Helper: create a task already 进行中 and intent-linked."""
    tid = tasks.create(assignee, title, intent_id=intent_id)
    tasks.update(tid, status="进行中")
    return tid


def test_native_memory_text_anchors_active_intent_verbatim():
    """A worker with an active intent-linked task gets the boss's verbatim
    raw_text anchored into its always-loaded CLAUDE.md projection."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("把支付页改成两步结账，别加第三步")
        _suspend_free_in_progress("worker_cc", "重构结账", iid)
        text = identity.native_memory_text("worker_cc")
    assert "Boss's verbatim anchor" in text
    assert "把支付页改成两步结账，别加第三步" in text  # verbatim, not paraphrased
    assert iid in text


def test_native_memory_text_omits_anchor_when_no_active_intent_task():
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        text = identity.native_memory_text("worker_cc")
    assert "Boss's verbatim anchor" not in text


def test_anchor_excludes_terminal_tasks():
    """A completed task's intent should not keep cluttering the anchor —
    only non-terminal (进行中 / 需审批) tasks anchor."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("做完就该消失的原话")
        tid = _suspend_free_in_progress("worker_cc", "t", iid)
        tasks.update(tid, status="已完成", _force=True)
        text = identity.native_memory_text("worker_cc")
    assert "Boss's verbatim anchor" not in text


def test_anchor_includes_suspended_task_intent():
    """需审批 is non-terminal — its intent must stay anchored so the agent
    doesn't drift while waiting on the boss."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("挂起期间也要记得的原话")
        tid = _suspend_free_in_progress("worker_cc", "t", iid)
        tasks.pause(tid)
        text = identity.native_memory_text("worker_cc")
    assert "挂起期间也要记得的原话" in text


def test_init_prompt_anchors_active_intent():
    """Double-insurance: the anchor also rides the on-wake init prompt, not
    only the native CLAUDE.md."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("init prompt 也要带的原话")
        _suspend_free_in_progress("worker_cc", "t", iid)
        prompt = identity.init_prompt("worker_cc")
    assert "Boss's verbatim anchor" in prompt
    assert "init prompt 也要带的原话" in prompt
    assert "继续之前未完成的工作" in prompt


def test_anchor_only_for_the_assigned_agent():
    """worker_a's intent must not leak into worker_b's anchor."""
    team = {"agents": {"worker_a": {"cli": "claude-code", "role": "x"},
                       "worker_b": {"cli": "claude-code", "role": "y"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("只属于 a 的原话")
        _suspend_free_in_progress("worker_a", "t", iid)
        text_b = identity.native_memory_text("worker_b")
    assert "只属于 a 的原话" not in text_b


def test_anchor_lists_multiple_distinct_intents():
    """An agent juggling two intent-linked tasks gets both verbatim asks
    anchored — neither should crowd the other out."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        i1 = tasks.create_intent("原话甲：两步结账")
        i2 = tasks.create_intent("原话乙：深色首页")
        _suspend_free_in_progress("worker_cc", "结账", i1)
        _suspend_free_in_progress("worker_cc", "首页", i2)
        text = identity.native_memory_text("worker_cc")
    assert "原话甲：两步结账" in text
    assert "原话乙：深色首页" in text
    assert i1 in text and i2 in text


def test_anchor_groups_tasks_sharing_one_intent():
    """Two tasks back-linking the SAME intent collapse to one anchor line
    that lists both task ids — the verbatim ask appears once, not twice."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("一句原话拆成两个子任务")
        t1 = _suspend_free_in_progress("worker_cc", "子任务一", iid)
        t2 = _suspend_free_in_progress("worker_cc", "子任务二", iid)
        text = identity.native_memory_text("worker_cc")
    # appears exactly once, both task ids grouped on the same line
    assert text.count("一句原话拆成两个子任务") == 1
    assert f"{t1}/{t2}" in text


def test_anchor_never_raises_on_store_error_returns_empty():
    """The anchor feeds the spawn / native-memory path, so a store hiccup
    must degrade to no-section rather than throwing and breaking wake."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}

    def _boom(*a, **k):
        raise RuntimeError("store unavailable")

    with isolated_env(team=team), attr_patch(tasks, list_tasks=_boom):
        # must not raise, and simply omits the anchor section
        text = identity.native_memory_text("worker_cc")
    assert "Boss's verbatim anchor" not in text


def test_write_also_writes_claude_native_memory_file():
    """write() for a claude-code agent drops a ~/.claude/CLAUDE.md in its
    per-agent HOME containing identity + policy + digest. Force the host
    HOME fallback so the file lands inside the test's tmp state dir."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        memory.append("worker_cc", "learning", "auth uses bcrypt")
        identity.write("worker_cc")
        native = Path(paths.agent_home("worker_cc")) / ".claude" / "CLAUDE.md"
        assert native.exists()
        text = native.read_text(encoding="utf-8")
        assert "team worker" in text          # identity body
        assert "Memory maintenance" in text               # remember policy
        assert "auth uses bcrypt" in text     # memory digest


# ── on-disk anchor refresh (anti-/compact staleness) ──────────────


def _native_path(agent: str) -> Path:
    return Path(paths.agent_home(agent)) / ".claude" / "CLAUDE.md"


def test_refresh_native_memory_rewrites_anchor_to_current():
    """The on-disk CLAUDE.md is otherwise only written at provision; this
    refresh re-projects it so the always-loaded anchor tracks the agent's
    *current* active tasks — appearing when a task goes active, and
    disappearing when it completes (the stale-anchor case)."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        # provisioned while idle: native file exists, no anchor
        identity.write("worker_cc")
        assert "Boss's verbatim anchor" not in _native_path("worker_cc").read_text("utf-8")
        # a task goes active → refresh injects the verbatim anchor on disk
        iid = tasks.create_intent("原话：两步结账别加第三步")
        tid = _suspend_free_in_progress("worker_cc", "结账", iid)
        assert identity.refresh_native_memory("worker_cc") is True
        on_disk = _native_path("worker_cc").read_text("utf-8")
        assert "原话：两步结账别加第三步" in on_disk and iid in on_disk
        # task completes → refresh drops the now-stale anchor from disk
        tasks.update(tid, status="已完成", _force=True)
        assert identity.refresh_native_memory("worker_cc") is True
        assert "原话：两步结账别加第三步" not in \
            _native_path("worker_cc").read_text("utf-8")


def test_refresh_native_memory_noop_when_unchanged():
    """No needless disk churn: a second refresh with nothing changed reports
    no write."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("稳定的原话")
        _suspend_free_in_progress("worker_cc", "t", iid)
        assert identity.refresh_native_memory("worker_cc") is True
        assert identity.refresh_native_memory("worker_cc") is False


def test_refresh_native_memory_noop_when_adapter_has_no_native_file():
    """Contract: an adapter that declares no native memory file makes
    refresh a quiet no-op that writes nothing (config, not failure).
    Stubbed because every registered CLI now overrides native_memory_path."""
    from claudeteam.agents import get_adapter
    team = {"agents": {"worker_codex": {"cli": "codex-cli", "model": "gpt-5.5",
                                        "role": "数据"}}}
    with isolated_env(team=team) as tmp, \
            attr_patch(get_adapter("codex-cli"), native_memory_path=lambda a: None):
        iid = tasks.create_intent("原话")
        _suspend_free_in_progress("worker_codex", "t", iid)
        assert identity.refresh_native_memory("worker_codex") is False
        assert list((tmp / "state").rglob("AGENTS.md")) == []


def test_refresh_native_memory_writes_for_codex():
    """Codex now has a native memory file (<CODEX_HOME>/AGENTS.md) → a
    task transition re-projects its anchor just like claude-code."""
    team = {"agents": {"worker_codex": {"cli": "codex-cli", "model": "gpt-5.5",
                                        "role": "数据"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("原话")
        _suspend_free_in_progress("worker_codex", "t", iid)
        assert identity.refresh_native_memory("worker_codex") is True
        path = Path(CodexCliAdapter().native_memory_path("worker_codex"))
        assert path.is_file()
        assert path.name == "AGENTS.md"


def test_anchor_surfaces_pending_question_while_suspended():
    """A suspended task's anchor line carries the pending question — the
    approver context must reach the worker's always-loaded view too."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("做个页面")
        tid = tasks.create("worker_cc", "活", intent_id=iid)
        tasks.update(tid, status="进行中")
        tasks.pause(tid, approval_note="第三行写什么？")
        text = identity._render_intent_anchor("worker_cc")
    assert "Pending question：第三行写什么？" in text


def test_anchor_surfaces_verdict_after_approve_note():
    """REGRESSION: after approve --note, the resumed task's anchor must
    carry the VERDICT — a worker re-injected mid-race must see what was
    decided, not just its own old question."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        iid = tasks.create_intent("做个页面")
        tid = tasks.create("worker_cc", "活", intent_id=iid)
        tasks.update(tid, status="进行中")
        tasks.pause(tid, approval_note="第三行写什么？")
        tasks.approve(tid, note="写鱼香肉丝")
        text = identity._render_intent_anchor("worker_cc")
    assert "Latest verdict：写鱼香肉丝" in text
    assert "第三行写什么" not in text     # verdict replaced the question


def test_write_native_memory_failure_warns_not_silent():
    """An OSError on the native-memory write must not fail the provision,
    but it must WARN — a silent swallow means the anti-drift anchor stops
    tracking with zero traces (disk full / unwritable HOME)."""
    from helpers import captured_stderr
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}

    def boom(path, text):
        raise OSError(28, "No space left on device")

    with isolated_env(team=team):
        with attr_patch(identity, atomic_write_text=boom):
            with captured_stderr() as err:
                identity._write_native_memory("worker_cc")   # must not raise
    msg = err.getvalue()
    assert "worker_cc" in msg and "native memory write failed" in msg
    assert "No space left" in msg


def test_refresh_native_memory_failure_warns_and_returns_false():
    """An unexpected exception inside refresh must keep the no-raise
    contract (the task command that triggered it goes on) but warn that
    the agent's anchor may now be stale."""
    from helpers import captured_stderr
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}

    def boom(path, text):
        raise OSError(13, "Permission denied")

    with isolated_env(team=team):
        iid = tasks.create_intent("会刷不进盘的原话")
        _suspend_free_in_progress("worker_cc", "活", iid)
        with attr_patch(identity, atomic_write_text=boom):
            with captured_stderr() as err:
                assert identity.refresh_native_memory("worker_cc") is False
    msg = err.getvalue()
    assert "worker_cc" in msg and "anchor refresh failed" in msg
    assert "Permission denied" in msg


def test_refresh_native_memory_unknown_agent_stays_quiet():
    """Config gaps (agent not in team.json) are a no-op False, not a
    warning — only real write/render failures should be loud."""
    from helpers import captured_stderr
    with isolated_env(team={"agents": {}}):
        with captured_stderr() as err:
            assert identity.refresh_native_memory("ghost") is False
    assert err.getvalue() == ""


def test_task_cli_transition_refreshes_on_disk_anchor():
    """END-TO-END: driving the real `claudeteam task` CLI must refresh the
    assignee's on-disk CLAUDE.md, so an already-online /compact-ed worker
    never keeps a stale anchor. Dispatch adds the anchor; completion
    removes it — both via the CLI, no manual reidentify."""
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team) as tmp:
        identity.write("worker_cc")               # online worker, idle
        run_cli(["task", "intent", "create", "原话：CLI 触发刷新"])
        run_cli(["task", "create", "worker_cc", "干活", "--intent", "I-1"])
        # going 进行中 through the CLI refreshes the on-disk anchor
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        assert "原话：CLI 触发刷新" in \
            _native_path("worker_cc").read_text("utf-8")
        # completing through the CLI drops the now-stale anchor on disk
        artifact = tmp / "artifacts" / "T-1" / "out.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("evidence", encoding="utf-8")
        run_cli([
            "task", "update", "T-1", "--status", "已完成",
            "--artifact", "artifacts/T-1/out.md",
            "--by", "manager",
        ])
        assert "原话：CLI 触发刷新" not in \
            _native_path("worker_cc").read_text("utf-8")


def test_task_cli_reassign_moves_on_disk_anchor_between_agents():
    """Reassigning an active task refreshes BOTH the old and new owner's
    on-disk anchor — the verbatim ask follows the task to its new owner and
    leaves the previous owner's file."""
    team = {"agents": {
        "worker_a": {"cli": "claude-code", "model": "sonnet", "role": "x"},
        "worker_b": {"cli": "claude-code", "model": "sonnet", "role": "y"}}}
    with isolated_env(team=team):
        identity.write("worker_a")
        identity.write("worker_b")
        run_cli(["task", "intent", "create", "跟着任务走的原话"])
        run_cli(["task", "create", "worker_a", "活", "--intent", "I-1"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        assert "跟着任务走的原话" in _native_path("worker_a").read_text("utf-8")
        run_cli(["task", "update", "T-1", "--assignee", "worker_b"])
        assert "跟着任务走的原话" not in _native_path("worker_a").read_text("utf-8")
        assert "跟着任务走的原话" in _native_path("worker_b").read_text("utf-8")


def test_write_skips_native_memory_for_non_claude_cli():
    """codex/gemini/qwen/kimi have no per-agent native memory file (no
    isolated HOME) → write() must not create a CLAUDE.md for them, but
    must still persist identity.md."""
    team = {"agents": {"worker_codex": {"cli": "codex-cli", "model": "gpt-5.5",
                                        "role": "数据"}}}
    with isolated_env(team=team) as tmp:
        identity.write("worker_codex")
        assert (tmp / "state" / "agents" / "worker_codex" / "identity.md").exists()
        # No native memory file written anywhere under state.
        assert list((tmp / "state").rglob("CLAUDE.md")) == []


# ── workspace + shared experience + skills wiring ────────────────


def test_render_includes_workspace_and_skills_sections():
    """Every agent's identity carries its private workspace path + a pointer
    to the reusable skills index."""
    from claudeteam.runtime import paths
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                     "role": "员工"}}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
        assert "Your private workspace" in text
        assert str(paths.agent_workspace("worker_cc")) in text
        assert "skills/" in text
        assert "SKILL.md" in text


def test_init_prompt_injects_shared_team_experience():
    """Team experience reaches the wake prompt alongside per-agent memory."""
    from claudeteam.store import team_memory
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                     "role": "员工"}}}
    with isolated_env(team=team):
        team_memory.append("测试用 python3 tests/run.py",
                           kind="learning", by="manager", pin=True)
        prompt = identity.init_prompt("worker_cc")
        assert "团队共享经验" in prompt
        assert "测试用 python3 tests/run.py" in prompt


def test_native_memory_text_includes_shared_team_experience():
    """The always-loaded native file (claude's CLAUDE.md) also carries the
    shared experience so it survives /compact."""
    from claudeteam.store import team_memory
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                     "role": "员工"}}}
    with isolated_env(team=team):
        team_memory.append("用两步结账", kind="decision", by="manager", pin=True)
        text = identity.native_memory_text("worker_cc")
        assert "团队共享经验" in text
        assert "用两步结账" in text


# ── playbook mechanism (per-role doc layered into identity) ────────


def test_read_playbook_returns_stripped_content():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "role.md"
        f.write_text("  # Role\n\ndo the thing  ", encoding="utf-8")
        assert identity._read_playbook(str(f)) == "# Role\n\ndo the thing"


def test_read_playbook_tolerates_non_utf8_file():
    # REGRESSION (D): a binary / non-UTF-8 playbook must degrade to "" rather
    # than crash the spawn. _read_playbook caught only OSError before, so a
    # latin-1/binary file raised UnicodeDecodeError straight through hire/up.
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "role.bin"
        f.write_bytes(b"\xff\xfe\x00 not utf-8 \x80\x81")
        assert identity._read_playbook(str(f)) == ""


def test_read_playbook_missing_file_degrades_to_empty():
    with tempfile.TemporaryDirectory() as tmp:
        assert identity._read_playbook(str(Path(tmp) / "nope.md")) == ""
