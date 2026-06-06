"""Tests for `claudeteam watchdog` daemon's alert wiring.

Mostly covers `_make_alert_fn` — the rest of the daemon (signal handler,
supervise loop, pidlock acquire) is exercised by the existing
test_runtime_watchdog.py.
"""
from __future__ import annotations

from helpers import attr_patch, isolated_env
from claudeteam.commands import watchdog as cmd_watchdog
from claudeteam.feishu import chat as feishu_chat
from claudeteam.runtime.manager_watch import OverdueNotice


def test_make_alert_fn_returns_none_when_chat_id_unset():
    """No chat target → no alert fn → supervise gets None default
    (which it tolerates; cooldowns happen but no chat delivery)."""
    with isolated_env():
        assert cmd_watchdog._make_alert_fn() is None


def test_make_alert_fn_sends_red_card_on_cooldown():
    """Round-98: cooldown alert is a red Feishu card (visually distinct
    from /team /health cards) instead of plain text."""
    cards_sent = []

    def fake_send_card(chat_id, card, **kw):
        cards_sent.append({"chat_id": chat_id, "card": card, **kw})
        return {"message_id": "om_alert"}

    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x",
                                       "lark_profile": "p"}), \
            attr_patch(feishu_chat, send_card=fake_send_card):
        alert = cmd_watchdog._make_alert_fn()
        assert alert is not None
        alert("router", 3, 600)

    assert len(cards_sent) == 1
    sent = cards_sent[0]
    assert sent["chat_id"] == "oc_x"
    assert sent["profile"] == "p"
    assert sent["as_user"] is False
    card = sent["card"]
    assert card["header"]["template"] == "red"
    title = card["header"]["title"]["content"]
    assert "router" in title and "cooldown" in title
    body = card["body"]["elements"][0]["content"]
    assert "router" in body
    assert "600s" in body
    assert "3" in body
    assert "claudeteam health" in body


def test_make_alert_fn_falls_back_to_text_when_card_send_fails():
    """A broken card path mustn't lose the alert — fall back to send_text
    so the operator at least sees something in chat."""
    text_sent = []

    def card_boom(chat_id, card, **kw):
        raise RuntimeError("card schema rejected by Feishu")

    def fake_send_text(chat_id, text, **kw):
        text_sent.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_fallback"}

    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x"}), \
            attr_patch(feishu_chat, send_card=card_boom,
                       send_text=fake_send_text):
        alert = cmd_watchdog._make_alert_fn()
        alert("router", 5, 300)

    assert len(text_sent) == 1
    assert "router" in text_sent[0]["text"]
    assert "300s" in text_sent[0]["text"]


def test_make_alert_fn_uses_lark_profile_from_runtime_config():
    """Profile must thread through send_card so the right bot identity
    sends the alert (not whichever profile happens to be the default)."""
    captured = []

    def fake_send_card(chat_id, card, **kw):
        captured.append(kw.get("profile"))
        return {"message_id": "om_x"}

    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x",
                                       "lark_profile": "team_alpha"}), \
            attr_patch(feishu_chat, send_card=fake_send_card):
        alert = cmd_watchdog._make_alert_fn()
        alert("router", 1, 60)

    assert captured == ["team_alpha"]


def test_make_manager_watch_alert_fn_returns_none_by_default():
    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x",
                                       "lark_profile": "p"}):
        assert cmd_watchdog._make_manager_watch_alert_fn() is None


def test_make_manager_watch_alert_fn_sends_orange_card_when_public_enabled():
    cards_sent = []

    def fake_send_card(chat_id, card, **kw):
        cards_sent.append({"chat_id": chat_id, "card": card, **kw})
        return {"message_id": "om_watch"}

    notice = OverdueNotice(
        task_id="T-9",
        assignee="worker_scout",
        title="⏱ T-9 派工超时：worker_scout",
        body="T-9 overdue body",
        public_title="需要主管确认：T-9 长时间未收口",
        public_body="系统发现一项主管派工需要核验。\n主管下一步：给出人话结论。",
    )
    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x",
                                       "lark_profile": "p"}) as tmp, \
            attr_patch(feishu_chat, send_card=fake_send_card):
        (tmp / "claudeteam.toml").write_text(
            "[manager_watch]\npublic_chat_alert = true\n",
            encoding="utf-8",
        )
        alert = cmd_watchdog._make_manager_watch_alert_fn()
        assert alert is not None
        alert(notice)

    sent = cards_sent[0]
    assert sent["chat_id"] == "oc_x"
    assert sent["profile"] == "p"
    assert sent["as_user"] is False
    assert sent["card"]["header"]["template"] == "orange"
    assert "T-9" in sent["card"]["header"]["title"]["content"]
    content = sent["card"]["body"]["elements"][0]["content"]
    assert "主管下一步" in content
    assert "overdue body" not in content


def test_make_manager_watch_alert_fn_skips_private_notice():
    cards_sent = []

    def fake_send_card(chat_id, card, **kw):
        cards_sent.append(card)
        return {"message_id": "om_watch"}

    notice = OverdueNotice(
        task_id="T-9",
        assignee="worker_scout",
        title="⏱ T-9 派工超时：worker_scout",
        body="T-9 overdue body",
    )
    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x",
                                       "lark_profile": "p"}) as tmp, \
            attr_patch(feishu_chat, send_card=fake_send_card):
        (tmp / "claudeteam.toml").write_text(
            "[manager_watch]\npublic_chat_alert = true\n",
            encoding="utf-8",
        )
        alert = cmd_watchdog._make_manager_watch_alert_fn()
        assert alert is not None
        alert(notice)

    assert cards_sent == []


def test_make_manager_watch_alert_fn_respects_chat_alert_false():
    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x"}) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "[manager_watch]\npublic_chat_alert = false\n",
            encoding="utf-8",
        )
        assert cmd_watchdog._make_manager_watch_alert_fn() is None


def test_run_manager_watch_sweeps_tasks_and_boss_inbox():
    calls = []

    with isolated_env(team={"agents": {"manager": {}}}), \
            attr_patch(
                cmd_watchdog.manager_watch,
                sweep=lambda alert_fn=None: calls.append("tasks"),
                sweep_first_output=lambda alert_fn=None: calls.append("first_output"),
                sweep_boss_inbox=lambda alert_fn=None: calls.append("boss_inbox"),
                sweep_manager_actions=lambda alert_fn=None: calls.append("manager_actions"),
            ):
        cmd_watchdog._run_manager_watch(alert_fn=lambda notice: None)

    assert calls == ["tasks", "first_output", "boss_inbox", "manager_actions"]


def test_run_cockpit_sync_is_disabled_by_default():
    calls = []
    with isolated_env(team={"agents": {"manager": {}}}):
        rc = cmd_watchdog._run_cockpit_sync(
            lambda args: calls.append(list(args)) or 0)

    assert rc is None
    assert calls == []


def test_run_cockpit_sync_threads_config_into_cli_args():
    calls = []
    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x",
                                      "lark_profile": "owner-profile"}) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "\n".join([
                "[cockpit_sync]",
                "enabled = true",
                'root = "/Users/wsm/Project"',
                "interval_s = 90",
                'base_token = "base_123"',
                'table_id = "tbl_456"',
                'task_table_id = "tbl_tasks"',
                'remote_state_dir = "/tmp/remote-teams"',
                'profile = "sync-profile"',
            ]) + "\n",
            encoding="utf-8",
        )
        rc = cmd_watchdog._run_cockpit_sync(
            lambda args: calls.append(list(args)) or 0)

    assert rc == 0
    assert calls == [[
        "--write",
        "--root", "/Users/wsm/Project",
        "--base-token", "base_123",
        "--table-id", "tbl_456",
        "--task-table-id", "tbl_tasks",
        "--remote-state-dir", "/tmp/remote-teams",
        "--profile", "sync-profile",
    ]]


def test_run_topic_digest_is_disabled_by_default():
    calls = []
    with isolated_env(team={"agents": {"manager": {}}}):
        path = cmd_watchdog._run_topic_digest(
            lambda target, **kw: calls.append((target, kw)) or target)

    assert path is None
    assert calls == []


def test_run_topic_digest_writes_to_config_relative_dir():
    calls = []
    with isolated_env(team={"agents": {"manager": {}}}) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "\n".join([
                "[topic_digest]",
                "enabled = true",
                'out_dir = "reports/topic-digests"',
                "include_closed = true",
            ]) + "\n",
            encoding="utf-8",
        )
        path = cmd_watchdog._run_topic_digest(
            lambda target, **kw: calls.append((target, kw)) or (target / "x.md"))

    assert path == tmp / "reports" / "topic-digests" / "x.md"
    assert calls == [(
        tmp / "reports" / "topic-digests",
        {"include_closed": True},
    )]


def test_run_provider_failover_is_disabled_by_default():
    calls = []
    with isolated_env(team={"agents": {"manager": {}}}):
        result = cmd_watchdog._run_provider_failover(
            lambda: calls.append("called") or {"action": "x"})

    assert result is None
    assert calls == []


def test_run_provider_failover_threads_enabled_config_to_sweep():
    calls = []
    with isolated_env(team={"agents": {"manager": {}}}) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "\n".join([
                "[provider_failover]",
                "enabled = true",
                'backup_preset = "zyapi-backup"',
            ]) + "\n",
            encoding="utf-8",
        )
        result = cmd_watchdog._run_provider_failover(
            lambda: calls.append("called") or {"action": "promoted_backup"})

    assert result == {"action": "promoted_backup"}
    assert calls == ["called"]
