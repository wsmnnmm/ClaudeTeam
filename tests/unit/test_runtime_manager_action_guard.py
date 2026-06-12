"""Tests for runtime/manager_action_guard.py."""
from __future__ import annotations

import json

from helpers import isolated_env
from claudeteam.runtime import manager_action_guard, paths
from claudeteam.store import local_facts
from claudeteam.util import write_json


def _team():
    return {
        "session": "S",
        "agents": {
            "manager": {"cli": "codex-cli"},
            "worker_design": {"cli": "codex-cli"},
            "worker_visual": {"cli": "codex-cli"},
            "worker_ops": {"cli": "codex-cli"},
            "worker_frontend": {"cli": "codex-cli"},
        },
    }


def _age_record(local_id: str, read_at: int = 0) -> None:
    path = paths.state_dir() / "manager-action-guard.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data["records"]:
        if row["local_id"] == local_id:
            row["created_at"] = 0
            row["read_at"] = read_at
            break
    write_json(path, data)


def test_record_boss_read_tracks_visual_owner():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "继续打磨，把第 4 张图重做得更正式")
        assert local_facts.mark_read(local_id)
        record = manager_action_guard.record_boss_read(
            local_facts.get_message(local_id))

    assert record is not None
    assert record["local_id"] == local_id
    assert record["route_hint"] == "visual"
    assert record["expected_owner"] == "worker_visual"
    assert record["closed_at"] is None


def test_classify_design_owner_separately_from_image_tasks():
    with isolated_env(team=_team()):
        route, owner = manager_action_guard.classify_content(
            "设计稿无法 MCP，先把页面像素还原规范说清楚")

    assert route == "design"
    assert owner == "worker_design"


def test_mark_delegate_closes_latest_open_record():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "第 4 张图重做得更正式")
        local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))

        closed = manager_action_guard.mark_delegate(
            "worker_visual", "请重做第 4 张图", task_id="T-4", ref="msg_worker")
        rows = manager_action_guard.list_records()

    assert closed is not None
    assert closed["closure_kind"] == "delegate"
    assert closed["closed_by"] == "manager->worker_visual"
    assert rows[0]["closed_at"]
    assert rows[0]["closure_ref"] == "msg_worker"


def test_mark_delegate_prefers_matching_owner_over_latest_open_record():
    with isolated_env(team=_team()):
        visual_id = local_facts.append_message(
            "manager", "user", "第 4 张图重做得更正式")
        local_facts.mark_read(visual_id)
        manager_action_guard.record_boss_read(local_facts.get_message(visual_id))

        code_id = local_facts.append_message(
            "manager", "user", "接口报错先修一下")
        local_facts.mark_read(code_id)
        manager_action_guard.record_boss_read(local_facts.get_message(code_id))

        closed = manager_action_guard.mark_delegate(
            "worker_visual", "请重做第 4 张图", ref="msg_worker")
        rows = {
            row["local_id"]: row
            for row in manager_action_guard.list_records()
        }

    assert closed is not None
    assert closed["local_id"] == visual_id
    assert rows[visual_id]["closed_at"]
    assert rows[code_id]["closed_at"] is None


def test_mark_boss_say_closes_open_record():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "现在卡住了吗")
        local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))

        manager_action_guard.mark_boss_say(
            "已派视觉同学重做，三分钟内给图或 blocker。", ref="om_x")
        rows = manager_action_guard.list_records()

    assert rows[0]["closure_kind"] == "boss_say"
    assert rows[0]["closed_by"] == "manager->user"
    assert rows[0]["closure_ref"] == "om_x"


def test_mark_boss_say_does_not_close_self_execution_reply():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "第 4 张图重做得更正式")
        local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))

        closed = manager_action_guard.mark_boss_say(
            "我已亲自用 sharp 把主图裁了一版，正在继续部署。", ref="om_x")
        rows = manager_action_guard.list_records()

    assert closed is None
    assert rows[0]["closed_at"] is None
    assert rows[0]["closure_kind"] == ""


def test_self_execution_detection_uses_two_tier_markers():
    """High-confidence markers fire alone; low-confidence single-verb
    markers need a co-cue (tool name) to count, otherwise we'd false-
    positive on benign phrasings like '我跑了下 git log'."""
    high = (
        "我已亲自用 sharp 把主图裁了一版。",
        "我亲自部署到生产。",
        "我图省事没走浏览器截图。",
    )
    low_with_cue = (
        "我跑了部署脚本，刚上线。",
        "我重启了服务。",
        "我裁切了原图，已经导出。",
    )
    benign = (
        "我跑了一下 git log 看看最近改动。",
        "我查了后台配置，没改。",
        "我看后台日志了。",
        "我跑测试通过。",
    )
    for msg in high:
        assert manager_action_guard._looks_like_manager_self_execution(msg), msg
    for msg in low_with_cue:
        assert manager_action_guard._looks_like_manager_self_execution(msg), msg
    for msg in benign:
        assert not manager_action_guard._looks_like_manager_self_execution(msg), msg


def test_observe_public_manager_reply_closes_open_record():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "网络不好要及时反馈")
        local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))

        manager_action_guard.observe_public_manager_reply(
            "收到，这条我已经按当前执行门禁处理了。", ref="om_manager_card")
        rows = manager_action_guard.list_records()

    assert rows[0]["closure_kind"] == "boss_say"
    assert rows[0]["closed_by"] == "manager->user"
    assert rows[0]["closure_ref"] == "om_manager_card"


def test_sweep_does_not_compensate_self_execution_boss_reply():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "第 4 张图重做得更正式")
        local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))
        local_facts.append_log(
            "manager", "say",
            "我已亲自用 sharp 把主图裁了一版，正在继续部署。",
            ref="om_self_exec",
        )
        _age_record(local_id, read_at=0)

        notices = manager_action_guard.sweep(
            now_ms_fn=lambda: 181_000,
            overdue_s=180,
            repeat_s=300,
            public_overdue_s=180,
        )
        rows = manager_action_guard.list_records()

    assert len(notices) == 1
    assert rows[0]["closed_at"] is None
    assert rows[0]["closure_kind"] == ""


def test_sweep_alerts_for_read_without_action_and_suppresses_duplicates():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "打开小红书后台看草稿截图")
        local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))
        _age_record(local_id, read_at=0)

        first = manager_action_guard.sweep(
            now_ms_fn=lambda: 181_000,
            overdue_s=180,
            repeat_s=300,
            public_overdue_s=180,
        )
        second = manager_action_guard.sweep(
            now_ms_fn=lambda: 240_000,
            overdue_s=180,
            repeat_s=300,
            public_overdue_s=180,
        )

    assert len(first) == 1
    assert first[0].local_id == local_id
    assert first[0].route_hint == "browser"
    assert first[0].expected_owner == "worker_ops"
    assert "老板消息已读但未闭环" in first[0].body
    assert "没有记录到 manager 回群、派工或真实 blocker" in first[0].body
    assert first[0].public_title
    assert second == []


def test_sweep_compensates_action_that_happened_before_read():
    with isolated_env(team=_team()):
        local_id = local_facts.append_message(
            "manager", "user", "第 4 张图重做得更正式")
        delegated_id = local_facts.append_message(
            "worker_visual", "manager", "请重做第 4 张图",
            task_id="T-4")
        local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))
        _age_record(local_id, read_at=0)

        notices = manager_action_guard.sweep(
            now_ms_fn=lambda: 181_000,
            overdue_s=180,
            repeat_s=300,
            public_overdue_s=180,
        )
        rows = manager_action_guard.list_records()

    assert notices == []
    assert rows[0]["closed_at"]
    assert rows[0]["closure_kind"] == "delegate_compensated"
    assert rows[0]["closure_ref"] == delegated_id


def test_sweep_public_alert_waits_for_manager_grace():
    with isolated_env(team=_team()) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "\n".join([
                "[manager_action_guard]",
                "public_after_manager_alert_s = 300",
            ]) + "\n",
            encoding="utf-8",
        )
        local_id = local_facts.append_message(
            "manager", "user", "打开小红书后台看草稿截图")
        local_facts.mark_read(local_id)
        manager_action_guard.record_boss_read(local_facts.get_message(local_id))
        _age_record(local_id, read_at=0)

        first = manager_action_guard.sweep(
            now_ms_fn=lambda: 181_000,
            overdue_s=180,
            repeat_s=300,
            public_overdue_s=180,
        )
        second = manager_action_guard.sweep(
            now_ms_fn=lambda: 482_000,
            overdue_s=180,
            repeat_s=300,
            public_overdue_s=180,
        )

    assert len(first) == 1
    assert first[0].public_body == ""
    assert len(second) == 1
    assert second[0].public_body


def test_disabled_guard_does_not_record_or_sweep():
    with isolated_env(team=_team()) as tmp:
        (tmp / "claudeteam.toml").write_text(
            "[manager_action_guard]\nenabled = false\n",
            encoding="utf-8",
        )
        local_id = local_facts.append_message("manager", "user", "ping")
        local_facts.mark_read(local_id)
        record = manager_action_guard.record_boss_read(
            local_facts.get_message(local_id))
        notices = manager_action_guard.sweep(now_ms_fn=lambda: 999_000)

    assert record is None
    assert notices == []
