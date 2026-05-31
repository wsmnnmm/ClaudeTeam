"""Tests for `claudeteam cockpit-brief`."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from helpers import attr_patch, run_cli
from claudeteam.commands import cockpit_brief, cockpit_sync


def _team(root: Path, name: str, *, status: str = "进行中",
          title: str = "页面还原验收",
          with_founder_meta: bool = True) -> Path:
    team = root / name
    (team / "state" / "facts").mkdir(parents=True)
    (team / "claudeteam.toml").write_text("chat_id = 'oc_x'\n", encoding="utf-8")
    task = {
        "id": "T-1",
        "title": title,
        "assignee": "manager",
        "status": status,
        "artifact_path": "artifacts/T-1.md",
        "created_at": 1779330000000,
        "updated_at": 1779330100000,
    }
    if with_founder_meta:
        task.update({
            "founder_stage": "mvp",
            "stage_exit_evidence": "截图和预览地址可验收",
            "evidence_action": "今天核对 1280x720 页面截图",
            "non_goal": "不改接口协议",
        })
    (team / "state" / "tasks.json").write_text(json.dumps({
        "tasks": [task],
        "_meta": {"last_id": 1},
    }, ensure_ascii=False), encoding="utf-8")
    (team / "state" / "facts" / "status.json").write_text(json.dumps({
        "agents": {
            "manager": {
                "agent": "manager",
                "status": "进行中",
                "task": "ready",
                "blocker": "",
                "updated_at": 1779330200000,
            }
        }
    }, ensure_ascii=False), encoding="utf-8")
    return team


def test_cockpit_brief_json_prioritizes_boss_attention_without_base_write():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "work-assistant-team", with_founder_meta=False)
        _team(root, "product-lab")

        def fake_health(path):
            if path.name == "product-lab":
                return {"team": path.name, "path": str(path), "ok": True,
                        "bad": 0, "warn": 0, "issues": []}
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli([
                "cockpit-brief", "--root", str(root),
                "--json", "--max-teams", "2",
            ])

    assert rc == 0
    data = json.loads(out)
    assert data["ok"] is True
    assert data["summary"]["needs_boss"] == 1
    assert data["summary"]["active"] == 1
    assert data["boss_brief"][0].startswith("【拍板】工作分身")
    assert data["teams"][0]["team"] == "工作分身"
    assert data["teams"][0]["source_type"] == "本机"
    assert data["source_summary"]["本机"] == 2
    assert data["teams"][0]["band"] == "needs_boss"


def test_cockpit_brief_builds_pending_approval_queue():
    now = datetime.fromtimestamp(1779330300000 / 1000, cockpit_sync._CST)
    rows = [
        {
            "战场": "Traffic Ops",
            "状态分栏": "执行中",
            "是否需要老板": "否",
            "本机可见活跃任务数": 2,
            "待验收任务数": 1,
            "证据缺口数": 0,
            "待验收代表任务": "T-5 [待验收] 深圈活动获客帖 -> manager",
            "当前动作": "深圈活动获客帖",
            "老板下一步": "",
            "阻塞": "无",
        },
        {
            "战场": "工作分身",
            "状态分栏": "执行中",
            "是否需要老板": "否",
            "本机可见活跃任务数": 11,
            "待验收任务数": 0,
            "证据缺口数": 0,
            "当前动作": "T-165 学测 UI 链路",
            "老板下一步": "",
            "阻塞": "无",
        },
        {
            "战场": "Product Lab 本地",
            "状态分栏": "待核验",
            "是否需要老板": "是",
            "本机可见活跃任务数": 6,
            "待验收任务数": 0,
            "证据缺口数": 0,
            "当前动作": "OpenClaw / Base",
            "老板下一步": "确认 Base 写权限",
            "阻塞": "Base 权限待确认",
        },
    ]

    brief = cockpit_brief.build_brief(
        rows, now=now, approval_base_url="https://brief.local/approve")
    approvals = brief["pending_approvals"]
    rendered = cockpit_brief.render_markdown(brief)

    assert [a["action_type"] for a in approvals] == [
        "验收优先", "收敛优先", "权限/blocker",
    ]
    assert approvals[0]["approval_phrase"] == "批准 A1"
    assert "https://brief.local/approve?" in approvals[0]["approve_url"]
    assert "本次待批动作" in rendered
    assert "深圈活动获客帖" in rendered
    assert "2 小时内执行" in approvals[0]["instruction"]


def test_cockpit_brief_renders_local_and_cloud_sources_separately():
    now = datetime.fromtimestamp(1779330300000 / 1000, cockpit_sync._CST)
    rows = [
        {
            "战场": "Product Lab 本地",
            "事实类型": "本机",
            "状态分栏": "执行中",
            "是否需要老板": "否",
            "本机可见活跃任务数": 1,
            "待验收任务数": 0,
            "证据缺口数": 0,
            "当前动作": "本机任务",
            "当前状态": "执行中",
            "老板下一步": "",
            "阻塞": "无",
        },
        {
            "战场": "Product Lab 云上",
            "事实类型": "云上",
            "状态分栏": "运行中",
            "是否需要老板": "否",
            "本机可见活跃任务数": 0,
            "待验收任务数": 0,
            "证据缺口数": 0,
            "当前动作": "云端部署快照",
            "当前状态": "执行中",
            "老板下一步": "",
            "阻塞": "无",
        },
    ]

    brief = cockpit_brief.build_brief(rows, now=now)
    rendered = cockpit_brief.render_markdown(brief)

    assert brief["source_summary"] == {"本机": 1, "云上": 1}
    assert "来源: 云上 1 / 本机 1" in rendered
    assert "[本机] Product Lab 本地" in rendered
    assert "[云上] Product Lab 云上" in rendered


def test_cockpit_brief_calls_stale_heartbeat_a_recheck_not_feishu_auth_breakage():
    now = datetime.fromtimestamp(1779330300000 / 1000, cockpit_sync._CST)
    row = cockpit_sync.build_row(
        Path("/tmp/team"),
        now=now,
        label="工作分身",
        health={
            "team": "team",
            "path": "/tmp/team",
            "ok": True,
            "bad": 0,
            "warn": 1,
            "issues": ["⚠️ worker_frontend heartbeat stale"],
        },
    )
    brief = cockpit_brief.build_brief([row], now=now)

    assert brief["summary"]["stale_only"] == 1
    assert brief["summary"]["needs_boss"] == 0
    assert "不等同于飞书机器人或 CLI 授权损坏" in brief["boss_brief"][0]


def test_cockpit_brief_does_not_confuse_codex_cli_heartbeat_with_lark_cli():
    now = datetime.fromtimestamp(1779330300000 / 1000, cockpit_sync._CST)
    row = cockpit_sync.build_row(
        Path("/tmp/team"),
        now=now,
        label="Product Lab 本地",
        health={
            "team": "team",
            "path": "/tmp/team",
            "ok": True,
            "bad": 0,
            "warn": 1,
            "issues": ["⚠️ manager: pane ready (codex-cli) but heartbeat is stale"],
        },
    )
    brief = cockpit_brief.build_brief([row], now=now)

    assert brief["summary"]["stale_only"] == 1
    assert brief["summary"]["needs_boss"] == 0


def test_cockpit_brief_marks_lark_cli_warning_as_needing_boss_attention():
    now = datetime.fromtimestamp(1779330300000 / 1000, cockpit_sync._CST)
    row = cockpit_sync.build_row(
        Path("/tmp/team"),
        now=now,
        label="WebsiteChuhai",
        health={
            "team": "team",
            "path": "/tmp/team",
            "ok": False,
            "bad": 0,
            "warn": 1,
            "issues": ["⚠️ lark-cli profile missing App Secret"],
        },
    )
    brief = cockpit_brief.build_brief([row], now=now)

    assert brief["summary"]["needs_boss"] == 1
    assert brief["summary"]["stale_only"] == 0
    assert brief["boss_brief"][0].startswith("【拍板】WebsiteChuhai")


def test_cockpit_brief_out_writes_markdown_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")
        out_file = root / "runtime-health" / "boss-brief.md"

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli([
                "cockpit-brief", "--root", str(root), "--out", str(out_file),
            ])
        written = out_file.read_text(encoding="utf-8")

    assert rc == 0
    assert "# 老板简报" in out
    assert "来源: 本机 1" in written
    assert "[本机] Product Lab 本地" in written


def test_cockpit_brief_help_and_top_level_registration():
    rc, out, _ = run_cli(["cockpit-brief", "--help"])
    assert rc == 0
    assert "usage: claudeteam cockpit-brief" in out
    rc, out, _ = run_cli([])
    assert rc == 0
    assert "cockpit-brief" in out
