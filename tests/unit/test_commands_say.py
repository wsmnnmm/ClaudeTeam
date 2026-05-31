"""Tests for `claudeteam say` — Feishu chat send + local mirror."""
from __future__ import annotations

import contextlib
import io
import sys

from helpers import attr_patch, env_patch, isolated_env, run_cli
from claudeteam.feishu import chat as feishu_chat
from claudeteam.runtime import manager_action_guard
from claudeteam.store import local_facts


def _isolated(chat_id: str = "oc_test", profile: str = ""):
    # R169: team config now carries role + emoji + color so the card
    # title renders as `{emoji} {agent} · {role}` (mirrors main's
    # `_agent_card_title`). Tests pin the new shape; older fixtures
    # had bare `{}` configs which now fall through to default emoji
    # + role="系统" — covered by a separate test.
    return isolated_env(
        team={"agents": {
            "manager": {"role": "团队主管", "emoji": "🎯", "color": "blue"},
            "worker_cc": {"role": "Claude Code 员工", "emoji": "💎",
                          "color": "purple"},
        }},
        runtime_config={"chat_id": chat_id, "lark_profile": profile},
    )


@contextlib.contextmanager
def _fake_send():
    """Replace feishu_chat.send_card with a recorder.

    R169: `claudeteam say` is card-only; the old text path is dead.
    Tests still keyed on `state['calls']` (legacy text-test ergonomics)
    but the recorder now captures send_card kwargs and synthesises
    a `text` field from the card body so existing assertions on
    `call['text']` keep working without rewrites. Send_text is still
    stubbed (no-op) in case some path accidentally falls back."""
    state = {
        "calls": [],
        "image_calls": [],
        "result": {"message_id": "om_fake"},
        "image_result": {"message_id": "om_fake_image"},
    }

    def fake_card(chat_id, card, *, profile="", as_user=False,
                  lark_run=None):
        # Synthesise the legacy `[<agent>] <body>` text shape from the
        # card title + body so older tests' text-string assertions
        # continue to make sense post-R169.
        title = card.get("header", {}).get("title", {}).get("content", "")
        body = ""
        try:
            body = card["body"]["elements"][0]["content"]
        except (KeyError, IndexError, TypeError):
            pass
        # Synthesised legacy shape: `[<agent>] <body>`. Title format
        # is `{emoji} {agent} · {role}` so we extract the agent slug.
        agent_slug = ""
        for tok in title.split():
            if tok and not tok.startswith(("🎯", "💎", "🟦", "🟧", "🟩",
                                            "🟪", "⚙️")) and tok != "·":
                agent_slug = tok
                break
        synth_text = f"[{agent_slug}] {body}" if agent_slug else body
        state["calls"].append({
            "chat_id": chat_id, "card": card, "text": synth_text,
            "profile": profile, "as_user": as_user, "reply_to": "",
        })
        return state["result"]

    def fake_text(*a, **kw):
        # No-op; should not be called post-R169 but keep for safety.
        return state["result"]

    def fake_image(chat_id, image, *, profile="", as_user=False, lark_run=None):
        state["image_calls"].append({
            "chat_id": chat_id, "image": image, "profile": profile,
            "as_user": as_user,
        })
        return state["image_result"]

    with attr_patch(
            feishu_chat,
            send_card=fake_card,
            send_text=fake_text,
            send_image=fake_image):
        yield state




def test_say_sends_to_chat_and_logs_locally():
    """`--no-card` keeps the old text path so this test pins the
    text-rendering format `[<agent>] <body>`."""
    with _isolated(), _fake_send() as send:
        rc, out, _ = run_cli(["say", "manager", "hello", "world", "--no-card"])
        assert rc == 0
        assert "manager → chat (message_id=om_fake)" in out
        assert send["calls"]
        call = send["calls"][0]
        assert call["chat_id"] == "oc_test"
        assert call["text"] == "[manager] hello world"
        # local mirror written
        logs = local_facts.list_logs("manager")
        assert len(logs) == 1
        assert logs[0]["type"] == "say"
        assert logs[0]["content"] == "hello world"
        assert logs[0]["ref"] == "om_fake"


def test_manager_say_to_user_closes_manager_action_guard():
    with _isolated(), _fake_send() as send:
        boss_msg = local_facts.append_message(
            "manager", "user", "现在主管卡住了吗")
        run_cli(["read", boss_msg])

        rc, out, err = run_cli([
            "say", "manager",
            "没有卡住，已派专岗核验，三分钟内给你图或 blocker。",
            "--to", "user",
        ])
        records = manager_action_guard.list_records()

    assert rc == 0, err
    assert "manager → chat" in out
    assert len(send["calls"]) == 1
    assert records[0]["closure_kind"] == "boss_say"
    assert records[0]["closed_by"] == "manager->user"


def test_manager_say_marks_matching_response_contract_fulfilled():
    with _isolated(), _fake_send() as send:
        boss_msg = local_facts.append_message(
            "manager", "user", "去问刘小排要速度优化方案")
        local_facts.mark_first_response(
            boss_msg,
            response_contract={
                "type": "research",
                "next_step": "补刘小排方案依据和反例验证",
            },
        )

        rc, _, err = run_cli([
            "say", "manager",
            "根据刘小排建议和现有数据，结论是先保留独立首响，再补反例压测。",
            "--to", "user",
        ])
        row = local_facts.get_message(boss_msg)
        logs = local_facts.list_logs("manager", limit=5)

    assert rc == 0, err
    assert "首响承诺" not in send["calls"][0]["text"]
    assert row["first_response_contract_fulfilled_ok"] is True
    assert row["first_response_contract_fulfilled_note"] == "matched"
    assert any(log["type"] == "response_contract_fulfilled" for log in logs)
    assert not any(log["type"] == "response_contract_guarded" for log in logs)


def test_manager_say_prefixes_when_response_contract_is_missing_from_reply():
    with _isolated(), _fake_send() as send:
        boss_msg = local_facts.append_message(
            "manager", "user", "查资料证明一周试运行法是否合理")
        local_facts.mark_first_response(
            boss_msg,
            response_contract={
                "type": "research",
                "next_step": "补资料来源和理论依据",
            },
        )

        rc, _, err = run_cli([
            "say", "manager",
            "结论：可以先小范围试运行，今天先选一条主线。",
            "--to", "user",
        ])
        row = local_facts.get_message(boss_msg)
        logs = local_facts.list_logs("manager", limit=8)

    assert rc == 0, err
    assert "首响承诺" in send["calls"][0]["text"]
    assert "补资料来源和理论依据" in send["calls"][0]["text"]
    assert row["first_response_contract_fulfilled_ok"] is True
    assert row["first_response_contract_fulfilled_note"] == "guard_prefix_added"
    assert any(log["type"] == "response_contract_guarded" for log in logs)


def test_say_dash_reads_message_from_stdin():
    with _isolated(), _fake_send_card() as st, \
            attr_patch(sys, stdin=io.StringIO("line 1\n- line 2\n")):
        rc, out, _ = run_cli(["say", "worker_cc", "-", "--to", "user"])
        logs = local_facts.list_logs("worker_cc")
    assert rc == 0
    assert "worker_cc → chat" in out
    body = st["card_calls"][0]["card"]["body"]["elements"][0]["content"]
    assert body == "line 1\n- line 2"
    assert logs[0]["content"] == "line 1\n- line 2"


def test_say_dash_empty_stdin_returns_one_without_sending():
    with _isolated(), _fake_send_card() as st, \
            attr_patch(sys, stdin=io.StringIO("")):
        rc, _, err = run_cli(["say", "worker_cc", "-", "--to", "user"])
    assert rc == 1
    assert "empty stdin message" in err
    assert st["card_calls"] == []


def test_say_default_identity_is_bot():
    with _isolated(), _fake_send() as send:
        run_cli(["say", "manager", "hi", "--no-card"])
        assert send["calls"][0]["as_user"] is False


def test_say_as_user_flag():
    with _isolated(), _fake_send() as send:
        run_cli(["say", "manager", "hi", "--no-card", "--as", "user"])
        assert send["calls"][0]["as_user"] is True


def test_say_env_var_picks_user_when_no_flag():
    with _isolated(), _fake_send() as send, \
            env_patch(CLAUDETEAM_LARK_SEND_AS="user"):
        run_cli(["say", "manager", "hi", "--no-card"])
        assert send["calls"][0]["as_user"] is True


def test_say_explicit_flag_overrides_env_var():
    with _isolated(), _fake_send() as send, \
            env_patch(CLAUDETEAM_LARK_SEND_AS="user"):
        run_cli(["say", "manager", "hi", "--no-card", "--as", "bot"])
        assert send["calls"][0]["as_user"] is False


def test_say_reply_flag_silently_dropped_post_R169():
    """R169: cards don't thread; --reply is consumed but silently
    dropped. say still succeeds (rc=0) and emits a card; only a
    one-line stderr warning surfaces the dropped threading."""
    with _isolated(), _fake_send() as send:
        rc, _, err = run_cli(["say", "manager", "hi", "--reply", "om_parent"])
        assert rc == 0
        assert len(send["calls"]) == 1
        assert "ignored" in err or "thread" in err


def test_say_no_local_skips_log_write():
    with _isolated(), _fake_send_card():
        run_cli(["say", "manager", "hi", "--no-local"])
        assert local_facts.list_logs("manager") == []


def test_say_returns_one_when_chat_id_unset():
    with _isolated(chat_id=""), _fake_send():
        rc, _, err = run_cli(["say", "manager", "hi", "--no-card"])
        assert rc == 1
        assert "chat_id not set" in err


def test_say_rejects_unknown_sender_agent():
    with _isolated(), _fake_send():
        rc, _, err = run_cli(["say", "--image-caption", "hi"])
        assert rc == 1
        assert "unknown sender agent" in err


def test_say_returns_one_when_lark_returns_none():
    with _isolated(), _fake_send() as send:
        send["result"] = None
        rc, _, err = run_cli(["say", "manager", "hi", "--no-card"])
        assert rc == 1
        assert "Feishu send failed" in err
        logs = local_facts.list_logs("manager")
        assert len(logs) == 1
        assert logs[0]["type"] == "say_failed"
        assert "content=hi" in logs[0]["content"]
        assert not any(r["type"] == "say" for r in logs)
        inbox = local_facts.list_messages("manager", unread_only=True)
        assert len(inbox) == 1
        assert "老板可能没收到" in inbox[0]["content"]


def test_say_threads_profile():
    with _isolated(profile="prod"), _fake_send() as send:
        run_cli(["say", "manager", "hi", "--no-card"])
        assert send["calls"][0]["profile"] == "prod"


def test_say_allows_ui_evidence_chain_answer_without_image():
    message = (
        "如实回答：三个文件证据链打通了，前端知道 html/csv/json 三件套，"
        "但团队没有把哪一批、哪一层、给哪个页面用锁成唯一口径。"
    )
    with _isolated(), _fake_send() as send:
        rc, _, err = run_cli(["say", "manager", message, "--to", "user"])
        assert rc == 0, err
        assert len(send["calls"]) == 1


def test_say_image_only_sends_image_and_logs_locally():
    with _isolated(), _fake_send() as send:
        rc, out, _ = run_cli(["say", "manager", "--image", "artifacts/shot.png"])
        logs = local_facts.list_logs("manager")
    assert rc == 0
    assert "image_id=om_fake_image" in out
    assert send["image_calls"] == [{
        "chat_id": "oc_test",
        "image": "artifacts/shot.png",
        "profile": "",
        "as_user": False,
    }]
    assert send["calls"] == []
    assert len(logs) == 1
    assert logs[0]["content"] == "[image] artifacts/shot.png"


def test_say_image_and_message_send_both_payloads():
    with _isolated(), _fake_send() as send:
        rc, out, _ = run_cli(
            ["say", "manager", "见图", "--image", "artifacts/shot.png"])
        logs = local_facts.list_logs("manager")
    assert rc == 0
    assert "image_id=om_fake_image" in out
    assert "message_id=om_fake" in out
    assert len(send["image_calls"]) == 1
    assert len(send["calls"]) == 1
    assert send["calls"][0]["text"] == "[manager] 见图"
    assert len(logs) == 1
    assert logs[0]["content"] == "见图\n[image] artifacts/shot.png"


def test_say_degrades_image_send_failure_to_progress_update():
    with _isolated(), _fake_send() as send:
        send["image_result"] = None
        rc, _, err = run_cli(["say", "manager", "--image", "artifacts/shot.png"])
        logs = local_facts.list_logs("manager")
    assert rc == 0, err
    assert send["calls"]
    assert "进度更新" in send["calls"][0]["text"]
    assert logs[0]["type"] == "say_failed"
    assert "[image] artifacts/shot.png" in logs[0]["content"]
    assert any(row["type"] == "say_progress_fallback" for row in logs)


def test_say_attach_alias_sends_image():
    with _isolated(), _fake_send() as send:
        rc, out, _ = run_cli(["say", "manager", "见附件", "--attach", "artifacts/shot.png"])
        logs = local_facts.list_logs("manager")
    assert rc == 0
    assert "image_id=om_fake_image" in out
    assert len(send["image_calls"]) == 1
    assert send["image_calls"][0]["image"] == "artifacts/shot.png"
    assert len(send["calls"]) == 1
    assert logs[0]["content"] == "见附件\n[image] artifacts/shot.png"


def test_say_rejects_image_and_attach_together():
    with _isolated():
        rc, _, err = run_cli([
            "say", "manager", "msg",
            "--image", "artifacts/a.png",
            "--attach", "artifacts/b.png",
        ])
    assert rc == 1
    assert "usage: claudeteam say" in err


def test_say_zero_or_one_arg_returns_one():
    rc, _, err = run_cli(["say"])
    assert rc == 1
    assert "usage:" in err
    rc, _, err = run_cli(["say", "manager"])
    assert rc == 1
    assert "usage:" in err


# ── --card flag (round-99) ──────────────────────────────────────


@contextlib.contextmanager
def _fake_send_card():
    """Replace feishu_chat.send_card alongside send_text."""
    state = {
        "text_calls": [],
        "card_calls": [],
        "image_calls": [],
        "result": {"message_id": "om_fake_card"},
        "image_result": {"message_id": "om_fake_image"},
    }

    def fake_text(chat_id, text, **kw):
        state["text_calls"].append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_fake_text"}

    def fake_card(chat_id, card, **kw):
        state["card_calls"].append({"chat_id": chat_id, "card": card, **kw})
        return state["result"]

    def fake_image(chat_id, image, **kw):
        state["image_calls"].append({"chat_id": chat_id, "image": image, **kw})
        return state["image_result"]

    with attr_patch(
            feishu_chat,
            send_text=fake_text,
            send_card=fake_card,
            send_image=fake_image):
        yield state


def test_say_card_flag_sends_card_not_text():
    """`--card` routes through send_card; send_text isn't touched.
    R169: title now `{emoji} {agent} · {role}` (no more bare `[agent]`)."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", "重要决策已落地", "--card"])
    assert rc == 0
    assert len(st["card_calls"]) == 1
    assert st["text_calls"] == []
    card = st["card_calls"][0]["card"]
    # R169: title is "{emoji} {agent} · {role}" pulled from team.json
    assert card["header"]["title"]["content"] == "🎯 manager · 团队主管"
    body = card["body"]["elements"][0]["content"]
    assert "重要决策已落地" in body
    # team.json `color: blue` → blue template
    assert card["header"]["template"] == "blue"


def test_say_card_failure_falls_back_to_plain_text():
    """If Feishu rejects interactive cards for a chat/profile, the boss
    should still get the content as plain text instead of a fake local
    failure loop."""
    with _isolated(), _fake_send_card() as st:
        st["result"] = None
        rc, out, err = run_cli(["say", "manager", "重要决策已落地"])
        logs = local_facts.list_logs("manager")
        inbox = local_facts.list_messages("manager", unread_only=True)
    assert rc == 0, err
    assert "fallback posted" in out
    assert len(st["card_calls"]) == 1
    assert st["text_calls"] == [{
        "chat_id": "oc_test",
        "text": "重要决策已落地",
        "profile": "",
        "as_user": False,
    }]
    assert logs[0]["type"] == "say"
    assert logs[0]["content"] == "重要决策已落地"
    assert inbox == []


def test_say_card_for_worker_uses_team_json_color_after_R169():
    """team.json's per-agent `color` field wins over the hard-coded
    worker_*→green default. Test fixture sets worker_cc → purple,
    matches main's worker_cc shade."""
    with _isolated(), _fake_send_card() as st:
        run_cli(["say", "worker_cc", "step 1 done", "--card"])
    card = st["card_calls"][0]["card"]
    assert card["header"]["template"] == "purple"
    assert card["header"]["title"]["content"] == "💎 worker_cc · Claude Code 员工"


def test_say_card_escapes_angle_bracket_placeholders_in_body():
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "worker_cc", "填 <server> / <public> / <price_id>"])
    assert rc == 0
    body = st["card_calls"][0]["card"]["body"]["elements"][0]["content"]
    assert "&lt;server&gt;" in body
    assert "&lt;public&gt;" in body
    assert "&lt;price_id&gt;" in body


def test_say_normalizes_literal_newlines_and_tabs_in_body():
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", r"汇总：\n- 第一项\n- 第二项\t已完成"])
    assert rc == 0
    body = st["card_calls"][0]["card"]["body"]["elements"][0]["content"]
    assert body == "汇总：\n- 第一项\n- 第二项\t已完成"


def test_say_blocks_empty_public_numbered_items():
    bad = "要确认的 UI 只有两页：\n1.\n2.\n先按这两页确认。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
        rows = local_facts.list_logs("manager")
    assert rc == 1
    assert "empty list item" in err
    assert st["card_calls"] == []
    assert rows[0]["type"] == "say_blocked"
    assert rows[0]["ref"] == "chat.publish.visible_quality_guard"


def test_say_blocks_empty_public_screenshot_lists():
    bad = "当前可对照的实现产物在：\n里面附了三张截图：、、。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
    assert rc == 1
    assert "empty image/screenshot list" in err
    assert st["card_calls"] == []


def test_say_blocks_public_image_filename_without_attachment():
    bad = "对应标注图：T-156-detail-marked.png 和 T-156-sync-study-marked.png。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
    assert rc == 1
    assert "does not attach it" in err
    assert st["card_calls"] == []


def test_say_blocks_public_path_only_delivery():
    bad = "已完成，产物在：`artifacts/T-34/q2-blind-guess-test-pack.md`，请查看。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
        rows = local_facts.list_logs("manager")
    assert rc == 1
    assert "only gives a local artifact path" in err
    assert st["card_calls"] == []
    assert rows[0]["type"] == "say_blocked"
    assert rows[0]["ref"] == "chat.publish.visible_quality_guard"


def test_say_blocks_public_cli_flag_only_delivery():
    bad = "- --task-id T-3"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
        rows = local_facts.list_logs("manager")
    assert rc == 1
    assert "only contains CLI flags" in err
    assert st["card_calls"] == []
    assert rows[0]["type"] == "say_blocked"
    assert rows[0]["content"] == bad
    assert rows[0]["ref"] == "chat.publish.visible_quality_guard"


def test_say_can_block_public_internal_execution_jargon_when_enabled():
    bad = (
        "归到 #traffic-ops：现在没有未完成的 manager 活跃任务。\n"
        "已完成：T-2 风险绿；T-3 三棒门禁全通过。"
    )
    with _isolated(), _fake_send_card() as st, \
            env_patch(CLAUDETEAM_CHAT_VISIBLE_QUALITY_GUARD_REJECT_INTERNAL_TOKENS="true"):
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
        rows = local_facts.list_logs("manager")
    assert rc == 1
    assert "internal execution jargon" in err
    assert st["card_calls"] == []
    assert rows[0]["type"] == "say_blocked"
    assert rows[0]["ref"] == "chat.publish.visible_quality_guard"


def test_say_can_require_realtime_status_card_shape_when_enabled():
    bad = "现在没有未完成任务，今晚两条主线已收口；下一步等你拍板。"
    with _isolated(), _fake_send_card() as st, \
            env_patch(CLAUDETEAM_CHAT_VISIBLE_QUALITY_GUARD_REQUIRE_REALTIME_STATUS_CARD="true"):
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
    assert rc == 1
    assert "realtime workflow card shape" in err
    assert st["card_calls"] == []


def test_say_can_require_visual_status_image_when_enabled():
    bad = "现在没有未完成任务，今晚两条主线已收口；下一步等你拍板。"
    with _isolated(), _fake_send_card() as st, \
            env_patch(CLAUDETEAM_CHAT_VISIBLE_QUALITY_GUARD_REQUIRE_VISUAL_STATUS_IMAGE="true"):
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
    assert rc == 1
    assert "visual field-report image" in err
    assert st["card_calls"] == []


def test_say_allows_visual_status_with_image_when_required():
    good = "流量运营现场速报：稿子已准备好，图里有内容预览和 A/B 决策。需要你选 A 或 B。"
    with _isolated(), _fake_send_card() as st, \
            env_patch(CLAUDETEAM_CHAT_VISIBLE_QUALITY_GUARD_REQUIRE_VISUAL_STATUS_IMAGE="true"):
        rc, _, err = run_cli(["say", "manager", good, "--to", "user", "--image", "artifacts/report.png"])
    assert rc == 0, err
    assert len(st["card_calls"]) == 1


def test_say_allows_realtime_status_card_when_required():
    good = (
        "流量团队实时状态（22:40）\n"
        "当前活跃任务：无。\n"
        "最近流水线：\n"
        "- 22:30 主管：已检查任务。\n"
        "最新待确认产物：小红书内容草稿，风险绿。\n"
        "需要你：选 A 或 B。\n"
        "系统健康：心跳陈旧，不能包装成持续生产中。"
    )
    with _isolated(), _fake_send_card() as st, \
            env_patch(CLAUDETEAM_CHAT_VISIBLE_QUALITY_GUARD_REQUIRE_REALTIME_STATUS_CARD="true"):
        rc, _, err = run_cli(["say", "manager", good, "--to", "user"])
    assert rc == 0, err
    assert len(st["card_calls"]) == 1


def test_say_blocks_stdin_marker_when_send_flags_leak_into_message():
    with _isolated(), _fake_send_card() as st, \
            attr_patch(sys, stdin=io.StringIO("老板看不到这段\n")):
        rc, _, err = run_cli([
            "say", "manager", "-", "--to", "user", "--task-id", "T-3"])
        rows = local_facts.list_logs("manager")
    assert rc == 1
    assert "only contains CLI flags" in err
    assert st["card_calls"] == []
    assert rows[0]["content"] == "- --task-id T-3"


def test_say_allows_public_delivery_summary_with_audit_path():
    msg = (
        "【任务完成】Q2 盲猜测试包\n"
        "状态：待验收\n"
        "核心产出：已整理 Q2 题面、选择项和反馈采集口径，够发起一轮小样本盲测。\n"
        "下一步：请老板确认是否今天发给 5 个测试用户。\n"
        "审计路径：artifacts/T-34/q2-blind-guess-test-pack.md"
    )
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", msg, "--to", "user"])
    assert rc == 0, err
    assert len(st["card_calls"]) == 1


def test_say_allows_public_audit_path_when_clickable_link_exists():
    msg = (
        "Q2 盲猜测试包已转成飞书文档，老板可直接打开验收。\n"
        "链接：https://example.com/doc/q2\n"
        "审计路径：artifacts/T-34/q2-blind-guess-test-pack.md"
    )
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", msg, "--to", "user"])
    assert rc == 0, err
    assert len(st["card_calls"]) == 1


def test_say_does_not_apply_path_only_gate_to_internal_messages():
    bad = "已完成，产物在：artifacts/T-34/q2-blind-guess-test-pack.md，请查看。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "worker_cc", bad, "--to", "manager"])
    assert rc == 0, err
    assert len(st["card_calls"]) == 1


def test_say_allows_public_image_filename_when_image_is_attached():
    msg = "任务详情页标注如下，重点看左侧 tab 和右侧信息行。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli([
            "say", "manager", msg,
            "--image", "state/agents/worker_visual/artifacts/T-156-detail-marked.png",
            "--to", "user",
        ])
    assert rc == 0, err
    assert st["image_calls"]
    assert st["card_calls"]


def test_say_degrades_public_ui_ok_claim_without_image_to_progress_update():
    bad = "T-165 UI验收通过，可以给老板确认。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
        rows = local_facts.list_logs("manager")
        inbox = local_facts.list_messages("manager", unread_only=True)
    assert rc == 0, err
    assert len(st["card_calls"]) == 1
    body = st["card_calls"][0]["card"]["body"]["elements"][0]["content"]
    assert "进度更新" in body
    assert "正式结论" in body
    assert rows[0]["type"] == "say_progress_fallback"
    assert "原回复" in inbox[0]["content"]


def test_say_allows_project_scope_answer_with_page_and_delivery_words():
    msg = (
        "这类情况不能假装“能做完”，正确应对是立刻改交付方式，不改事实。\n"
        "半天时间只能保证半天产物：先把需求问清楚、锁定唯一主线、"
        "补出第一轮 SPEC 和派工边界。\n"
        "宁可只做一个页面/一个主流程/一版样板，不同时铺多页，"
        "不带着未确认的接口和设计细节开工。\n"
        "一句话：时间不够时，不是把两天活压成半天乱做，"
        "而是把半天变成老板能拍板、团队能继续推进的最小确定性交付。"
    )
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", msg, "--to", "user"])
        rows = local_facts.list_logs("manager")
    assert rc == 0, err
    assert len(st["card_calls"]) == 1
    body = st["card_calls"][0]["card"]["body"]["elements"][0]["content"]
    assert "不能假装" in body
    assert rows[0]["type"] == "say"


def test_say_allows_public_ui_blocker_without_image():
    msg = "T-165 UI 不能确认：当前三张截图都是白屏，属于截图链路 blocker。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", msg, "--to", "user"])
    assert rc == 0, err
    assert len(st["card_calls"]) == 1


def test_say_allows_public_ui_ok_claim_with_image():
    msg = (
        "T-165 UI 大致没问题，可以进入设计复核。\n"
        "预览：http://localhost:5173/#/task/detail?visualPreview=1"
    )
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli([
            "say", "manager", msg,
            "--image", "state/agents/worker_frontend/artifacts/T-165/detail.png",
            "--to", "user",
        ])
    assert rc == 0, err
    assert st["image_calls"]
    assert st["card_calls"]


def test_say_degrades_public_ui_ok_claim_with_image_but_no_preview_url():
    msg = "T-165 UI验收通过，可以进入设计复核。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli([
            "say", "manager", msg,
            "--image", "state/agents/worker_frontend/artifacts/T-165/detail.png",
            "--to", "user",
        ])
    assert rc == 0, err
    assert len(st["card_calls"]) == 1
    body = st["card_calls"][0]["card"]["body"]["elements"][0]["content"]
    assert "进度更新" in body
    assert "预览" in body or "截图" in body
    assert st["image_calls"] == []


def test_say_blocks_progress_update_that_claims_final_acceptance():
    bad = "进度更新：T-165 UI 已验收，预计 5 分钟后补截图。负责人：manager。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
    assert rc == 1
    assert "progress update must not claim" in err
    assert st["card_calls"] == []


def test_say_blocks_completion_claim_without_verification_evidence():
    bad = "任务已完成，可以验收。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", bad, "--to", "user"])
    assert rc == 1
    assert "verification evidence" in err
    assert st["card_calls"] == []


def test_say_allows_completion_claim_with_verification_evidence():
    good = "任务已完成，可以验收。证据：测试通过，预览：https://example.com/demo。"
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", good, "--to", "user"])
    assert rc == 0, err
    assert len(st["card_calls"]) == 1


def test_say_normalizes_literal_newlines_after_urls():
    """Team reports often pass one CLI argument containing URLs followed by
    visible ``\n`` list breaks. URL text should survive, but the line
    breaks must render as real Feishu card paragraphs."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli([
            "say", "manager",
            (
                r"这次补上链接版：\n\n"
                r"1) 课程第17章：https://scys.com/deepsea/2001/course/164?chapterId=10838\n"
                r"2) Cloudflare Turnstile：https://dash.cloudflare.com/?to=/:account/turnstile\n"
                r"3) QiaChat 当前入口：http://119.91.213.166\n\n"
                r"结论：推荐先小范围试用。"
            ),
        ])
    assert rc == 0
    body = st["card_calls"][0]["card"]["body"]["elements"][0]["content"]
    assert r"\n" not in body
    assert "https://scys.com/deepsea/2001/course/164?chapterId=10838\n2)" in body
    assert "http://119.91.213.166\n\n结论" in body


def test_say_does_not_overdecode_path_like_backslashes():
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "worker_cc", r"路径 C:\new\test 和 /tmp/\n/cache 保持原样"])
    assert rc == 0
    body = st["card_calls"][0]["card"]["body"]["elements"][0]["content"]
    assert body == r"路径 C:\new\test 和 /tmp/\n/cache 保持原样"


def test_say_card_color_reflects_live_toml_edit():
    """REGRESSION: editing a worker's `card_color` in claudeteam.toml
    should change the very next `say` card without a router restart.
    config.agent_config goes through the lenient JSON / mtime-cached
    TOML path, so a live edit takes effect on the next call."""
    from claudeteam.runtime import paths, tunables as _tun
    with _isolated() as tmp, _fake_send_card() as st:
        # First call: default fixture has worker_cc card_color=purple
        rc, _, _ = run_cli(["say", "worker_cc", "first"])
        assert rc == 0
        first_color = st["card_calls"][0]["card"]["header"]["template"]
        assert first_color == "purple"

        # Operator edits claudeteam.toml — flip worker_cc to red
        cf = paths.config_file()
        cf.write_text(
            '[team]\nsession = "ClaudeTeam"\n\n'
            '[team.agents.manager]\ncli = "claude-code"\nrole = "主管"\n\n'
            '[team.agents.worker_cc]\ncli = "claude-code"\nrole = "Claude Code 员工"\n'
            'card_color = "red"\n',
            encoding='utf-8')
        _tun.reset_cache()

        rc, _, _ = run_cli(["say", "worker_cc", "second"])
        assert rc == 0
        second_color = st["card_calls"][1]["card"]["header"]["template"]
        assert second_color == "red", \
            f"card_color edit didn't take effect: still {second_color}"


def test_say_with_reply_warns_and_sends_card_anyway():
    """Cards don't thread; `--reply` prints a stderr warning but
    still sends the card. R169: the warn message is generic
    "--reply ignored (Feishu cards don't thread)" since there's
    no longer a --card vs --no-card distinction."""
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", "msg",
                              "--reply", "om_xx"])
    assert rc == 0
    assert "ignored" in err and "thread" in err
    assert len(st["card_calls"]) == 1


def test_say_default_now_sends_card_after_R168():
    """R168: default flipped — every `claudeteam say` now sends a v2
    card (colored header per role), not plain text. Boss-flagged
    convention for the test_a deploy: agent messages must look like
    structured updates in chat, not raw text. Plain text path opts
    in via the new `--no-card` flag (test below).

    R169: title format updated to `{emoji} {agent} · {role}`."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", "plain text msg"])
    assert rc == 0
    assert len(st["card_calls"]) == 1
    assert st["text_calls"] == []
    card = st["card_calls"][0]["card"]
    assert card["header"]["title"]["content"] == "🎯 manager · 团队主管"
    body = card["body"]["elements"][0]["content"]
    assert "plain text msg" in body


def test_say_card_falls_back_to_default_emoji_when_team_json_missing_emoji():
    """team.json may not specify `emoji` — fall back to the per-agent
    default emoji table; missing-from-table agents get the system ⚙️
    glyph rather than crashing or rendering an empty space."""
    bare = isolated_env(
        team={"agents": {"manager": {"role": "管理"},
                          "worker_unknown": {"role": "未知员工"}}},
        runtime_config={"chat_id": "oc_test", "lark_profile": ""},
    )
    with bare, _fake_send_card() as st:
        run_cli(["say", "manager", "x"])
        # manager has a default-table emoji
        assert st["card_calls"][0]["card"]["header"]["title"]["content"] == \
            "🎯 manager · 管理"
        st["card_calls"].clear()
        run_cli(["say", "worker_unknown", "x"])
        # not in default table → ⚙️ system glyph
        assert st["card_calls"][0]["card"]["header"]["title"]["content"] == \
            "⚙️ worker_unknown · 未知员工"


def test_say_no_card_flag_is_a_no_op_post_R169():
    """R169: `--no-card` removed as escape hatch — every chat message
    is a card. Flag is consumed for backwards-compat but does not
    change behaviour. Boss-flagged convention: no plain-text agent
    chat in test_a deploy."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", "收到", "--no-card"])
    assert rc == 0
    # All sends now go through send_card path; send_text is dead
    assert len(st["card_calls"]) == 1
    assert st["text_calls"] == []
    title = st["card_calls"][0]["card"]["header"]["title"]["content"]
    assert title == "🎯 manager · 团队主管"


def test_say_audit_log_failure_does_not_block_chat_send():
    """REGRESSION: audit log write is best-effort. Disk full / permission
    denied / corrupt logs.jsonl should NOT prevent the chat send — the
    boss is waiting for the message to land in the group, audit row
    is secondary."""
    def boom(*a, **kw):
        raise OSError("[Errno 28] No space left on device")

    with _isolated(), _fake_send() as send, \
            attr_patch(local_facts, append_log=boom):
        rc, _, err = run_cli(["say", "manager", "important message", "--no-card"])
    # Chat send still succeeded despite audit failing
    assert rc == 0
    # The Feishu chat got the message
    assert len(send["calls"]) == 1
    # Stderr surfaced the audit-log warning so operator knows
    assert "audit log write failed" in err


# ── Step 3: --to + chat.publish 过滤 ───────────────────────────


def _toml_with_publish(tmp_path, **kv):
    """Drop a claudeteam.toml with [chat.publish] = kv into tmp_path."""
    from claudeteam.runtime import tunables
    lines = ["[chat.publish]"]
    for k, v in kv.items():
        if v == "always":
            lines.append(f'{k} = "always"')
        elif v is True:
            lines.append(f"{k} = true")
        elif v is False:
            lines.append(f"{k} = false")
    (tmp_path / "claudeteam.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    tunables.reset_cache()


def test_say_default_to_is_user_when_unset():
    """No --to flag → default 'user' (老板)。chat.publish.manager_to_user
    默认 True → send_card 被调。"""
    with _isolated() as tmp, _fake_send() as send:
        rc, _, _ = run_cli(["say", "manager", "hi 老板"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_silenced_when_publish_false():
    """publish[manager_to_worker]=false → say --to worker_cc 不发卡，
    只写 say_silenced 审计。"""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_worker=False)
        rc, out, _ = run_cli(["say", "manager", "派单消息", "--to", "worker_cc"])
        assert rc == 0
        assert len(send["calls"]) == 0
        assert "silenced" in out
        # Audit log 仍然写（必须在 isolated_env 内查，state_dir 才是 tmp）
        rows = local_facts.list_logs("manager")
        assert len(rows) == 1
        assert rows[0]["type"] == "say_silenced"
        assert rows[0]["content"] == "派单消息"


def test_say_passes_through_when_publish_true():
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_worker=True)
        rc, _, _ = run_cli(["say", "manager", "派单", "--to", "worker_cc"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_passes_through_when_publish_always():
    """`always` is a hint, treated as True at runtime."""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_user="always")
        rc, _, _ = run_cli(["say", "manager", "答老板", "--to", "user"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_worker_to_user_default_true():
    """worker → user (worker 完工卡) — 默认 True (preserve current behavior)."""
    with _isolated() as tmp, _fake_send() as send:
        rc, _, _ = run_cli(["say", "worker_cc", "完工 ✅", "--to", "user"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_worker_internal_alignment_ack_is_silenced():
    """Worker loop acknowledgements should not become boss-visible cards."""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, worker_to_user=True)
        msg = "T-161 对齐保持：继续只监控三类触发条件，其他场景不重复回报；T-156 保持主线。"
        rc, out, _ = run_cli(["say", "worker_cc", msg, "--to", "user"])
        rows = local_facts.list_logs("worker_cc")
    assert rc == 0
    assert len(send["calls"]) == 0
    assert "internal alignment" in out
    assert rows[0]["type"] == "say_silenced"
    assert rows[0]["ref"] == "chat.publish.internal_worker_ack"


def test_say_worker_real_blocker_can_report_directly():
    """The anti-spam guard is narrow: real boss-action blockers still pass."""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, worker_to_user=True)
        msg = "真实 blocker：需要老板授权登录后我才能继续复现。"
        rc, _, _ = run_cli(["say", "worker_cc", msg, "--to", "user"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_publish_live_edit_takes_effect_without_restart():
    """REGRESSION: editing claudeteam.toml [chat.publish] should
    affect the very next `say` call without needing to restart any
    daemon. Boss requirement: a config file is meant to live-edit.

    Verifies the tunables mtime-cache invalidation actually works
    end-to-end through commands/say.py."""
    with _isolated() as tmp, _fake_send() as send:
        # Round 1: worker_to_user = true → say goes through
        _toml_with_publish(tmp, worker_to_user=True)
        rc, _, _ = run_cli(["say", "worker_cc", "完工 1", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 1

        # Operator edits toml live → flip to false
        _toml_with_publish(tmp, worker_to_user=False)
        rc, out, _ = run_cli(["say", "worker_cc", "完工 2", "--to", "user"])
        assert rc == 0
        # Next call must see the new value: silenced, no chat send
        assert len(send["calls"]) == 1, "publish=false didn't take effect"
        assert "silenced" in out

        # Flip back to true → goes through again
        _toml_with_publish(tmp, worker_to_user=True)
        rc, _, _ = run_cli(["say", "worker_cc", "完工 3", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 2


def test_say_worker_to_manager_silenced_when_false():
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, worker_to_manager=False)
        rc, out, _ = run_cli(["say", "worker_cc", "进度更新", "--to", "manager"])
    assert rc == 0
    assert len(send["calls"]) == 0
    assert "silenced" in out


def test_say_unknown_to_falls_back_to_user_role():
    """`--to foobar` → receiver_role='user' fallback (safest default)."""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_user="always")
        rc, _, _ = run_cli(["say", "manager", "msg", "--to", "foobar"])
    assert rc == 0
    assert len(send["calls"]) == 1   # user_to_user → default True


def test_say_to_arg_value_required():
    """`--to` without a value should usage-error."""
    with _isolated():
        rc, _, err = run_cli(["say", "manager", "msg", "--to"])
    assert rc == 1
    assert "usage: claudeteam say" in err


# ── Step 4a: publish_overrides 单 agent 覆盖 ───────────────────


def _isolated_with_overrides(agent: str, overrides: dict, **other_agent_cfg):
    """Build an isolated_env where the named agent has publish_overrides."""
    full_cfg = {"role": "测试", "emoji": "💎", "color": "green",
                "publish_overrides": overrides, **other_agent_cfg}
    return isolated_env(
        team={"agents": {
            "manager": {"role": "团队主管", "emoji": "🎯", "color": "blue"},
            agent: full_cfg,
        }},
        runtime_config={"chat_id": "oc_test", "lark_profile": ""},
    )


def test_say_overrides_force_silence_when_global_default_true():
    """Even if chat.publish is unset (default True), agent override
    can still silence its own channel."""
    with _isolated_with_overrides("worker_cc", {"worker_to_user": False}) as tmp, \
            _fake_send() as send:
        rc, out, _ = run_cli(["say", "worker_cc", "完工", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 0
        assert "silenced" in out


def test_say_overrides_force_pass_when_global_silenced():
    """Override can also force-allow a channel that's globally silenced."""
    with _isolated_with_overrides("worker_cc", {"worker_to_manager": True}) as tmp, \
            _fake_send() as send:
        _toml_with_publish(tmp, worker_to_manager=False)
        rc, _, _ = run_cli(["say", "worker_cc", "进度", "--to", "manager"])
        assert rc == 0
        assert len(send["calls"]) == 1


def test_say_overrides_take_precedence_over_global():
    """When global says false but override says true, override wins."""
    with _isolated_with_overrides("worker_cc", {"worker_to_user": True}) as tmp, \
            _fake_send() as send:
        _toml_with_publish(tmp, worker_to_user=False)
        rc, _, _ = run_cli(["say", "worker_cc", "完工", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 1


def test_say_overrides_always_treated_as_true():
    with _isolated_with_overrides("worker_cc", {"worker_to_user": "always"}) as tmp, \
            _fake_send() as send:
        _toml_with_publish(tmp, worker_to_user=False)
        rc, _, _ = run_cli(["say", "worker_cc", "完工", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 1


def test_say_overrides_other_agents_unaffected():
    """worker_cc has override forcing silence; worker_codex w/o override
    follows global rule."""
    other = {"role": "数据", "emoji": "🟦", "color": "purple"}
    with isolated_env(team={"agents": {
        "manager": {"role": "主管"},
        "worker_cc": {"role": "策划", "publish_overrides": {"worker_to_user": False}},
        "worker_codex": other,
    }}, runtime_config={"chat_id": "oc_test", "lark_profile": ""}) as tmp, \
            _fake_send() as send:
        # worker_cc → 静默
        rc1, _, _ = run_cli(["say", "worker_cc", "完工 cc", "--to", "user"])
        assert len(send["calls"]) == 0
        # worker_codex → 通过（默认 True）
        rc2, _, _ = run_cli(["say", "worker_codex", "完工 codex", "--to", "user"])
        assert len(send["calls"]) == 1
        assert rc1 == 0 and rc2 == 0


def test_say_no_override_falls_through_to_global():
    """Agent without publish_overrides → global rule applies."""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_worker=False)
        rc, out, _ = run_cli(["say", "manager", "派单", "--to", "worker_cc"])
        assert rc == 0
        assert len(send["calls"]) == 0
        assert "silenced" in out
