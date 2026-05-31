"""Tests for `claudeteam cockpit-sync`."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from helpers import attr_patch, run_cli
from claudeteam.commands import cockpit_sync


def _team(root: Path, name: str, *, task_status: str = "进行中",
          founder_stage: str = "mvp", with_founder_meta: bool = True,
          artifact_path: str = "artifacts/T-1.md",
          create_artifact: bool = False) -> Path:
    team = root / name
    (team / "state" / "facts").mkdir(parents=True)
    (team / "claudeteam.toml").write_text("chat_id = 'oc_x'\n", encoding="utf-8")
    task = {
            "id": "T-1",
            "title": "真实任务",
            "assignee": "manager",
            "status": task_status,
            "artifact_path": artifact_path,
            "created_at": 1779330000000,
            "updated_at": 1779330100000,
        }
    if with_founder_meta:
        task.update({
            "founder_stage": founder_stage,
            "stage_exit_evidence": "至少 3 个真实用户愿意继续试用",
            "evidence_action": "今天验证 1 个用户痛点并记录原话",
            "non_goal": "不扩功能，不写新页面",
        })
    (team / "state" / "tasks.json").write_text(json.dumps({
        "tasks": [task],
        "_meta": {"last_id": 1},
    }, ensure_ascii=False), encoding="utf-8")
    if create_artifact and artifact_path and not artifact_path.startswith("http"):
        artifact = team / artifact_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("# 真实产物\n", encoding="utf-8")
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


def test_cockpit_sync_json_builds_rows_from_local_facts():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli(["cockpit-sync", "--root", str(root), "--json"])
    assert rc == 0
    data = json.loads(out)
    row = data["rows"][0]
    agent_rows = {row["员工"]: row for row in data["agent_rows"]}
    task_rows = {row["任务号"]: row for row in data["task_rows"]}
    assert data["write"] is False
    assert row["战场"] == "Product Lab 本地"
    assert row["当前动作"] == "真实任务"
    assert row["阶段"] == "MVP / 最小可行"
    assert row["阶段出口证据"] == "至少 3 个真实用户愿意继续试用"
    assert row["今天最小证据动作"] == "今天验证 1 个用户痛点并记录原话"
    assert row["不做什么"] == "不扩功能，不写新页面"
    assert row["Founder OS 状态"] == "已回写"
    assert row["状态分栏"] == "执行中"
    assert "老板操作" in row["建议操作"]
    assert "继续执行" in row["建议操作"]
    assert row["本机可见活跃任务数"] == 1
    assert agent_rows["manager"]["战场"] == "Product Lab 本地"
    assert agent_rows["manager"]["手头任务数"] == 1
    assert agent_rows["manager"]["名片"] == "Product Lab 本地/manager"
    assert agent_rows["manager"]["老板看人分组"] == "01 有活 / 要看"
    assert agent_rows["manager"]["未收口原因"].startswith("执行中未收口")
    assert "老板操作" in agent_rows["manager"]["建议操作"]
    assert task_rows["T-1"]["任务名"] == "真实任务"
    assert task_rows["T-1"]["任务卡ID"] == "Product Lab 本地/T-1"
    assert task_rows["T-1"]["负责人"] == "manager"
    assert task_rows["T-1"]["状态"] == "进行中"
    assert task_rows["T-1"]["未收口原因"] == "执行中未收口"
    assert task_rows["T-1"]["老板处理分类"] == "03 进行中"
    assert task_rows["T-1"]["产物可见性"] == "⚠️ 本地缺失"
    assert task_rows["T-1"]["产物链接"] == "本地缺失，需员工补交可打开产物"
    assert task_rows["T-1"]["真实产物链接"] == "artifacts/T-1.md"


def test_task_rows_separate_openable_links_from_local_artifact_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        local = _team(root, "product-lab", create_artifact=True)
        rows = cockpit_sync.build_task_rows(local, include_private=True)

    row = rows[0]
    assert row["产物可见性"] == "📎 本地待上传"
    assert row["产物链接"] == "见【老板可见产物】附件；若未显示附件，需运行上传产物"
    assert row["真实产物链接"] == "artifacts/T-1.md"
    assert row["_artifact_upload_path"].endswith("artifacts/T-1.md")


def test_task_rows_do_not_queue_missing_artifacts_for_upload():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = _team(root, "product-lab")
        rows = cockpit_sync.build_task_rows(team, include_private=True)

    row = rows[0]
    assert row["产物可见性"] == "⚠️ 本地缺失"
    assert "_artifact_upload_path" not in row


def test_task_rows_keep_external_artifacts_as_openable_links():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = _team(root, "website-chuhai-team",
                     artifact_path="https://example.com/report")
        rows = cockpit_sync.build_task_rows(team, include_private=True)

    row = rows[0]
    assert row["产物可见性"] == "🔗 外链可打开"
    assert row["产物链接"] == "https://example.com/report"
    assert "_artifact_upload_path" not in row


def test_task_rows_prefer_markdown_image_for_attachment_upload():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = _team(root, "product-lab")
        artifact = team / "artifacts" / "T-1.md"
        image = team / "artifacts" / "preview.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        artifact.write_text("# 交付\n\n![preview](preview.png)\n", encoding="utf-8")
        rows = cockpit_sync.build_task_rows(team, include_private=True)

    row = rows[0]
    assert row["产物可见性"] == "📎 本地待上传"
    assert row["_artifact_upload_path"].endswith("artifacts/preview.png")


def test_task_rows_choose_viewable_file_inside_artifact_directory():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = _team(root, "website-chuhai-team",
                     artifact_path="artifacts/T-1-dir")
        directory = team / "artifacts" / "T-1-dir"
        directory.mkdir(parents=True)
        (directory / "notes.md").write_text("# notes\n", encoding="utf-8")
        image = directory / "preview.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        rows = cockpit_sync.build_task_rows(team, include_private=True)

    assert rows[0]["产物可见性"] == "📎 本地待上传"
    assert rows[0]["_artifact_upload_path"].endswith("artifacts/T-1-dir/preview.png")


def test_task_rows_mark_empty_artifact_as_none():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = _team(root, "todo002-study-coach", artifact_path="")
        rows = cockpit_sync.build_task_rows(team)

    assert rows[0]["产物可见性"] == "无产物"
    assert rows[0]["产物链接"] == "无产物"


def test_agent_row_counts_fresh_status_task_as_current_work():
    now = datetime.fromtimestamp(1779347000000 / 1000, cockpit_sync._CST)
    rows = cockpit_sync.build_agent_rows(
        Path("/tmp/WebsiteChuhai"),
        now=now,
        label="WebsiteChuhai",
        configured={"worker_engineer": {"role": "网站工程员工"}},
        tasks=[],
        statuses=[{
            "agent": "worker_engineer",
            "status": "进行中",
            "task": "T-26 批量转录后台运行中：tmux=t26_transcribe",
            "blocker": "",
            "updated_at": 1779346700000,
        }],
    )

    row = rows[0]
    assert row["员工"] == "worker_engineer"
    assert row["工作状态"] == "手头有活"
    assert row["轻松状态"] == "专注中"
    assert row["手头任务数"] == 1
    assert row["进行中任务数"] == 1
    assert "T-26 批量转录后台运行中" in row["当前任务"]


def test_cockpit_sync_json_marks_missing_founder_metadata_for_followup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "todo002-study-coach", with_founder_meta=False)

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli(["cockpit-sync", "--root", str(root), "--json"])
    assert rc == 0
    data = json.loads(out)
    row = data["rows"][0]
    assert row["阶段"] == "待团队回写"
    assert row["Founder OS 状态"].startswith("待回写")
    assert row["状态分栏"] == "待核验"
    assert row["是否需要老板"] == "是"
    assert "老板操作" in row["建议操作"]
    assert "重新核验" in row["建议操作"]
    assert "Founder OS 字段" in row["老板下一步"]


def test_cockpit_sync_merges_registry_only_teams():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        local = _team(root, "product-lab")
        registry = root / "registry.py"
        registry.write_text(
            "import json\n"
            "print(json.dumps({'teams': [\n"
            "  {'key': 'product_lab_local', 'label': 'Product Lab 本地',"
            "   'config_path': '" + str(local / "claudeteam.toml") + "',"
            "   'chat_id': 'oc_local', 'status': '当前可用'},\n"
            "  {'key': 'product_lab_cloud', 'label': 'Product Lab 云上',"
            "   'config_path': '/srv/ai/projects/product-lab/claudeteam.toml',"
            "   'chat_id': 'oc_cloud', 'status': '需云机核验',"
            "   'notes': '云上冷备'},\n"
            "  {'key': 'smart_partner', 'label': '智能伙伴',"
            "   'chat_id': 'oc_partner', 'status': '需接入驾驶舱'}\n"
            "]}, ensure_ascii=False))\n",
            encoding="utf-8",
        )

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli([
                "cockpit-sync", "--root", str(root),
                "--registry-script", str(registry),
                "--json",
            ])
    assert rc == 0
    data = json.loads(out)
    rows = {row["战场"]: row for row in data["rows"]}
    assert sorted(rows) == ["Product Lab 云上", "Product Lab 本地", "智能伙伴"]
    assert rows["Product Lab 云上"]["Founder OS 状态"] == "需远端账本回写"
    assert rows["Product Lab 云上"]["状态分栏"] == "待核验"
    assert "重新核验" in rows["Product Lab 云上"]["建议操作"]
    assert rows["智能伙伴"]["状态分栏"] == "需要老板动作"
    assert rows["智能伙伴"]["是否需要老板"] == "是"
    assert "老板决策" in rows["智能伙伴"]["建议操作"]


def test_cockpit_sync_renders_local_openclaw_as_separate_runtime():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")
        registry = root / "registry.py"
        registry.write_text(
            "import json\n"
            "print(json.dumps({'teams': [\n"
            "  {'key': 'local_openclaw', 'label': '本地 OpenClaw',"
            "   'config_path': '/Users/example/.openclaw/openclaw.json',"
            "   'status': '网关可达｜模型已对齐',"
            "   'agent_count': 1,"
            "   'notes': 'gateway=127.0.0.1:18789 可达；默认模型=codex/gpt-5.5'}\n"
            "]}, ensure_ascii=False))\n",
            encoding="utf-8",
        )

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli([
                "cockpit-sync", "--root", str(root),
                "--registry-script", str(registry),
                "--json",
            ])

    assert rc == 0
    data = json.loads(out)
    rows = {row["战场"]: row for row in data["rows"]}
    row = rows["本地 OpenClaw"]
    assert row["当前状态"] == "已接入"
    assert row["状态分栏"] == "运行中"
    assert row["负责人agent"] == "manager"
    assert row["是否需要老板"] == "否"
    assert "codex/gpt-5.5" in row["事实来源"]
    assert "Product Lab manager" in row["老板下一步"]
    assert row["阻塞"] == "无"
    assert row["卡住判断"] == "未卡住"


def test_cockpit_sync_surfaces_local_openclaw_cron_failures():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")
        registry = root / "registry.py"
        registry.write_text(
            "import json\n"
            "print(json.dumps({'teams': [\n"
            "  {'key': 'local_openclaw', 'label': '本地 OpenClaw',"
            "   'config_path': '/Users/example/.openclaw/openclaw.json',"
            "   'status': '网关可达｜模型已对齐',"
            "   'agent_count': 1,"
            "   'notes': 'gateway=127.0.0.1:18789 可达；默认模型=codex/gpt-5.5',"
            "   'cron_summary': '定时任务 1 个，启用 1 个，异常 1 个，下次 05-22 17:02',"
            "   'cron_attention': '每日Toolify榜单抓取-写入飞书 连续失败 41 次：缺飞书投递目标',"
            "   'cron_latest': '每日Toolify榜单抓取-写入飞书: error；最近产出：已提取前10个AI产品'}\n"
            "]}, ensure_ascii=False))\n",
            encoding="utf-8",
        )

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli([
                "cockpit-sync", "--root", str(root),
                "--registry-script", str(registry),
                "--json",
            ])

    assert rc == 0
    data = json.loads(out)
    row = {row["战场"]: row for row in data["rows"]}["本地 OpenClaw"]
    assert row["当前状态"] == "已接入｜定时任务异常"
    assert row["健康灯"] == "黄｜OpenClaw 可用，cron 异常"
    assert row["是否需要老板"] == "是"
    assert "连续失败 41 次" in row["阻塞"]
    assert "定时任务 1 个" in row["任务清单"]
    assert row["卡住判断"] == "定时任务未闭环"
    assert "修复 OpenClaw 定时任务" in row["老板下一步"]


def test_cockpit_sync_mentions_openclaw_cron_dedupe_rule():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")
        registry = root / "registry.py"
        registry.write_text(
            "import json\n"
            "print(json.dumps({'teams': [\n"
            "  {'key': 'local_openclaw', 'label': '本地 OpenClaw',"
            "   'config_path': '/Users/example/.openclaw/openclaw.json',"
            "   'status': '网关可达｜模型已对齐',"
            "   'agent_count': 1,"
            "   'notes': 'gateway=127.0.0.1:18789 可达；默认模型=codex/gpt-5.5',"
            "   'cron_summary': '定时任务 1 个，启用 1 个，异常 1 个，下次 05-22 17:02',"
            "   'cron_dedupe': '已启用｜账本 24 条',"
            "   'cron_attention': '每日Toolify增量抓取-去重写入飞书 连续失败 41 次：飞书多维表格写入 403',"
            "   'cron_latest': '每日Toolify增量抓取-去重写入飞书: error，已启用增量去重'}\n"
            "]}, ensure_ascii=False))\n",
            encoding="utf-8",
        )

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli([
                "cockpit-sync", "--root", str(root),
                "--registry-script", str(registry),
                "--json",
            ])

    assert rc == 0
    data = json.loads(out)
    row = {row["战场"]: row for row in data["rows"]}["本地 OpenClaw"]
    assert "增量去重：已启用｜账本 24 条" in row["今天最小证据动作"]
    assert "增量去重 已启用｜账本 24 条" in row["任务清单"]
    assert "去重规则已启用" in row["老板下一步"]
    assert "飞书多维表写入权限" in row["老板下一步"]


def test_cockpit_sync_includes_remote_snapshot_rows():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")
        remote = root / "product-lab" / "state" / "remote-teams" / "todo002_cloud"
        (remote / "state" / "facts").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "todo002_cloud",
            "label": "TODO002 云上",
            "fetched_at": "2026-05-21 12:00 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "claudeteam.toml").write_text(
            "\n".join([
                "[team.agents.manager]",
                'role = "云上主管"',
                "[team.agents.worker_cloud]",
                'role = "云上执行员工"',
                "",
            ]),
            encoding="utf-8",
        )
        (remote / "state" / "tasks.json").write_text(json.dumps({
            "tasks": [{
                "id": "T-C1",
                "title": "云上真实任务",
                "assignee": "worker_cloud",
                "status": "进行中",
                "artifact_path": "artifacts/T-C1.md",
                "created_at": 4102444800000,
                "updated_at": 4102444860000,
            }],
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "facts" / "status.json").write_text(json.dumps({
            "agents": {
                "worker_cloud": {
                    "agent": "worker_cloud",
                    "status": "进行中",
                    "task": "云上真实任务",
                    "blocker": "",
                    "updated_at": 4102444920000,
                }
            }
        }, ensure_ascii=False), encoding="utf-8")

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli(["cockpit-sync", "--root", str(root), "--json"])

    assert rc == 0
    data = json.loads(out)
    rows = {row["战场"]: row for row in data["rows"]}
    agent_rows = {row["名片"]: row for row in data["agent_rows"]}
    task_rows = {row["任务卡ID"]: row for row in data["task_rows"]}
    assert rows["TODO002 云上"]["事实来源"].startswith("remote_snapshot=")
    assert rows["TODO002 云上"]["本机可见活跃任务数"] == 1
    assert "TODO002 云上/worker_cloud" in agent_rows
    assert agent_rows["TODO002 云上/worker_cloud"]["工作状态"] == "手头有活"
    assert task_rows["TODO002 云上/T-C1"]["来源群"] == "云上 ClaudeTeam tasks.json"


def test_cockpit_sync_shows_configured_cloud_staff_before_status_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")
        cloud_cfg = root / "todo002-study-coach" / "claudeteam.cloud.toml"
        cloud_cfg.parent.mkdir()
        cloud_cfg.write_text(
            "\n".join([
                "[team.agents.manager]",
                'role = "云上主管"',
                "[team.agents.worker_deepsea]",
                'role = "深海圈知识库管理员"',
                "",
            ]),
            encoding="utf-8",
        )
        registry = root / "registry.py"
        registry.write_text(
            "import json\n"
            "print(json.dumps({'teams': [\n"
            "  {'key': 'todo002_cloud', 'label': 'TODO002 云上',"
            "   'config_path': '" + str(cloud_cfg) + "',"
            "   'chat_id': 'oc_cloud', 'status': '当前可用'}\n"
            "]}, ensure_ascii=False))\n",
            encoding="utf-8",
        )

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health):
            rc, out, _ = run_cli([
                "cockpit-sync", "--root", str(root),
                "--registry-script", str(registry),
                "--json",
            ])

    assert rc == 0
    data = json.loads(out)
    agent_rows = {row["名片"]: row for row in data["agent_rows"]}
    assert "TODO002 云上/manager" in agent_rows
    assert "TODO002 云上/worker_deepsea" in agent_rows
    assert agent_rows["TODO002 云上/worker_deepsea"]["轻松状态"] == "状态待确认"
    assert agent_rows["TODO002 云上/worker_deepsea"]["老板看人分组"] == "03 待唤醒确认"
    assert [row for row in data["task_rows"] if row["所属战场"] == "TODO002 云上"] == []


def test_task_cards_keep_old_active_tasks_visible():
    with tempfile.TemporaryDirectory() as tmp:
        team = Path(tmp) / "work-assistant-team"
        (team / "state" / "facts").mkdir(parents=True)
        (team / "claudeteam.toml").write_text("chat_id = 'oc_x'\n", encoding="utf-8")
        (team / "state" / "tasks.json").write_text(json.dumps({
            "tasks": [{
                "id": "T-old",
                "title": "旧但未收口的任务",
                "assignee": "worker_frontend",
                "status": "进行中",
                "created_at": 1,
                "updated_at": 1,
            }],
        }, ensure_ascii=False), encoding="utf-8")

        rows = cockpit_sync.build_task_rows(team)

    assert [row["任务号"] for row in rows] == ["T-old"]
    assert rows[0]["未收口原因"] == "执行中未收口"


def test_task_rows_group_boss_processing_lanes():
    with tempfile.TemporaryDirectory() as tmp:
        team = Path(tmp) / "product-lab"
        (team / "state").mkdir(parents=True)
        (team / "state" / "tasks.json").write_text(json.dumps({
            "tasks": [
                {
                    "id": "T-1",
                    "title": "等老板拍板",
                    "assignee": "manager",
                    "status": "进行中",
                    "description": "需要老板确认预算",
                    "created_at": 1779330000000,
                    "updated_at": 1779330400000,
                },
                {
                    "id": "T-2",
                    "title": "待验收",
                    "assignee": "worker",
                    "status": "待验收",
                    "artifact_path": "artifacts/T-2.md",
                    "created_at": 1779330000000,
                    "updated_at": 1779330300000,
                },
                {
                    "id": "T-3",
                    "title": "未启动",
                    "assignee": "worker",
                    "status": "待处理",
                    "created_at": 1779330000000,
                    "updated_at": 1779330200000,
                },
                {
                    "id": "T-4",
                    "title": "已收口",
                    "assignee": "worker",
                    "status": "已完成",
                    "created_at": 1779330000000,
                    "updated_at": 1779330100000,
                },
            ],
            "_meta": {"last_id": 4},
        }, ensure_ascii=False), encoding="utf-8")

        now = datetime.fromtimestamp(1779330500000 / 1000, cockpit_sync._CST)
        rows = {
            row["任务号"]: row
            for row in cockpit_sync.build_task_rows(team, now=now)
        }

    assert rows["T-1"]["老板处理分类"] == "01 等我拍板"
    assert rows["T-2"]["老板处理分类"] == "02 等验收"
    assert rows["T-3"]["老板处理分类"] == "04 未启动"
    assert rows["T-4"]["老板处理分类"] == "90 已收口"


def test_cockpit_sync_write_updates_existing_record_by_battle():
    rows = [{"战场": "Product Lab 本地", "当前状态": "执行中"}]
    calls = []

    def fake_lark(args, **kwargs):
        calls.append({"args": list(args), "kwargs": dict(kwargs)})
        if "+record-list" in args:
            assert "--format" in args and "json" in args
            assert "--field-id" in args and "战场" in args
            return {
                "data": [["Product Lab 本地"]],
                "fields": ["战场"],
                "record_id_list": ["rec_existing"],
                "has_more": False,
            }
        return {"ok": True}

    result = cockpit_sync.sync_rows(
        rows, base_token="base", table_id="tbl", profile="p",
        lark_call=fake_lark)
    assert result["ok"] is True
    assert result["updated"] == 1
    assert calls[1]["kwargs"]["profile"] == "p"
    assert "--record-id" in calls[1]["args"]
    assert "rec_existing" in calls[1]["args"]


def test_cockpit_sync_write_updates_agent_rows_by_unique_key():
    rows = [{"名片": "Product Lab 本地/manager", "战场": "Product Lab 本地",
             "员工": "manager"}]
    calls = []

    def fake_lark(args, **kwargs):
        calls.append({"args": list(args), "kwargs": dict(kwargs)})
        if "+record-list" in args:
            assert "--field-id" in args and "名片" in args
            return {
                "data": [["Product Lab 本地/manager"]],
                "fields": ["名片"],
                "record_id_list": ["rec_agent"],
                "has_more": False,
            }
        return {"ok": True}

    result = cockpit_sync.sync_rows(
        rows, base_token="base", table_id="tbl_agents", profile="p",
        key_field="名片", lark_call=fake_lark)

    assert result["ok"] is True
    assert result["updated"] == 1
    assert "--record-id" in calls[1]["args"]
    assert "rec_agent" in calls[1]["args"]


def test_cockpit_sync_write_updates_task_rows_by_task_id():
    rows = [{
        "任务号": "T-1",
        "任务名": "真实任务",
        "状态": "进行中",
        "_artifact_upload_path": "/tmp/private.md",
    }]
    calls = []

    def fake_lark(args, **kwargs):
        calls.append({"args": list(args), "kwargs": dict(kwargs)})
        if "+record-list" in args:
            assert "--field-id" in args and "任务号" in args
            return {
                "data": [["T-1"]],
                "fields": ["任务号"],
                "record_id_list": ["rec_task"],
                "has_more": False,
            }
        return {"ok": True}

    result = cockpit_sync.sync_rows(
        rows, base_token="base", table_id="tbl_tasks", profile="p",
        key_field="任务号", lark_call=fake_lark)

    assert result["ok"] is True
    assert result["updated"] == 1
    assert "--record-id" in calls[1]["args"]
    assert "rec_task" in calls[1]["args"]
    payload = json.loads(calls[1]["args"][calls[1]["args"].index("--json") + 1])
    assert "_artifact_upload_path" not in payload


def test_upload_task_artifacts_uploads_only_rows_with_private_local_path():
    with tempfile.TemporaryDirectory() as tmp:
        artifact = Path(tmp) / "preview.png"
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n")
        rows = [
            {"任务卡ID": "Product Lab 本地/T-1",
             "_artifact_upload_path": str(artifact)},
            {"任务卡ID": "Product Lab 本地/T-2"},
        ]
        calls = []

        def fake_lark(args, **kwargs):
            calls.append({"args": list(args), "kwargs": dict(kwargs)})
            if "+record-list" in args:
                return {
                    "data": [["Product Lab 本地/T-1"]],
                    "fields": ["任务卡ID"],
                    "record_id_list": ["rec_task"],
                    "has_more": False,
                }
            return {"ok": True}

        result = cockpit_sync.upload_task_artifacts(
            rows, base_token="base", table_id="tbl_tasks", profile="p",
            attachment_field="fld_artifact", lark_call=fake_lark)

    assert result["ok"] is True
    assert result["uploaded"] == 1
    assert result["skipped"] == 1
    upload_call = calls[1]["args"]
    assert "+record-upload-attachment" in upload_call
    assert "rec_task" in upload_call
    assert "fld_artifact" in upload_call
    assert "preview.png" in upload_call
    assert calls[1]["kwargs"]["cwd"] == str(artifact.parent)


def test_cockpit_sync_text_reports_write_failure():
    with tempfile.TemporaryDirectory() as tmp:
        team = _team(Path(tmp), "work-assistant-team")

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": False,
                    "bad": 1, "warn": 0, "issues": ["❌ router down"]}

        def fake_lark(args, **kwargs):
            if "+record-list" in args:
                return {"items": []}
            return None

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health), \
                attr_patch(cockpit_sync.lark, call=fake_lark):
            rc, out, _ = run_cli(["cockpit-sync", "--write", str(team)])
    assert rc == 1
    assert "工作分身" in out
    assert "failed=1" in out
    assert "record-upsert failed" in out


def test_cockpit_sync_help_and_top_level_registration():
    rc, out, _ = run_cli(["cockpit-sync", "--help"])
    assert rc == 0
    assert "usage: claudeteam cockpit-sync" in out
    rc, out, _ = run_cli([])
    assert rc == 0
    assert "cockpit-sync" in out
