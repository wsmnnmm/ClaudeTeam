"""Tests for Feishu Base edit intake."""
from __future__ import annotations

from pathlib import Path

from helpers import FakeProc, isolated_env
from claudeteam.feishu import base_intake
from claudeteam.store import tasks


def _base_config(root: Path) -> str:
    return "\n".join([
        "[base_intake]",
        "enabled = true",
        f'root = "{root}"',
        'base_token = "base_x"',
        'task_table_id = "tbl_tasks"',
        'cockpit_table_id = "tbl_cockpit"',
        'event_types = ["drive.file.bitable_record_changed_v1"]',
        'trigger_statuses = ["待下发", "老板已决策"]',
        'action_fields = ["老板操作"]',
        "writeback = true",
        'writeback_field = "下发回执"',
        "clear_action_after_dispatch = true",
        "",
    ])


def _team(root: Path, name: str = "product-lab") -> Path:
    team = root / name
    team.mkdir()
    (team / "claudeteam.toml").write_text(
        "\n".join([
            'chat_id = ""',
            'lark_profile = "p"',
            "[team]",
            'session = "S"',
            "[team.agents.manager]",
            'cli = "codex-cli"',
            'role = "主管"',
            "",
        ]),
        encoding="utf-8",
    )
    return team


def test_cockpit_intent_requires_explicit_boss_decision():
    fields = {
        "负责人团队": "Product Lab 本地",
        "负责人agent": "manager",
        "当前动作": "智能伙伴接入判断",
        "老板分组": ["现在要你决定"],
        "老板下一步": "是否接入？",
    }
    assert base_intake.build_intent(
        base_intake.cockpit_sync.DEFAULT_TABLE_ID, "rec1", fields) is None

    fields["老板决策"] = "接入，先做审计任务"
    intent = base_intake.build_intent(
        base_intake.cockpit_sync.DEFAULT_TABLE_ID, "rec1", fields)
    assert intent is not None
    assert intent.team_label == "Product Lab 本地"
    assert "接入，先做审计任务" in intent.body


def test_task_flow_status_can_trigger_dispatch_without_decision_field():
    fields = {
        "负责人团队": "Product Lab 本地",
        "负责人agent": "manager",
        "任务标题": "建立决策入口",
        "当前状态": "待下发",
        "下一步动作": "从 Base 创建任务",
    }
    intent = base_intake.build_intent(base_intake.DEFAULT_TASK_TABLE_ID, "rec2", fields)
    assert intent is not None
    assert intent.title == "建立决策入口"
    assert "从 Base 创建任务" in intent.body


def test_task_status_intent_maps_boss_state_to_local_task_status():
    fields = {
        "任务卡ID": "Product Lab 本地/T-7",
        "所属战场": "Product Lab 本地",
        "状态": "验收通过",
        "真实产物链接": "artifacts/T-7/report.md",
    }
    intent = base_intake.build_task_status_intent(
        base_intake.DEFAULT_TASK_TABLE_ID, "rec_status", fields)

    assert intent is not None
    assert intent.team_label == "Product Lab 本地"
    assert intent.task_id == "T-7"
    assert intent.status == "已完成"
    assert intent.artifact == "artifacts/T-7/report.md"


def test_task_status_continue_dispatches_from_state_field():
    fields = {
        "负责人团队": "Product Lab 本地",
        "负责人agent": "worker_ops",
        "任务标题": "继续旧任务",
        "状态": "继续执行",
        "下一步动作": "补一条最新回执",
    }
    intent = base_intake.build_intent(base_intake.DEFAULT_TASK_TABLE_ID, "rec_continue", fields)

    assert intent is not None
    assert intent.agent == "worker_ops"
    assert intent.title == "继续执行：Product Lab 本地"
    assert "继续执行当前任务" in intent.body


def test_task_status_intent_accepts_boss_operation_field():
    fields = {
        "任务卡ID": "Product Lab 本地/T-8",
        "所属战场": "Product Lab 本地",
        "状态": "待验收",
        "老板操作": "取消任务",
    }
    intent = base_intake.build_task_status_intent(
        base_intake.DEFAULT_TASK_TABLE_ID, "rec_status_action", fields)

    assert intent is not None
    assert intent.task_id == "T-8"
    assert intent.status == "已取消"
    assert intent.action_field == "老板操作"


def test_cockpit_action_can_reactivate_team():
    fields = {
        "战场": "工作分身",
        "负责人团队": "工作分身",
        "负责人agent": "manager",
        "当前状态": "待核验",
        "当前动作": "本机状态过期",
        "老板操作": "重新核验",
        "老板下一步": "催对应 manager 做 live health + 任务回执。",
    }
    intent = base_intake.build_intent(
        base_intake.cockpit_sync.DEFAULT_TABLE_ID, "rec3", fields)

    assert intent is not None
    assert intent.action_field == "老板操作"
    assert intent.title == "重新核验：工作分身"
    assert "让 manager 先唤醒/确认 pane ready" in intent.body
    assert "写回驾驶舱" in intent.body


def test_boss_action_alias_routes_to_manager():
    fields = {
        "负责人团队": "Product Lab 本地",
        "负责人agent": "worker_ops",
        "当前状态": "待核验",
        "老板操作": "重新校验",
    }
    intent = base_intake.build_intent(
        base_intake.cockpit_sync.DEFAULT_TABLE_ID, "rec_alias", fields)

    assert intent is not None
    assert intent.agent == "manager"
    assert intent.title == "重新核验：Product Lab 本地"
    assert "让 manager" in intent.body


def test_employee_wake_routes_to_employee():
    fields = {
        "负责人团队": "Product Lab 本地",
        "负责人agent": "worker_ops",
        "当前状态": "空闲",
        "老板操作": "唤醒员工",
    }
    intent = base_intake.build_intent(
        base_intake.cockpit_sync.DEFAULT_TABLE_ID, "rec_wake", fields)

    assert intent is not None
    assert intent.agent == "worker_ops"
    assert intent.title == "唤醒员工：Product Lab 本地"
    assert "唤醒 worker_ops" in intent.body


def test_reassign_routes_to_manager():
    fields = {
        "负责人团队": "Product Lab 本地",
        "负责人agent": "worker_builder",
        "当前状态": "忙到起飞",
        "老板操作": "重新分派",
    }
    intent = base_intake.build_intent(
        base_intake.cockpit_sync.DEFAULT_TABLE_ID, "rec_reassign", fields)

    assert intent is not None
    assert intent.agent == "manager"
    assert "重新分派" in intent.body


def test_handle_payload_fetches_dispatches_once_and_writebacks():
    lark_calls = []
    dispatched = []
    payload = {
        "header": {
            "event_id": "evt_1",
            "event_type": "drive.file.bitable_record_changed_v1",
        },
        "event": {"table_id": "tbl_cockpit", "record_id": "recabc"},
    }

    def fake_lark(args, **kwargs):
        lark_calls.append(list(args))
        if "+record-get" in args:
            return {
                "data": [[
                    "Product Lab 本地", "manager", "智能伙伴", "接入，先审计",
                ]],
                "fields": ["负责人团队", "负责人agent", "当前动作", "老板决策"],
                "record_id_list": ["recabc"],
                "has_more": False,
            }
        return {"ok": True}

    def fake_dispatch(intent):
        dispatched.append(intent)
        return base_intake.DispatchResult(True, task_id="T-9", message="ok")

    with isolated_env() as tmp:
        (tmp / "claudeteam.toml").write_text(_base_config(tmp), encoding="utf-8")
        first = base_intake.handle_payload(
            payload, profile="p", lark_call=fake_lark, dispatch=fake_dispatch)
        second = base_intake.handle_payload(
            payload, profile="p", lark_call=fake_lark, dispatch=fake_dispatch)

    assert first == 1
    assert second == 0
    assert len(dispatched) == 1
    assert any("+record-upsert" in call for call in lark_calls)


def test_writeback_clears_action_field_after_dispatch():
    lark_calls = []
    payload = {
        "header": {
            "event_id": "evt_action_1",
            "event_type": "drive.file.bitable_record_changed_v1",
        },
        "event": {"table_id": "tbl_cockpit", "record_id": "recaction"},
    }

    def fake_lark(args, **kwargs):
        lark_calls.append(list(args))
        if "+record-get" in args:
            return {
                "data": [[
                    "工作分身", "manager", "待核验", "重新核验",
                ]],
                "fields": ["负责人团队", "负责人agent", "当前状态", "老板操作"],
                "record_id_list": ["recaction"],
                "has_more": False,
            }
        return {"ok": True}

    with isolated_env() as tmp:
        (tmp / "claudeteam.toml").write_text(_base_config(tmp), encoding="utf-8")
        sent = base_intake.handle_payload(
            payload,
            profile="p",
            lark_call=fake_lark,
            dispatch=lambda intent: base_intake.DispatchResult(
                True, task_id="T-10", message="ok"),
        )

    assert sent == 1
    upserts = [call for call in lark_calls if "+record-upsert" in call]
    assert upserts
    body = upserts[-1][upserts[-1].index("--json") + 1]
    assert '"老板操作": null' in body


def test_handle_payload_direct_status_updates_local_task_and_writebacks():
    lark_calls = []
    payload = {
        "header": {
            "event_id": "evt_status_1",
            "event_type": "drive.file.bitable_record_changed_v1",
        },
        "event": {"table_id": "tbl_tasks", "record_id": "recstatus"},
    }

    def fake_lark(args, **kwargs):
        lark_calls.append(list(args))
        if "+record-get" in args:
            return {
                "data": [[
                    "Product Lab 本地/T-1",
                    "Product Lab 本地",
                    "验收通过",
                    "artifacts/T-1/report.md",
                ]],
                "fields": ["任务卡ID", "所属战场", "状态", "真实产物链接"],
                "record_id_list": ["recstatus"],
                "has_more": False,
            }
        return {"ok": True}

    with isolated_env() as tmp:
        root = tmp / "projects"
        root.mkdir()
        (tmp / "claudeteam.toml").write_text(_base_config(root), encoding="utf-8")
        team = _team(root, "product-lab")
        with base_intake._temporary_env(base_intake._target_env(team)):
            tid = tasks.create("manager", "可验收任务", artifact_path="")
        sent = base_intake.handle_payload(payload, profile="p", lark_call=fake_lark)
        with base_intake._temporary_env(base_intake._target_env(team)):
            row = tasks.get(tid)

    assert sent == 1
    assert row is not None
    assert row["status"] == "已完成"
    assert row["artifact_path"] == "artifacts/T-1/report.md"
    upserts = [call for call in lark_calls if "+record-upsert" in call]
    assert upserts
    body = upserts[-1][upserts[-1].index("--json") + 1]
    assert '"状态": "已完成"' in body
    assert '"当前状态": "已完成"' in body


def test_direct_complete_requires_artifact_and_keeps_task_open():
    lark_calls = []
    payload = {
        "header": {
            "event_id": "evt_status_no_artifact",
            "event_type": "drive.file.bitable_record_changed_v1",
        },
        "event": {"table_id": "tbl_tasks", "record_id": "recstatus"},
    }

    def fake_lark(args, **kwargs):
        lark_calls.append(list(args))
        if "+record-get" in args:
            return {
                "data": [[
                    "Product Lab 本地/T-1",
                    "Product Lab 本地",
                    "关闭任务",
                    "",
                ]],
                "fields": ["任务卡ID", "所属战场", "状态", "真实产物链接"],
                "record_id_list": ["recstatus"],
                "has_more": False,
            }
        return {"ok": True}

    with isolated_env() as tmp:
        root = tmp / "projects"
        root.mkdir()
        (tmp / "claudeteam.toml").write_text(_base_config(root), encoding="utf-8")
        team = _team(root, "product-lab")
        with base_intake._temporary_env(base_intake._target_env(team)):
            tid = tasks.create("manager", "缺产物任务")
        sent = base_intake.handle_payload(payload, profile="p", lark_call=fake_lark)
        with base_intake._temporary_env(base_intake._target_env(team)):
            row = tasks.get(tid)

    assert sent == 1
    assert row is not None
    assert row["status"] == "待处理"
    upserts = [call for call in lark_calls if "+record-upsert" in call]
    assert upserts
    body = upserts[-1][upserts[-1].index("--json") + 1]
    assert "cannot be marked" in body


def test_dispatch_intent_writes_task_into_target_team():
    with isolated_env() as tmp:
        _team(tmp, "product-lab")
        intent = base_intake.DispatchIntent(
            table_id="tbl_cockpit",
            record_id="rec1",
            team_label="Product Lab 本地",
            agent="manager",
            title="Base 决策任务",
            body="请执行 Base 决策任务",
            fingerprint="fp",
        )
        result = base_intake.dispatch_intent(intent, root=tmp)
        with base_intake._temporary_env(base_intake._target_env(tmp / "product-lab")):
            rows = tasks.list_tasks()

    assert result.ok is True
    assert result.task_id
    assert rows[0]["assignee"] == "manager"
    assert "Base" in rows[0]["title"]


def test_dispatch_intent_can_route_to_remote_snapshot_team():
    calls = []

    def fake_run(args, **kwargs):
        calls.append({"args": list(args), "kwargs": dict(kwargs)})
        return FakeProc(returncode=0, stdout="ok [task_id=T-cloud]\n")

    with isolated_env() as tmp:
        snapshot = tmp / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        snapshot.mkdir(parents=True)
        (snapshot / "meta.json").write_text(
            "{"
            '"key":"product_lab_cloud",'
            '"label":"Product Lab 云上",'
            '"remote_host":"cloud-host",'
            '"remote_product":"/srv/ai/projects/product-lab"'
            "}\n",
            encoding="utf-8",
        )
        intent = base_intake.DispatchIntent(
            table_id="tbl_agents",
            record_id="rec_cloud",
            team_label="Product Lab 云上",
            agent="worker_ops",
            title="云上 Base 决策任务",
            body="请云上执行 Base 决策任务",
            fingerprint="fp",
        )
        result = base_intake.dispatch_intent(intent, root=tmp, run=fake_run)

    assert result.ok is True
    assert result.task_id == "T-cloud"
    assert calls
    assert calls[0]["args"][0:2] == ["ssh", "cloud-host"]
    assert "worker_ops" in " ".join(calls[0]["args"])


def test_dispatch_intent_maps_smart_partner_to_product_lab_owner_team():
    with isolated_env() as tmp:
        _team(tmp, "product-lab")
        intent = base_intake.DispatchIntent(
            table_id="tbl_cockpit",
            record_id="rec_sp",
            team_label="智能伙伴",
            agent="manager",
            title="接入审计",
            body="请审计智能伙伴接入",
            fingerprint="fp",
        )
        result = base_intake.dispatch_intent(intent, root=tmp)
        with base_intake._temporary_env(base_intake._target_env(tmp / "product-lab")):
            rows = tasks.list_tasks()

    assert result.ok is True
    assert rows and rows[0]["assignee"] == "manager"
    assert "审计智能伙伴接入" in rows[0]["title"] or "智能伙伴" in rows[0]["title"]


def test_dispatch_intent_maps_local_openclaw_to_product_lab_owner_team():
    with isolated_env() as tmp:
        _team(tmp, "product-lab")
        intent = base_intake.DispatchIntent(
            table_id="tbl_cockpit",
            record_id="rec_local_oc",
            team_label="本地 OpenClaw",
            agent="manager",
            title="恢复本地 OpenClaw",
            body="请核验本地 OpenClaw 网关和模型配置",
            fingerprint="fp",
        )
        result = base_intake.dispatch_intent(intent, root=tmp)
        with base_intake._temporary_env(base_intake._target_env(tmp / "product-lab")):
            rows = tasks.list_tasks()

    assert result.ok is True
    assert rows and rows[0]["assignee"] == "manager"
    assert "OpenClaw" in rows[0]["title"]
