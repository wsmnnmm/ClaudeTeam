"""Tests for feishu/deliver.py — Decision → side-effects."""
from __future__ import annotations

import shlex
from pathlib import Path

from helpers import attr_patch, env_patch, isolated_env, tmux_patch
from claudeteam.agents import identity
from claudeteam.feishu.deliver import (
    apply, _compose_inject_text, _topic_event_for_decision,
    _wants_manager_summary, _wants_realtime_status,
)
from claudeteam.feishu.router import Action, Decision
from claudeteam.store import local_facts, topics


class _FakeAdapter:
    def submit_keys(self):
        return ["Enter"]

    def spawn_cmd(self, agent, model):
        return f"fake-cli {agent} {model}"

    def ready_markers(self):
        return ["fake-ready"]

    def rate_limit_markers(self):
        return []


def _adapter_factory(_agent):
    return _FakeAdapter()


# ── DROP path ─────────────────────────────────────────────────────


def test_drop_decision_is_skipped_with_no_side_effects():
    decision = Decision(action=Action.DROP, reason="dedup")
    inject_calls = []
    write_calls = []
    report = apply(
        decision,
        adapter_for_agent=_adapter_factory,
        tmux_inject=lambda *a, **kw: inject_calls.append((a, kw)) or True,
        append_message=lambda *a, **kw: write_calls.append((a, kw)),
        session="S",
    )
    assert report.skipped is True
    assert inject_calls == []
    assert write_calls == []


# ── ROUTE — happy path ───────────────────────────────────────────


def test_route_writes_inbox_and_injects_for_each_target():
    decision = Decision(
        action=Action.ROUTE,
        targets=["worker_a", "worker_b"],
        sender="manager",
        text="please do X",
        msg_id="om_1",
    )
    inject_calls = []
    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda target, text, submit_keys=None: inject_calls.append((str(target), text, submit_keys)) or True,
            session="S",
        )

    assert report.skipped is False
    assert report.written == ["worker_a", "worker_b"]
    assert report.injected == ["worker_a", "worker_b"]
    assert report.failed_inject == []
    assert {c[0] for c in inject_calls} == {"S:worker_a", "S:worker_b"}
    # default submit_keys come from the adapter
    assert inject_calls[0][2] == ["Enter"]


def test_route_uses_user_as_sender_when_decision_sender_blank():
    """Human messages have sender="" — store should record `from=user`."""
    decision = Decision(action=Action.ROUTE, targets=["manager"], text="hi", msg_id="om_2")
    with isolated_env():
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
        )
        rows = local_facts.list_messages("manager")
        assert len(rows) == 1
        assert rows[0]["from"] == "user"


def test_route_can_write_inbox_without_waking_configured_target():
    """Project frontdesk bridges may consume inbox rows themselves.

    DeepSeaStudyTeam uses this to let the Kimi first responder read
    user->manager inbox rows without waking an incompatible Claude Code pane.
    """
    decision = Decision(action=Action.ROUTE, targets=["manager"], text="hi", msg_id="om_2")
    inject_calls = []
    wake_calls = []
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_SKIP_PANE_INJECT_TARGETS="user:manager"):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: inject_calls.append((a, kw)) or True,
            wake_fn=lambda *a, **kw: wake_calls.append((a, kw)) or True,
            session="S",
        )
        rows = local_facts.list_messages("manager")

    assert len(rows) == 1
    assert rows[0]["from"] == "user"
    assert report.written == ["manager"]
    assert report.injected == []
    assert report.failed_inject == []
    assert report.skipped_inject == ["manager"]
    assert inject_calls == []
    assert wake_calls == []


def test_route_passes_decision_text_into_inbox():
    decision = Decision(action=Action.ROUTE, targets=["worker"], text="hello world", msg_id="om")
    with isolated_env():
        apply(decision, adapter_for_agent=_adapter_factory,
              tmux_inject=lambda *a, **kw: True, session="S")
        rows = local_facts.list_messages("worker")
        assert rows[0]["content"] == "hello world"


def test_route_includes_reply_context_in_inbox_and_inject_text():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="@bot 这个是什么意思",
        msg_id="om_child",
        reply_to="om_parent",
        reply_context="[飞书回复上下文]\n- 父消息摘要: 认知生效验收",
    )
    inject_calls = []
    with isolated_env():
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda target, text, submit_keys=None: inject_calls.append(text) or True,
            session="S",
        )
        rows = local_facts.list_messages("manager")
    assert "认知生效验收" in rows[0]["content"]
    assert "[老板本条新消息]" in rows[0]["content"]
    assert "认知生效验收" in inject_calls[0]
    assert "@bot 这个是什么意思" in inject_calls[0]


def test_route_fast_ack_posts_for_boss_message_to_manager():
    """Optional zero-LLM receipt: boss sees 'queued/working' before a slow
    manager model finishes thinking."""
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="来一个全员配置分析",
        msg_id="om_fast_ack",
    )
    sent = []

    def fake_send(chat_id, text, **kw):
        sent.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_ack"}

    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FAST_ACK_ENABLED="true"):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
            chat_id="oc_x",
            profile="prod",
            chat_send=fake_send,
        )

    assert report.fast_ack is True
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "oc_x"
    assert "主管队列" in sent[0]["text"]
    assert "话题：暂未绑定" in sent[0]["text"]
    assert sent[0]["profile"] == "prod"
    assert sent[0]["as_user"] is False


def test_route_fast_ack_includes_topic_when_marker_switches():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="#TeamOps 检查长期记忆机制",
        msg_id="om_fast_ack_topic",
    )
    sent = []

    def fake_send(chat_id, text, **kw):
        sent.append(text)
        return {"message_id": "om_ack"}

    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FAST_ACK_ENABLED="true"):
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
            chat_id="oc_x",
            chat_send=fake_send,
        )

    assert len(sent) == 1
    assert "话题：切换到 #TeamOps" in sent[0]


def test_route_refreshes_stale_identity_before_injecting_message():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="主管瘦身配置已打开，确认 live identity 也更新",
        msg_id="om_stale_identity",
    )
    team = {"agents": {"manager": {
        "cli": "claude-code",
        "model": "opus",
        "role": "团队主管",
        "identity_profile": "slim",
    }}}
    inject_calls = []
    with isolated_env(team=team):
        path = identity.identity_path("manager")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# old full identity\n主管亲跑 vs 派 worker\n",
                        encoding="utf-8")

        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda target, text, submit_keys=None:
                inject_calls.append((str(target), text, submit_keys)) or True,
            session="S",
        )
        refreshed = path.read_text(encoding="utf-8")

    assert report.injected == ["manager"]
    assert len(inject_calls) == 2
    assert "Manager 瘦身红线" in inject_calls[0][1]
    assert "主管瘦身配置已打开" in inject_calls[1][1]
    assert "Superpowers 工作流内核" in refreshed
    assert "按需读取的 SOP 索引" in refreshed
    assert "主管亲跑 vs 派 worker" not in refreshed


def test_route_first_response_runner_suppresses_static_fast_ack():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="速度还是不行，先真实判断怎么改",
        msg_id="om_first_runner",
    )
    runner_calls = []
    static_ack_sends = []

    def fake_runner(decision, **kw):
        runner_calls.append({"decision": decision, **kw})
        return True

    with isolated_env(), env_patch(
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENABLED="true",
            CLAUDETEAM_ROUTER_FAST_ACK_ENABLED="true"):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
            chat_id="oc_x",
            chat_send=lambda *a, **kw: static_ack_sends.append((a, kw)) or {"message_id": "om_ack"},
            first_response_runner=fake_runner,
        )

    assert report.first_response_started is True
    assert report.fast_ack is False
    assert static_ack_sends == []
    assert len(runner_calls) == 1
    assert runner_calls[0]["local_id"].startswith("msg_")
    assert runner_calls[0]["chat_id"] == "oc_x"
    assert runner_calls[0]["topic_event"] is not None


def test_route_first_response_runner_skips_peer_message_to_manager():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        sender="worker_ops",
        text="配置报告完成",
        msg_id="om_peer_no_runner",
    )
    runner_calls = []
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENABLED="true"):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
            chat_id="oc_x",
            first_response_runner=lambda *a, **kw: runner_calls.append((a, kw)) or True,
        )

    assert report.first_response_started is False
    assert runner_calls == []
    assert report.injected == ["manager"]


def test_boss_message_to_manager_is_high_priority():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="现在查为什么飞书任务这么慢",
        msg_id="om_boss",
    )
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
        )
        rows = local_facts.list_messages("manager")
    assert rows[0]["priority"] == "高"


def test_boss_topic_marker_updates_topic_store_and_injects_capsule():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="#工作Bug 查 T-164 现在卡哪里",
        msg_id="om_topic",
    )
    inject_calls = []
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        topics.set_capsule("工作Bug", "T-164 已降噪暂停；恢复时先查三维定位接口。")
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda target, text, submit_keys=None: inject_calls.append(text) or True,
            session="S",
        )
        assert report.injected == ["manager"]
        assert topics.current_name() == "工作Bug"
        assert "当前话题#工作Bug" in inject_calls[0]
        assert "三维定位接口" in inject_calls[0]


def test_boss_message_without_marker_continues_current_topic():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="继续，只给一个下一步",
        msg_id="om_topic_continue",
    )
    inject_calls = []
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        topics.set_capsule("TeamOps", "用户压力高，禁止刷收到。")
        topics.switch("TeamOps")
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda target, text, submit_keys=None: inject_calls.append(text) or True,
            session="S",
        )
        assert topics.current_name() == "TeamOps"
        assert "当前话题#TeamOps" in inject_calls[0]
        assert "禁止刷收到" in inject_calls[0]


def test_boss_reply_context_overrides_stale_current_topic():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="什么意思？",
        msg_id="om_moneyprinter_followup",
        reply_context=(
            "[飞书回复上下文]\n"
            "父消息摘要：MoneyPrinterTurbo 需要作为外部服务接入，"
            "不是直接改一行 ClaudeTeam 配置。"
        ),
    )
    inject_calls = []
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        topics.set_capsule(
            "TeamOps",
            "T-92/T-100 默会晨训按 08:20 截止收口，不等全员。",
        )
        topics.switch("TeamOps", msg_id="om_old_teamops")
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda target, text, submit_keys=None: inject_calls.append(text) or True,
            session="S",
        )
        row = topics.current()

    assert row["name"] == "TeamOps"
    assert row["last_message_id"] == "om_old_teamops"
    assert "MoneyPrinterTurbo" in inject_calls[0]
    assert "回复上下文优先" in inject_calls[0]
    assert "当前话题#TeamOps" not in inject_calls[0]
    assert "默会晨训" not in inject_calls[0]


def test_boss_message_without_current_topic_injects_topic_triage_hint():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="刚才那个图也发我",
        msg_id="om_topic_missing",
    )
    inject_calls = []
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda target, text, submit_keys=None: inject_calls.append(text) or True,
            session="S",
        )
        assert topics.current_name() == ""
        assert "当前没有已绑定话题" in inject_calls[0]
        assert "归到 #话题名" in inject_calls[0]


def test_boss_message_preempts_busy_manager_before_inject():
    from claudeteam.runtime import tmux, wake

    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="先停一下，回答我当前真实状态",
        msg_id="om_preempt",
    )
    calls = []

    def fake_send_keys(target, *keys, **kw):
        calls.append(("send_keys", str(target), keys))
        return True

    def fake_inject(target, text, submit_keys=None):
        calls.append(("inject", str(target), text, submit_keys))
        return True

    with isolated_env(), \
            attr_patch(wake, is_rate_limited=lambda *a, **kw: False,
                       is_ready=lambda *a, **kw: False), \
            attr_patch(tmux, send_keys=fake_send_keys):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=fake_inject,
            session="S",
        )

    assert report.injected == ["manager"]
    assert calls[0] == ("send_keys", "S:manager", ("C-c",))
    assert calls[1][0] == "inject"


def test_boss_message_first_response_sla_preempts_even_when_ready():
    from claudeteam.runtime import tmux, wake

    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="秒回测试",
        msg_id="om_preempt_sla",
    )
    calls = []

    def fake_send_keys(target, *keys, **kw):
        calls.append(("send_keys", str(target), keys))
        return True

    def fake_inject(target, text, submit_keys=None):
        calls.append(("inject", str(target), text, submit_keys))
        return True

    with isolated_env(), \
            env_patch(CLAUDETEAM_ROUTER_MANAGER_REAL_FIRST_RESPONSE_S="10"), \
            attr_patch(wake, is_rate_limited=lambda *a, **kw: False,
                       is_ready=lambda *a, **kw: True), \
            attr_patch(tmux, send_keys=fake_send_keys):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=fake_inject,
            session="S",
        )

    assert report.injected == ["manager"]
    assert calls[0] == ("send_keys", "S:manager", ("C-c",))
    assert calls[1][0] == "inject"


def test_runner_enabled_disables_legacy_manager_first_response_preempt():
    from claudeteam.runtime import tmux, wake

    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="秒回测试",
        msg_id="om_runner_no_legacy_preempt",
    )
    calls = []

    def fake_send_keys(target, *keys, **kw):
        calls.append(("send_keys", str(target), keys))
        return True

    def fake_inject(target, text, submit_keys=None):
        calls.append(("inject", str(target), text, submit_keys))
        return True

    with isolated_env(), \
            env_patch(CLAUDETEAM_ROUTER_MANAGER_REAL_FIRST_RESPONSE_S="10",
                      CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENABLED="true"), \
            attr_patch(wake, is_rate_limited=lambda *a, **kw: False,
                       is_ready=lambda *a, **kw: True), \
            attr_patch(tmux, send_keys=fake_send_keys):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=fake_inject,
            first_response_runner=lambda *a, **kw: True,
            session="S",
        )

    assert report.first_response_started is True
    assert report.injected == ["manager"]
    assert [call[0] for call in calls] == ["inject"]


def test_route_fast_ack_skips_peer_message_to_manager():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        sender="worker_ops",
        text="配置报告完成",
        msg_id="om_worker_card",
    )
    sent = []
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FAST_ACK_ENABLED="true"):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
            chat_id="oc_x",
            chat_send=lambda *a, **kw: sent.append((a, kw)) or {"message_id": "om_ack"},
        )

    assert report.fast_ack is False
    assert sent == []


def test_route_fast_ack_skips_stale_catchup_message():
    decision = Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text="旧消息不补发收到",
        msg_id="om_old",
        create_time="1",
    )
    sent = []
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FAST_ACK_ENABLED="true"):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
            chat_id="oc_x",
            chat_send=lambda *a, **kw: sent.append((a, kw)) or {"message_id": "om_ack"},
        )

    assert report.fast_ack is False
    assert report.injected == ["manager"]
    assert sent == []


# ── partial failure ──────────────────────────────────────────────


def test_inject_failure_keeps_inbox_write_and_records_failure():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: False,
            session="S",
        )
    assert report.written == ["worker_a"]
    assert report.injected == []
    assert report.failed_inject == ["worker_a"]


def test_inject_exception_caught_and_recorded():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")

    def boom(*a, **kw):
        raise RuntimeError("tmux dead")

    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=boom,
            session="S",
        )
    assert report.written == ["worker_a"]
    assert report.failed_inject == ["worker_a"]


def test_append_message_exception_skips_inject_for_that_agent():
    decision = Decision(action=Action.ROUTE,
                        targets=["worker_a", "worker_b"],
                        text="x", msg_id="om")
    inject_calls = []

    def bad_append(agent, *a, **kw):
        if agent == "worker_a":
            raise IOError("disk full")
        # fall through to real local_facts for worker_b
        return local_facts.append_message(agent, *a, **kw)

    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda t, *a, **kw: inject_calls.append(str(t)) or True,
            append_message=bad_append,
            session="S",
        )
    assert "worker_a" not in report.written
    assert "worker_b" in report.written
    # only worker_b got injected
    assert inject_calls == ["S:worker_b"]


# ── adapter integration ─────────────────────────────────────────


# ── lazy wake integration ──────────────────────────────────────


_WAKE_TEAM = {"agents": {"worker_a": {"cli": "claude-code", "model": "opus"}}}


def test_wake_fn_called_per_target_with_spawn_cmd():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    wake_calls = []

    def fake_wake(target, adapter, *, spawn_cmd, init_msg=None, on_woken=None,
                  timeout_s=None, **_kw):
        wake_calls.append((str(target), spawn_cmd))
        return True

    with isolated_env(team=_WAKE_TEAM):
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            wake_fn=fake_wake,
            session="S",
        )
        wake_script = Path(shlex.split(wake_calls[0][1])[1]).read_text(encoding="utf-8")
    assert len(wake_calls) == 1
    assert wake_calls[0][0] == "S:worker_a"
    assert wake_calls[0][1].startswith("bash ")
    assert "worker_a" in wake_script
    assert "opus" in wake_script


def test_wake_fn_returning_false_skips_inject_and_records_failure():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    inject_calls = []
    with isolated_env(team=_WAKE_TEAM):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: inject_calls.append(a) or True,
            wake_fn=lambda *a, **kw: False,
            session="S",
        )
    assert inject_calls == []
    assert report.injected == []
    assert report.failed_inject == ["worker_a"]


def test_no_wake_fn_skips_wake_step():
    """Backward-compat: deliver without wake_fn does nothing wake-related."""
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
        )
    assert report.injected == ["worker_a"]


# ── rate limit ──────────────────────────────────────────────────


def test_rate_limited_pane_keeps_inbox_skips_inject():
    """When wake.is_rate_limited returns True for an agent, inbox row is
    written but inject is skipped — message preserved for replay."""
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    inject_calls = []

    class RateLimitedAdapter:
        def submit_keys(self):
            return ["Enter"]

        def spawn_cmd(self, agent, model):
            return "fake"

        def ready_markers(self):
            return ["fake-ready"]

        def rate_limit_markers(self):
            return ["Approaching usage limit"]

    # patch tmux.capture_pane to feign a rate-limited pane
    rate_text = "...Approaching usage limit\n"
    with tmux_patch(capture_pane=lambda t, lines=80: rate_text), \
            isolated_env(team=_WAKE_TEAM):
        report = apply(
            decision,
            adapter_for_agent=lambda _: RateLimitedAdapter(),
            tmux_inject=lambda *a, **kw: inject_calls.append(a) or True,
            session="S",
        )
    assert report.written == ["worker_a"]
    assert report.injected == []
    assert report.rate_limited == ["worker_a"]
    assert inject_calls == []


def test_each_agent_uses_its_own_submit_keys():
    """Codex/Kimi vs Claude submit-key sequences differ; verify each."""
    keys_seen = {}

    class _A:
        def __init__(self, keys):
            self._k = keys

        def submit_keys(self):
            return self._k

        def rate_limit_markers(self):
            return []

    def factory(agent):
        return _A(["M-Enter"]) if agent == "codex_w" else _A(["Enter"])

    decision = Decision(action=Action.ROUTE, targets=["codex_w", "claude_w"],
                        text="x", msg_id="om")
    with isolated_env():
        apply(
            decision,
            adapter_for_agent=factory,
            tmux_inject=lambda target, text, submit_keys=None:
                keys_seen.setdefault(str(target), submit_keys) or True,
            session="S",
        )
    assert keys_seen["S:codex_w"] == ["M-Enter"]
    assert keys_seen["S:claude_w"] == ["Enter"]


# ── SLASH dispatch + chat-send failure logging ───────────────────


def test_slash_logs_warning_when_chat_send_returns_none():
    """REGRESSION: when lark-cli timeout / OAuth wall / proxy interference
    makes chat.send_text return None, the slash command silently lost
    its bot reply card. router log should now make this visible."""
    import io
    import contextlib

    decision = Decision(action=Action.SLASH, text="/help",
                        msg_id="om_slash_test", create_time="0")
    # Round-79: /help now returns a card dict; it routes through
    # chat_send_card, not chat_send. Capture both sites so the test still
    # exercises the failure path regardless of which transport the handler
    # picked.
    chat_send_card_calls = []

    def failing_chat_send_card(chat_id, card, **kw):
        chat_send_card_calls.append({"chat_id": chat_id, "card": card, **kw})
        return None  # simulate lark-cli failure

    out = io.StringIO()
    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x"}), \
            contextlib.redirect_stdout(out):
        report = apply(decision,
                       chat_send_card=failing_chat_send_card,
                       team_agents=["manager"],
                       chat_id="oc_x",
                       profile="prod")
    # send_card was called (slash dispatched + tried to post a card)
    assert len(chat_send_card_calls) == 1
    body = chat_send_card_calls[0]["card"]["body"]["elements"][0]["content"]
    assert "/help" in body or "🆘" in body
    # Warning was logged so operator can grep the daemon log
    log = out.getvalue()
    assert "chat reply for om_slash_test failed to post" in log


# ── inject-text composer (R172.b/R173) ───────────────────────────


def _decision(text, *, sender="", reply_context=""):
    return Decision(action=Action.ROUTE, targets=["worker_cc"],
                     sender=sender, text=text, msg_id="om_x", create_time="0",
                     reply_context=reply_context)


def test_compose_inject_text_user_message_says_use_claudeteam_say():
    """Boss / unknown sender → wrapper points at `claudeteam say` (chat
    callback channel). The original text body is preserved verbatim
    after the hint."""
    out = _compose_inject_text("worker_cc", _decision("hello there"))
    assert "bin/ct say worker_cc - --to user" in out
    assert "bin/ct send manager worker_cc" in out
    assert "真实交付/真实 blocker/需要老板动作" in out
    assert "先做最小真实动作" in out
    assert "禁止只说" in out
    assert "hello there" in out
    assert "[群聊·老板]" in out


def test_compose_inject_text_includes_real_time_context_lookup_hint():
    """Every pane nudge should remind agents that relative-time/history
    questions need a quick ledger lookup before answering."""
    out = _compose_inject_text("manager", _decision("你还记得上午聊了什么吗"))
    assert "当前真实时间（本机本地时区）" in out
    assert "今天/上午/刚才/之前/还记得吗" in out
    assert "bin/ct recall manager" in out
    assert "logs/artifacts" in out


def test_compose_inject_text_manager_boss_message_preempts_old_tasks():
    out = _compose_inject_text("manager", _decision("你们能自我进化吗？说说你们的思路"))
    assert "老板消息绝对抢占" in out
    assert "不要先验收旧 worker 回执" in out
    assert "不要先批量 `task done` 清尾巴" in out
    assert "先 `task list --assignee manager --active` 对账当前活跃任务。" not in out


def test_compose_inject_text_manager_real_first_response_when_enabled():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_MANAGER_REAL_FIRST_RESPONSE_S="10"):
        out = _compose_inject_text("manager", _decision("现在下一步怎么做"))
    assert "10 秒内" in out
    assert "第一段前不要运行" in out
    assert "老板可见的自然语言首响" in out
    assert "根据老板语气匹配" in out
    assert "不要把「意图/风险/负责人/证据/下一证据」" in out
    assert "总字数不超过 120 字" in out
    assert "bin/ct say manager - --to user" in out
    assert "first_response_audit" in out
    assert "然后再执行常规核验" in out
    assert "自动 fast_ack" in out
    assert "先做最小真实动作" not in out


def test_compose_inject_text_manager_real_first_response_defers_topic_context():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_MANAGER_REAL_FIRST_RESPONSE_S="10"):
        out = _compose_inject_text(
            "manager",
            _decision("先回答我"),
            topic_context="[话题上下文] 很长的历史胶囊",
        )
    assert "[话题上下文延迟核验]" in out
    assert "很长的历史胶囊" not in out


def test_compose_inject_text_manager_real_first_response_skips_peer_message():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_MANAGER_REAL_FIRST_RESPONSE_S="10"):
        out = _compose_inject_text(
            "manager", _decision("配置报告完成", sender="worker_scout"))
    assert "真实首响门禁" not in out
    assert "bin/ct send worker_scout manager" in out


def test_compose_inject_text_skips_legacy_first_response_when_runner_enabled():
    with isolated_env(), env_patch(
            CLAUDETEAM_ROUTER_MANAGER_REAL_FIRST_RESPONSE_S="10",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENABLED="true"):
        out = _compose_inject_text("manager", _decision("现在下一步怎么做"))
    assert "真实首响门禁" not in out
    assert "先做最小真实动作" in out
    assert "首响行动契约" in out
    assert "fulfillment 日志" in out


def test_compose_inject_text_adds_realtime_status_hint_when_enabled():
    with isolated_env(), \
            env_patch(CLAUDETEAM_CHAT_VISIBLE_QUALITY_GUARD_REQUIRE_REALTIME_STATUS_CARD="true"):
        out = _compose_inject_text("manager", _decision("现在什么情况了"))
    assert "实时状态卡门禁" in out
    assert "scripts/traffic-status.py --out artifacts/traffic/boss-comms/latest-status-card.md" in out
    assert "scripts/traffic-gate.py status-card" in out
    assert "禁止用 task list/recall 的历史总结替代实时看板" in out


def test_compose_inject_text_adds_natural_progress_hint_for_boss_followup():
    out = _compose_inject_text(
        "manager",
        _decision(
            "进展如何",
            reply_context="[飞书回复上下文]\n父消息摘要：T-5 需要截图和预览 URL 做 UI 验收",
        ),
    )
    assert "自然语言进度更新" in out
    assert "谁在做、做到哪、卡在哪、下次什么时候回" in out
    assert "不能宣称已完成/已通过/已验收" in out


def test_compose_inject_text_adds_visual_status_hint_when_enabled():
    with isolated_env(), \
            env_patch(CLAUDETEAM_CHAT_VISIBLE_QUALITY_GUARD_REQUIRE_VISUAL_STATUS_IMAGE="true"):
        out = _compose_inject_text("manager", _decision("现在什么情况了"))
    assert "现场速报门禁" in out
    assert "scripts/traffic-field-report.py" in out
    assert "--image artifacts/traffic/boss-comms/field-report/latest-field-report.png" in out
    assert "禁止无图纯文字回复" in out


def test_compose_inject_text_skips_realtime_status_hint_when_disabled():
    with isolated_env():
        out = _compose_inject_text("manager", _decision("现在什么情况了"))
    assert "实时状态卡门禁" not in out


def test_compose_inject_text_peer_message_uses_send_back_to_sender():
    """Sender is a known agent (peer message) → reply via `claudeteam
    send <sender>` instead of public say."""
    out = _compose_inject_text(
        "worker_cc", _decision("question for you", sender="manager"))
    assert "bin/ct send manager worker_cc" in out
    assert "bin/ct task list --assignee worker_cc" in out
    assert "--artifact <path> --done" in out
    assert "对齐/待命/继续监控" in out
    assert "question for you" in out
    assert "[同事·manager]" in out


def test_compose_inject_text_includes_local_id_for_mark_read():
    """When deliver knows the inbox row's local_id, the wrapper appends
    `claudeteam read <id>` so the agent clears its inbox after replying."""
    out = _compose_inject_text(
        "worker_cc", _decision("ack me"), local_id="msg_42")
    assert "bin/ct read msg_42" in out
    assert "bin/ct task list --assignee worker_cc" in out


def test_compose_inject_text_omits_read_hint_when_local_id_blank():
    """No local_id → no read hint (e.g. for synthetic dispatches that
    didn't go through inbox append)."""
    out = _compose_inject_text("worker_cc", _decision("ad-hoc"))
    assert " read " not in out


def test_compose_inject_text_summary_cue_adds_send_to_manager_hint():
    """R173: when boss message asks for a summary / 汇总 / report,
    non-manager agents get an extra hint to also `claudeteam send
    manager` so manager's inbox pings (manager pane is blind to chat)."""
    out = _compose_inject_text(
        "worker_cc", _decision("数一下文件数量然后让 manager 汇总"))
    assert "bin/ct send manager worker_cc" in out


def test_compose_inject_text_summary_cue_skipped_for_manager_self():
    """Manager doesn't need to send-to-self when boss asks for a
    summary; the hint is non-manager-only."""
    out = _compose_inject_text(
        "manager", _decision("做个汇总报告"))
    # The base "claudeteam say manager" hint stays
    assert "bin/ct say manager - --to user" in out
    # But the extra "send manager" line is suppressed for manager itself
    assert "send manager manager" not in out


def test_compose_inject_text_summary_cue_skipped_without_keyword():
    """Casual boss messages still allow worker direct reply, while also
    reminding them to route non-public updates through manager."""
    out = _compose_inject_text(
        "worker_cc", _decision("just say hi back"))
    assert "bin/ct say worker_cc - --to user" in out
    assert "bin/ct send manager worker_cc" in out


def test_compose_inject_text_keeps_reply_context_next_to_user_message():
    out = _compose_inject_text(
        "manager",
        _decision("这个是什么意思", reply_context="[飞书回复上下文]\n父消息摘要：认知生效验收"),
    )
    assert "父消息摘要：认知生效验收" in out
    assert "[老板本条新消息]" in out
    assert "这个是什么意思" in out


def test_wants_manager_summary_chinese_cues():
    for cue in ("汇总", "汇报", "总结", "报告"):
        assert _wants_manager_summary(f"做个 {cue} 给我"), cue


def test_wants_manager_summary_english_cues():
    for cue in ("summarize", "summary", "report back"):
        assert _wants_manager_summary(f"please {cue} when done"), cue


def test_wants_manager_summary_no_match():
    assert not _wants_manager_summary("hello there")
    assert not _wants_manager_summary("just ack me")


def test_wants_realtime_status_cues():
    assert _wants_realtime_status("现在什么情况了")
    assert _wants_realtime_status("进度怎么样")
    assert _wants_realtime_status("有真的在做吗")
    assert _wants_realtime_status("现在怎么样")
    assert _wants_realtime_status("还没好吗")
    assert not _wants_realtime_status("帮我写一篇文案")


# ── _topic_event_for_decision — quote-reply linking ──────────────


def _boss_decision(text, **kw):
    """Shortcut for a human boss → manager ROUTE decision."""
    return Decision(
        action=Action.ROUTE,
        targets=["manager"],
        text=text,
        msg_id=kw.pop("msg_id", "om_t"),
        **kw,
    )


def test_topic_event_quote_reply_switches_to_parent_topic():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        topics.switch("TeamOps")
        topics.apply_message("#TeamOps T-164 正在排查", msg_id="om_parent_x")

        event, ctx = _topic_event_for_decision(
            _boss_decision("这个 bug 已经修好了", reply_to="om_parent_x"),
            "manager",
        )
        assert event is not None
        assert event["kind"] == "switch"
        assert topics.current_name() == "TeamOps"


def test_topic_event_drift_detection_is_disabled_by_default():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        topics.set_capsule("TeamOps", "T-164 暂停；恢复时先查三维定位接口。")
        topics.switch("TeamOps")

        event, ctx = _topic_event_for_decision(
            _boss_decision("飞书文档权限要改成公开可读并且需要设置下载水印限制"),
            "manager",
        )
        assert event is not None
        assert "自动检测话题漂移" not in ctx
        assert topics.current_name() == "TeamOps"


def test_topic_event_drift_detection_auto_creates_topic_when_enabled():
    with isolated_env(), env_patch(
        CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false",
        CLAUDETEAM_TOPICS_AUTO_DRIFT_ENABLED="true",
    ):
        topics.set_capsule("TeamOps", "T-164 暂停；恢复时先查三维定位接口。")
        topics.switch("TeamOps")

        event, ctx = _topic_event_for_decision(
            _boss_decision("飞书文档权限要改成公开可读并且需要设置下载水印限制"),
            "manager",
        )
        assert event is not None
        assert event["kind"] == "switch"
        assert "自动检测话题漂移" in ctx
        assert topics.current_name() != "TeamOps"


def test_topic_event_short_text_does_not_trigger_drift():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        topics.set_capsule("TeamOps", "用户压力高，禁止刷收到。")
        topics.switch("TeamOps")

        event, ctx = _topic_event_for_decision(
            _boss_decision("继续，只给一个下一步"),
            "manager",
        )
        # Short continuation should stay on current topic
        assert topics.current_name() == "TeamOps"


def test_topic_event_explicit_marker_switches():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        topics.switch("TeamOps")

        event, ctx = _topic_event_for_decision(
            _boss_decision("#部署上线 检查 k8s pod 状态"),
            "manager",
        )
        assert event is not None
        assert event["kind"] == "switch"
        assert topics.current_name() == "部署上线"


def test_topic_event_non_boss_to_manager_returns_none():
    with isolated_env():
        event, ctx = _topic_event_for_decision(
            Decision(
                action=Action.ROUTE,
                targets=["worker_a"],
                text="hello",
                sender="manager",
                msg_id="om_w",
            ),
            "worker_a",
        )
        assert event is None
        assert ctx == ""


def test_topic_event_reply_context_without_marker_skips_current_topic():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_BOSS_PREEMPT_ENABLED="false"):
        topics.set_capsule("TeamOps", "T-92 默会晨训 08:20 收口。")
        topics.switch("TeamOps")

        event, ctx = _topic_event_for_decision(
            _boss_decision(
                "什么意思？",
                reply_context="[飞书回复上下文]\n父消息摘要：MoneyPrinterTurbo 需外部服务接入",
            ),
            "manager",
        )
        # reply_context without #topic overrides topic tracking
        assert event is None
        assert "回复上下文优先" in ctx
