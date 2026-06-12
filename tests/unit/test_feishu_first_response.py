"""Tests for feishu/first_response.py — real-model first response path."""
from __future__ import annotations

import json

from helpers import env_patch, isolated_env
from claudeteam.feishu.first_response import (
    FirstResponseResult, generate_text, run_once, should_run,
)
from claudeteam.feishu.router import Action, Decision
from claudeteam.store import local_facts


def _boss_decision(**kw):
    data = {
        "action": Action.ROUTE,
        "targets": ["manager"],
        "text": "现在速度还是不行，先给我判断怎么解决",
        "msg_id": "om_first",
        "create_time": "",
    }
    data.update(kw)
    return Decision(**data)


def test_should_run_only_when_enabled_for_boss_to_manager():
    decision = _boss_decision()
    with isolated_env():
        assert should_run(decision, "manager") is False
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENABLED="true"):
        assert should_run(decision, "manager") is True
        assert should_run(decision, "worker_cc") is False
        assert should_run(_boss_decision(sender="worker_cc"), "manager") is False
        assert should_run(Decision(action=Action.DROP), "manager") is False


def test_should_run_skips_stale_catchup_message():
    decision = _boss_decision(create_time="1")
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENABLED="true"):
        assert should_run(decision, "manager") is False


def test_generate_text_uses_anthropic_alias_and_prompt_shape():
    captured = {}

    def fake_http(url, payload, headers, timeout_s):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout_s"] = timeout_s
        return {"content": [{"type": "text", "text": "我先接住这个速度问题，先切独立首响通道，再补链路耗时证据。"}]}

    env = {
        "ANTHROPIC_BASE_URL": "https://api.example.com/v1",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fast-model",
    }
    with isolated_env(), env_patch(
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_PROVIDER="anthropic",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_MODEL="haiku",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_TIMEOUT_S="4"):
        result = generate_text(_boss_decision(), http_json=fake_http, provider_env=env)

    assert result.ok is True
    assert result.model == "fast-model"
    assert result.provider == "anthropic"
    assert captured["url"] == "https://api.example.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["timeout_s"] == 4.0
    assert captured["payload"]["model"] == "fast-model"
    assert "老板原话" in captured["payload"]["messages"][0]["content"]
    assert "不要说收到、排队中" in captured["payload"]["messages"][0]["content"]
    assert "不要写“结论：/证据：/下一步：/需要老板：”这种公文标签" in captured["payload"]["messages"][0]["content"]
    assert result.contract["type"] == "verification"
    assert result.contract["next_step"]


def test_generate_text_can_use_first_response_provider_preset():
    captured = {}

    def fake_http(url, payload, headers, timeout_s):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout_s"] = timeout_s
        return {"choices": [{"message": {"content": json.dumps({
            "text": "我先按 MoneyPrinterTurbo 接入讨论处理，下一条补配置清单和取舍。",
            "response_contract": {
                "type": "research",
                "next_step": "补接入配置清单",
            },
        }, ensure_ascii=False)}}]}

    with isolated_env() as tmp, env_patch(
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_PROVIDER_PRESET="fast-qwen",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENDPOINT="chat_completions",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_MODEL="haiku"):
        state = tmp / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "provider-presets.json").write_text(json.dumps({
            "presets": {
                "fast-qwen": {
                    "ANTHROPIC_BASE_URL": "https://qwen.example.com/v1",
                    "ANTHROPIC_AUTH_TOKEN": "sk-fast",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "qwen-plus",
                },
            },
        }), encoding="utf-8")

        result = generate_text(_boss_decision(), http_json=fake_http)

    assert result.ok is True
    assert result.model == "qwen-plus"
    assert captured["url"] == "https://qwen.example.com/v1/chat/completions"
    assert captured["payload"]["model"] == "qwen-plus"
    assert captured["headers"]["authorization"] == "Bearer sk-fast"
    assert result.contract["type"] == "research"


def test_generate_text_service_override_scrubs_stale_alias_model():
    captured = {}

    def fake_http(url, payload, headers, timeout_s):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout_s"] = timeout_s
        return {"output": [{"content": [{"type": "output_text", "text": json.dumps({
            "text": "我先接住这条催促，下一条补核验后的正式进展。",
            "response_contract": {
                "type": "verification",
                "next_step": "补正式核验进展",
            },
        }, ensure_ascii=False)}]}]}

    team = {
        "agents": {
            "manager": {
                "cli": "codex-cli",
                "model": "gpt-5.5",
                "provider_preset": "deepseek-primary",
            },
        }
    }
    with isolated_env(team=team) as tmp, env_patch(
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_PROVIDER="anthropic",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENDPOINT="responses",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_MODEL="haiku"):
        state = tmp / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "ccswitch.json").write_text(json.dumps({
            "env": {
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "sk-deepseek",
                "ANTHROPIC_MODEL": "deepseek-v4-pro",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-pro",
                "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
                "OPENAI_API_KEY": "sk-deepseek",
                "OPENAI_MODEL": "deepseek-v4-pro",
                "OPENAI_WIRE_API": "chat",
            },
        }), encoding="utf-8")
        (state / "provider-presets.json").write_text(json.dumps({
            "presets": {
                "deepseek-primary": {
                    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                    "ANTHROPIC_AUTH_TOKEN": "sk-deepseek",
                    "ANTHROPIC_MODEL": "deepseek-v4-pro",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-pro",
                },
            },
        }), encoding="utf-8")
        (state / "provider-service.json").write_text(json.dumps({
            "active_service": "zyapi",
            "source_preset": "zyapi-primary",
            "env": {
                "ANTHROPIC_BASE_URL": "https://zyapi.tuluo.top:8888/v1",
                "ANTHROPIC_AUTH_TOKEN": "pk-zyapi",
                "OPENAI_BASE_URL": "https://zyapi.tuluo.top:8888/v1",
                "OPENAI_API_KEY": "pk-zyapi",
                "OPENAI_MODEL": "gpt-5.5",
                "OPENAI_MODEL_PROVIDER": "custom",
                "OPENAI_WIRE_API": "responses",
                "OPENAI_REQUIRES_OPENAI_AUTH": "true",
                "OPENAI_DISABLE_RESPONSE_STORAGE": "true",
            },
        }), encoding="utf-8")

        result = generate_text(_boss_decision(), http_json=fake_http)

    assert result.ok is True
    assert result.model == "gpt-5.5"
    assert captured["url"] == "https://zyapi.tuluo.top:8888/v1/responses"
    assert captured["payload"]["model"] == "gpt-5.5"
    assert captured["headers"]["authorization"] == "Bearer pk-zyapi"


def test_generate_text_includes_recent_manager_context_for_option_reply():
    captured = {}

    def fake_http(_url, payload, _headers, _timeout_s):
        captured["prompt"] = payload["messages"][0]["content"]
        return {"content": [{"type": "text", "text": json.dumps({
            "text": "按刚才方案选择 B，不再等登录，我会继续做推荐流分析。",
            "response_contract": {
                "type": "dispatch",
                "next_step": "按B方案派推荐流分析",
            },
        }, ensure_ascii=False)}]}

    env = {
        "ANTHROPIC_BASE_URL": "https://api.example.com",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fast-model",
    }
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FIRST_RESPONSE_PROVIDER="anthropic"):
        local_facts.append_log(
            "manager",
            "say",
            "现在需要你拍一个方向：A. 登录创作者服务平台继续找笔记灵感；"
            "B. 今天不碰登录，改做 explore/频道推荐流热点机制分析。",
        )
        local_facts.append_message("manager", "user", "B", priority="高")
        result = generate_text(
            _boss_decision(text="B"),
            http_json=fake_http,
            provider_env=env,
        )

    assert result.ok is True
    assert "近期上下文" in captured["prompt"]
    assert "A. 登录创作者服务平台" in captured["prompt"]
    assert "B. 今天不碰登录" in captured["prompt"]
    assert "老板原话：\nB" in captured["prompt"]


def test_generate_text_includes_recent_commit_context_for_dev_compare():
    captured = {}

    def fake_http(_url, payload, _headers, _timeout_s):
        captured["prompt"] = payload["messages"][0]["content"]
        return {"content": [{"type": "text", "text": json.dumps({
            "text": "我按刚提交的 fd9f9203 去对比 dev，先查分支差异和冲突风险。",
            "response_contract": {
                "type": "verification",
                "next_step": "对比dev分支差异",
            },
        }, ensure_ascii=False)}]}

    env = {
        "ANTHROPIC_BASE_URL": "https://api.example.com",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fast-model",
    }
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FIRST_RESPONSE_PROVIDER="anthropic"):
        local_facts.append_log(
            "manager",
            "say",
            "已提交，commit: fd9f9203 fix：app-student 答题结果选项颜色状态修复 + 工具栏高度自适应",
        )
        local_facts.append_message("manager", "user", "对比一下 dev 分支 可以合并过去吗？", priority="高")
        result = generate_text(
            _boss_decision(text="对比一下 dev 分支 可以合并过去吗？"),
            http_json=fake_http,
            provider_env=env,
        )

    assert result.ok is True
    assert "fd9f9203" in captured["prompt"]
    assert "dev 分支" in captured["prompt"]


def test_generate_text_parses_structured_response_contract():
    def fake_http(_url, _payload, _headers, _timeout_s):
        body = {
            "text": "我先按查资料场景处理，先拉刘小排方案和可证伪点，再给你结论。",
            "response_contract": {
                "type": "research",
                "next_step": "补刘小排方案依据和反例验证",
            },
        }
        return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}]}

    env = {
        "ANTHROPIC_BASE_URL": "https://api.example.com",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fast-model",
    }
    decision = _boss_decision(text="去问刘小排，顺便查资料做理论支持")
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FIRST_RESPONSE_PROVIDER="anthropic"):
        result = generate_text(decision, http_json=fake_http, provider_env=env)

    assert result.ok is True
    assert result.text == "我先按查资料场景处理，先拉刘小排方案和可证伪点，再给你结论。"
    assert result.contract == {
        "type": "research",
        "next_step": "补刘小排方案依据和反例验证",
    }


def test_generate_text_can_use_openai_responses_endpoint():
    captured = {}

    def fake_http(url, payload, headers, timeout_s):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        body = {
            "text": "我先用更快的响应端点接住，下一条补链路证据。",
            "response_contract": {
                "type": "verification",
                "next_step": "补首响链路证据",
            },
        }
        return {"output_text": json.dumps(body, ensure_ascii=False)}

    env = {
        "ANTHROPIC_BASE_URL": "https://api.example.com/v1",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fast-model",
    }
    with isolated_env(), env_patch(
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_PROVIDER="anthropic",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENDPOINT="responses"):
        result = generate_text(_boss_decision(), http_json=fake_http, provider_env=env)

    assert result.ok is True
    assert captured["url"] == "https://api.example.com/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert captured["payload"]["model"] == "fast-model"
    assert "老板原话" in captured["payload"]["input"][1]["content"]
    assert result.contract == {
        "type": "verification",
        "next_step": "补首响链路证据",
    }


def test_generate_text_falls_back_when_messages_endpoint_is_not_allowed():
    calls = []

    def fake_http(url, _payload, _headers, _timeout_s):
        calls.append(url)
        if url.endswith("/messages"):
            raise RuntimeError("http 400: 不允许访问 /v1/messages，允许的端点: /v1/responses")
        return {"output_text": json.dumps({
            "text": "我先切到兼容响应端点，继续给你真实首响。",
            "response_contract": {
                "type": "quick_answer",
                "next_step": "给出最短判断",
            },
        }, ensure_ascii=False)}

    env = {
        "ANTHROPIC_BASE_URL": "https://api.example.com",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fast-model",
    }
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENDPOINT="messages"):
        result = generate_text(_boss_decision(), http_json=fake_http, provider_env=env)

    assert result.ok is True
    assert calls == [
        "https://api.example.com/v1/messages",
        "https://api.example.com/v1/responses",
    ]
    assert result.text == "我先切到兼容响应端点，继续给你真实首响。"
    assert result.contract["type"] == "quick_answer"


def test_generate_text_rejects_status_probe_placeholder_without_evidence():
    def fake_http(_url, _payload, _headers, _timeout_s):
        body = {
            "text": (
                "我理解你是在问团队成员是否在线、是否还能继续推进；"
                "我先按当前上下文去核对最近回执和活跃状态，"
                "下一条我会带上能证明成员是否可响应的最新证据。"
            ),
            "response_contract": {
                "type": "verification",
                "next_step": "补团队成员在线证据",
            },
        }
        return {"output_text": json.dumps(body, ensure_ascii=False)}

    env = {
        "ANTHROPIC_BASE_URL": "https://api.example.com/v1",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fast-model",
    }
    decision = _boss_decision(text="团队成员还活着吗")
    with isolated_env(), env_patch(
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_PROVIDER="anthropic",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_ENDPOINT="responses"):
        result = generate_text(decision, http_json=fake_http, provider_env=env)

    assert result.ok is False
    assert "status probe" in result.error


def test_run_once_sends_and_marks_first_response_without_reading_inbox():
    sent = []

    def fake_send(chat_id, text, **kw):
        sent.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_sent"}

    def fake_generate(*_a, **_kw):
        return FirstResponseResult(
            ok=True,
            text="我先按速度瓶颈处理：首响走独立模型通道，后台继续补证据。",
            provider="anthropic",
            model="fast-model",
            elapsed_ms=1200,
            contract={"type": "verification", "next_step": "补链路耗时证据"},
        )

    with isolated_env():
        local_id = local_facts.append_message("manager", "user", "老板消息", priority="高")
        result = run_once(
            _boss_decision(),
            local_id=local_id,
            chat_id="oc_x",
            profile="prod",
            chat_send=fake_send,
            generate_fn=fake_generate,
        )
        row = local_facts.get_message(local_id)
        logs = local_facts.list_logs("manager", limit=5)

    assert result.ok is True
    assert result.send_message_id == "om_sent"
    assert sent[0]["chat_id"] == "oc_x"
    assert sent[0]["profile"] == "prod"
    assert sent[0]["as_user"] is False
    assert row["read"] is False
    assert row["first_response_message_id"] == "om_sent"
    assert row["first_response_elapsed_ms"] == 1200
    assert row["first_response_contract"] == {
        "type": "verification",
        "next_step": "补链路耗时证据",
    }
    assert any(log["type"] == "first_response_sent"
               and "contract=" in log["content"] for log in logs)


def test_run_once_logs_failure_and_keeps_inbox_unmarked():
    def fake_generate(*_a, **_kw):
        return FirstResponseResult(
            ok=False,
            error="timeout",
            provider="anthropic",
            model="fast-model",
            elapsed_ms=6001,
        )

    with isolated_env():
        local_id = local_facts.append_message("manager", "user", "老板消息", priority="高")
        result = run_once(
            _boss_decision(),
            local_id=local_id,
            chat_id="oc_x",
            generate_fn=fake_generate,
        )
        row = local_facts.get_message(local_id)
        logs = local_facts.list_logs("manager", limit=5)

    assert result.ok is False
    assert "first_response_at" not in row
    assert row["read"] is False
    assert any(log["type"] == "first_response_failed"
               and "timeout" in log["content"] for log in logs)


def test_run_once_failure_sends_fast_ack_fallback_and_logs_both_events():
    sent = []

    def fake_generate(*_a, **_kw):
        return FirstResponseResult(
            ok=False,
            error="timeout",
            provider="anthropic",
            model="fast-model",
            elapsed_ms=6001,
        )

    def fake_send(chat_id, text, **kw):
        sent.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_fallback"}

    with isolated_env(), env_patch(
            CLAUDETEAM_ROUTER_FAST_ACK_ENABLED="true",
            CLAUDETEAM_ROUTER_FIRST_RESPONSE_FAILURE_FALLBACK_TEXT=(
                "自动兜底回执：首响模型超时，已转主管队列。")):
        local_id = local_facts.append_message("manager", "user", "老板消息", priority="高")
        result = run_once(
            _boss_decision(),
            local_id=local_id,
            chat_id="oc_x",
            profile="prod",
            chat_send=fake_send,
            generate_fn=fake_generate,
            topic_event={"kind": "switch", "topic": {"name": "TeamOps"}},
        )
        row = local_facts.get_message(local_id)
        logs = local_facts.list_logs("manager", limit=10)

    assert result.ok is False
    assert row["read"] is False
    assert "first_response_at" not in row
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "oc_x"
    assert sent[0]["profile"] == "prod"
    assert sent[0]["as_user"] is False
    assert "自动兜底回执" in sent[0]["text"]
    assert "话题：切换到 #TeamOps" in sent[0]["text"]
    assert any(log["type"] == "first_response_fallback_sent"
               and "om_fallback" in log["content"] for log in logs)
    assert any(log["type"] == "first_response_failed"
               and "timeout" in log["content"] for log in logs)


def test_run_once_failure_fallback_appends_honest_system_disclaimer():
    sent = []

    def fake_generate(*_a, **_kw):
        return FirstResponseResult(
            ok=False,
            error="timeout",
            provider="anthropic",
            model="fast-model",
            elapsed_ms=6001,
        )

    def fake_send(chat_id, text, **kw):
        sent.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_fallback"}

    with isolated_env(), env_patch(
            CLAUDETEAM_ROUTER_FAST_ACK_ENABLED="true",
            CLAUDETEAM_ROUTER_FAST_ACK_TEXT=(
                "收到，已进入云上学习团队主管前台。"
                "我会先做云上最小真实核验；1-3 分钟内给你当前事实/卡点/下一步。")):
        local_id = local_facts.append_message("manager", "user", "团队成员还活着吗", priority="高")
        result = run_once(
            _boss_decision(text="团队成员还活着吗"),
            local_id=local_id,
            chat_id="oc_x",
            chat_send=fake_send,
            generate_fn=fake_generate,
        )

    assert result.ok is False
    assert len(sent) == 1
    assert "系统自动回执" in sent[0]["text"]
    assert "不代表已完成处理或已有事实结论" in sent[0]["text"]


def test_run_once_failure_logs_fallback_send_error_without_marking_inbox():
    def fake_generate(*_a, **_kw):
        return FirstResponseResult(
            ok=False,
            error="timeout",
            provider="anthropic",
            model="fast-model",
            elapsed_ms=6001,
        )

    def fake_send(*_a, **_kw):
        raise RuntimeError("lark down")

    with isolated_env(), env_patch(
            CLAUDETEAM_ROUTER_FAST_ACK_ENABLED="true",
            CLAUDETEAM_ROUTER_FAST_ACK_TEXT="自动入队回执"):
        local_id = local_facts.append_message("manager", "user", "老板消息", priority="高")
        result = run_once(
            _boss_decision(),
            local_id=local_id,
            chat_id="oc_x",
            chat_send=fake_send,
            generate_fn=fake_generate,
        )
        row = local_facts.get_message(local_id)
        logs = local_facts.list_logs("manager", limit=10)

    assert result.ok is False
    assert row["read"] is False
    assert "first_response_at" not in row
    assert any(log["type"] == "first_response_fallback_failed"
               and "lark down" in log["content"] for log in logs)
    assert any(log["type"] == "first_response_failed"
               and "timeout" in log["content"] for log in logs)
