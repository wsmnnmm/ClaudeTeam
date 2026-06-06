"""Tests for `claudeteam cockpit-sync`."""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from helpers import attr_patch, run_cli
from claudeteam.commands import cockpit_sync


def _builder_daily_receipt(**overrides) -> dict:
    receipt = {
        "schema_version": "builder_daily_receipt/v1",
        "receipt_id": "builder-daily:2026-06-01:worker_research",
        "report_date": "2026-06-01",
        "date": "2026-06-01",
        "agent": "worker_research",
        "markdown_file": "docs/builder-daily/2026-06-01.md",
        "markdown_sha256": "sha256_builder_daily_test",
        "message_id": "om_builder_daily",
        "sent_at": "2026-06-01T08:31:00+08:00",
        "event_time": "2026-06-01T08:30:00+08:00",
        "processed_time": "2026-06-01T08:30:20+08:00",
        "delivered_time": "2026-06-01T08:31:00+08:00",
        "verified_time": "2026-06-01T08:31:10+08:00",
        "schedule_source": "cron",
        "schedule_command": "builder-daily-dispatch",
        "delivery_mode": "scheduled",
        "delivery_delay_seconds": 20,
        "delivery_delay_minutes": 0,
    }
    receipt.update(overrides)
    return receipt


def _todo002_digest_receipt(**overrides) -> dict:
    receipt = {
        "schema_version": "todo002_digest_receipt/v1",
        "receipt_id": "digest-delivery:2026-06-01:manager",
        "lane": "deepsea-digest",
        "report_date": "2026-06-01",
        "date": "2026-06-01",
        "agent": "manager",
        "title": "worker_curator 今日摘要片段（2026-06-01）",
        "digest_file": "/srv/ai/projects/todo002-study-coach/knowledge-base/digests/2026-06-01-worker-curator-deepsea-summary.md",
        "source_artifact_file": "/srv/ai/projects/todo002-study-coach/knowledge-base/curated/2026-06-01/deepsea-curation-2026-06-01.md",
        "source_artifact_sha256": "sha256_todo002_curated_test",
        "source_handoff_mode": "worker_handoff",
        "message_id": "om_todo002_digest",
        "event_time": "2026-06-01T10:00:00+08:00",
        "processed_time": "2026-06-01T10:01:00+08:00",
        "delivered_time": "2026-06-01T10:01:01+08:00",
        "verified_time": "2026-06-01T10:01:05+08:00",
        "schedule_source": "cron",
        "schedule_command": "deepsea-digest-verify",
        "delivery_mode": "scheduled",
        "delivery_delay_seconds": 60,
        "delivery_delay_minutes": 1,
    }
    receipt.update(overrides)
    return receipt


def _traffic_brief_receipt(**overrides) -> dict:
    receipt = {
        "schema_version": "traffic_ops_brief_receipt/v1",
        "receipt_id": "traffic-brief:2026-06-01:worker_ops",
        "report_date": "2026-06-01",
        "artifact_file": "/srv/ai/projects/traffic-ops-team/state/traffic-ledger/boss-brief.md",
        "boss_view_file": "/srv/ai/projects/traffic-ops-team/artifacts/traffic/boss-comms/latest-status-card.md",
        "event_time": "2026-06-01T18:00:00+08:00",
        "processed_time": "2026-06-01T18:03:00+08:00",
        "verified_time": "2026-06-01T18:03:00+08:00",
        "schedule_source": "cron",
        "schedule_command": "traffic-brief",
        "delivery_mode": "scheduled",
        "delivery_delay_seconds": 180,
        "delivery_delay_minutes": 3,
    }
    receipt.update(overrides)
    return receipt


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
            "issue_class": "local-business",
            "current_segment": "receipt",
            "next_natural_window": "2026-06-03 08:45 CST",
            "base_absorb_needed": "no",
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


def _remote_snapshot(root: Path, owner: str, key: str, label: str, *,
                     fetched_at: str,
                     task_id: str,
                     task_title: str,
                     agent: str = "worker_cloud") -> Path:
    remote = root / owner / "state" / "remote-teams" / key
    (remote / "state" / "facts").mkdir(parents=True)
    (remote / "meta.json").write_text(json.dumps({
        "key": key,
        "label": label,
        "fetched_at": fetched_at,
    }, ensure_ascii=False), encoding="utf-8")
    (remote / "claudeteam.toml").write_text(
        "\n".join([
            "[team.agents.manager]",
            'role = "云上主管"',
            f"[team.agents.{agent}]",
            'role = "云上执行员工"',
            "",
        ]),
        encoding="utf-8",
    )
    (remote / "state" / "tasks.json").write_text(json.dumps({
        "tasks": [{
            "id": task_id,
            "title": task_title,
            "assignee": agent,
            "status": "进行中",
            "artifact_path": f"artifacts/{task_id}.md",
            "created_at": 4102444800000,
            "updated_at": 4102444860000,
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (remote / "state" / "facts" / "status.json").write_text(json.dumps({
        "agents": {
            agent: {
                "agent": agent,
                "status": "进行中",
                "task": task_title,
                "blocker": "",
                "updated_at": 4102444920000,
            }
        }
    }, ensure_ascii=False), encoding="utf-8")
    return remote


def _write_todo002_mentor_chain(team: Path, *, request_id: str,
                                request_mode: str,
                                return_status: str,
                                source_request_id: str | None = None) -> None:
    request_dir = team / "state" / "cross-team" / "todo002-mentor-requests"
    import_dir = team / "state" / "cross-team" / "todo002-mentor-return-import-runs"
    boss_dir = team / "state" / "cross-team" / "todo002-mentor-boss-view"
    remote_return_dir = (
        team / "state" / "remote-teams" / "todo002_cloud" / "knowledge-base"
        / "cross-team" / "returns" / "source-team" / f"{request_id}-return"
    )
    request_dir.mkdir(parents=True, exist_ok=True)
    import_dir.mkdir(parents=True, exist_ok=True)
    boss_dir.mkdir(parents=True, exist_ok=True)
    remote_return_dir.mkdir(parents=True, exist_ok=True)

    request_path = request_dir / f"{request_id}.receipt.json"
    request_path.write_text(json.dumps({
        "schema_version": "source_todo002_request_receipt/v1",
        "request_id": request_id,
        "request_mode": request_mode,
        "sent_at": "2026-06-01T22:42:06+08:00",
    }, ensure_ascii=False), encoding="utf-8")

    import_path = import_dir / "2026-06-01T14-42-45.000Z.json"
    import_path.write_text(json.dumps({
        "schema_version": "source_todo002_import_receipt/v1",
        "ok": True,
        "count": 1,
        "imported": 1,
        "skipped": 0,
        "failed": 0,
        "created_at": "2026-06-01T14:42:45.000Z",
    }, ensure_ascii=False), encoding="utf-8")

    return_json = remote_return_dir / "return.json"
    return_json.write_text(json.dumps({
        "returnId": f"{request_id}-return",
        "status": return_status,
        "requestMode": request_mode,
        "sourceRequestId": source_request_id or request_id,
    }, ensure_ascii=False), encoding="utf-8")

    boss_path = boss_dir / f"{request_id}-return.receipt.json"
    boss_path.write_text(json.dumps({
        "schema_version": "source_todo002_boss_view_receipt/v1",
        "return_id": f"{request_id}-return",
        "return_json": str(return_json),
        "message_id": "om_boss_view",
        "sent_at": "2026-06-01T22:45:08+08:00",
    }, ensure_ascii=False), encoding="utf-8")


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
    assert row["问题类型"] == "local-business"
    assert row["当前段位"] == "receipt"
    assert row["下一自然窗口"] == "2026-06-03 08:45 CST"
    assert row["需上收基座"] == "no"
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
    assert task_rows["T-1"]["问题类型"] == "local-business"
    assert task_rows["T-1"]["当前段位"] == "receipt"
    assert task_rows["T-1"]["下一自然窗口"] == "2026-06-03 08:45 CST"
    assert task_rows["T-1"]["需上收基座"] == "no"
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


def test_cockpit_sync_pull_remote_refreshes_matching_snapshot_before_build():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        local = _team(root, "product-lab")
        script = root / "product-lab" / "scripts" / "cloud" / "claudeteam-cloud-pull-facts.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        remote = _remote_snapshot(
            root,
            "product-lab",
            "product_lab_cloud",
            "Product Lab 云上",
            fetched_at="2026-05-01 00:00 CST",
            task_id="T-201",
            task_title="云上 Builder Daily",
            agent="worker_research",
        )

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        def fake_run(args, **kwargs):
            assert args[0] == "bash"
            assert args[1].endswith("product-lab/scripts/cloud/claudeteam-cloud-pull-facts.sh")
            assert Path(kwargs["cwd"]).resolve() == root.resolve()
            assert Path(kwargs["env"]["CLAUDETEAM_LOCAL_ROOT"]).resolve() == root.resolve()
            (remote / "meta.json").write_text(json.dumps({
                "key": "product_lab_cloud",
                "label": "Product Lab 云上",
                "fetched_at": datetime.now(cockpit_sync._CST).strftime("%Y-%m-%d %H:%M CST"),
            }, ensure_ascii=False), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="remote facts snapshot refreshed\n", stderr="")

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health), \
                attr_patch(cockpit_sync.subprocess, run=fake_run):
            rc, out, _ = run_cli([
                "cockpit-sync",
                "--root", str(root),
                "--pull-remote",
                str(local),
                "--json",
            ])

    assert rc == 0
    data = json.loads(out)
    assert data["remote_pull"]["ok"] is True
    assert data["remote_pull"]["attempted"] == 1
    pull_run = data["remote_pull"]["runs"][0]
    assert pull_run["label"] == "Product Lab 云上"
    assert pull_run["ok"] is True
    remote_row = {row["战场"]: row for row in data["rows"]}["Product Lab 云上"]
    assert "云上快照拉取已过期" not in json.dumps(remote_row, ensure_ascii=False)


def test_cockpit_sync_pull_remote_failure_returns_nonzero_and_surfaces_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        local = _team(root, "product-lab")
        script = root / "product-lab" / "scripts" / "cloud" / "claudeteam-cloud-pull-facts.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        _remote_snapshot(
            root,
            "product-lab",
            "product_lab_cloud",
            "Product Lab 云上",
            fetched_at="2026-05-01 00:00 CST",
            task_id="T-202",
            task_title="云上 Builder Daily",
            agent="worker_research",
        )

        def fake_health(path):
            return {"team": path.name, "path": str(path), "ok": True,
                    "bad": 0, "warn": 0, "issues": []}

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 7, stdout="", stderr="ssh timeout")

        with attr_patch(cockpit_sync.fleet_health, _health_payload=fake_health), \
                attr_patch(cockpit_sync.subprocess, run=fake_run):
            rc, out, _ = run_cli([
                "cockpit-sync",
                "--root", str(root),
                "--pull-remote",
                str(local),
                "--json",
            ])

    assert rc == 1
    data = json.loads(out)
    assert data["remote_pull"]["ok"] is False
    assert data["remote_pull"]["failed"] == 1
    pull_run = data["remote_pull"]["runs"][0]
    assert pull_run["returncode"] == 7
    assert "ssh timeout" in pull_run["stderr"]
    rows = {row["战场"]: row for row in data["rows"]}
    assert "Product Lab 云上" in rows


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


def test_cockpit_sync_aggregates_remote_snapshots_across_team_dirs_and_prefers_fresher_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")
        _team(root, "todo002-study-coach")
        _team(root, "traffic-ops-team")
        _remote_snapshot(
            root, "product-lab", "todo002_cloud", "TODO002 云上",
            fetched_at="2026-05-21 13:36 CST",
            task_id="T-stale", task_title="旧 TODO002 快照任务",
        )
        _remote_snapshot(
            root, "todo002-study-coach", "todo002_cloud", "TODO002 云上",
            fetched_at="2099-06-01 23:08 CST",
            task_id="T-fresh", task_title="新 TODO002 快照任务",
            agent="worker_deepsea",
        )
        _remote_snapshot(
            root, "traffic-ops-team", "traffic_ops_cloud", "Traffic Ops 云上",
            fetched_at="2099-06-01 23:09 CST",
            task_id="T-traffic", task_title="Traffic 云上快照任务",
            agent="worker_ops",
        )

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
    assert "Traffic Ops 云上" in rows
    assert rows["TODO002 云上"]["事实来源"].startswith(
        "remote_snapshot="
        + str((root / "todo002-study-coach" / "state" / "remote-teams" / "todo002_cloud").resolve())
    )
    assert "fetched_at=2099-06-01 23:08 CST" in rows["TODO002 云上"]["事实来源"]
    assert rows["TODO002 云上"]["当前动作"] == "新 TODO002 快照任务"
    assert "TODO002 云上/worker_deepsea" in agent_rows
    assert "Traffic Ops 云上/worker_ops" in agent_rows
    assert task_rows["TODO002 云上/T-fresh"]["任务名"] == "新 TODO002 快照任务"
    assert task_rows["Traffic Ops 云上/T-traffic"]["任务名"] == "Traffic 云上快照任务"


def test_build_row_marks_todo002_mentor_chain_smoke_roundtrip_as_not_real():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = _team(root, "website-chuhai-team")
        _write_todo002_mentor_chain(
            team,
            request_id="20260601T224203-request-receipt-smoke-website",
            request_mode="smoke",
            return_status="smoke_ack",
        )
        row = cockpit_sync.build_row(
            team,
            now=datetime(2026, 6, 1, 23, 0, tzinfo=cockpit_sync._CST),
            health={"team": team.name, "path": str(team), "ok": True,
                    "bad": 0, "warn": 0, "issues": []},
        )

    assert row["状态分栏"] == "待核验"
    assert "同单闭环仍是 smoke" in row["阻塞"]
    assert "mentor_request_mode=smoke" in row["事实来源"]
    assert "mentor_return_status=smoke_ack" in row["事实来源"]
    assert "mentor_continuity_status=failed" in row["事实来源"]


def test_build_row_marks_single_real_todo002_mentor_chain_as_unproven_continuity():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = _team(root, "work-assistant-team")
        _write_todo002_mentor_chain(
            team,
            request_id="20260602T084500-real-mentor-request",
            request_mode="mentor_request",
            return_status="completed",
        )
        row = cockpit_sync.build_row(
            team,
            now=datetime(2026, 6, 2, 9, 0, tzinfo=cockpit_sync._CST),
            health={"team": team.name, "path": str(team), "ok": True,
                    "bad": 0, "warn": 0, "issues": []},
        )

    assert row["状态分栏"] == "待核验"
    assert "连续真实样本不足: 1/2" in row["阻塞"]
    assert "mentor_request_mode=mentor_request" in row["事实来源"]
    assert "mentor_return_status=completed" in row["事实来源"]
    assert "mentor_continuity_status=failed" in row["事实来源"]
    assert "mentor_consecutive_real_roundtrips=1" in row["事实来源"]


def test_build_row_accepts_todo002_mentor_chain_when_two_real_roundtrips_exist():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = _team(root, "work-assistant-team")
        _write_todo002_mentor_chain(
            team,
            request_id="20260601T214500-real-mentor-request-prev",
            request_mode="mentor_request",
            return_status="completed",
        )
        _write_todo002_mentor_chain(
            team,
            request_id="20260602T084500-real-mentor-request",
            request_mode="mentor_request",
            return_status="completed",
        )
        row = cockpit_sync.build_row(
            team,
            now=datetime(2026, 6, 2, 9, 0, tzinfo=cockpit_sync._CST),
            health={"team": team.name, "path": str(team), "ok": True,
                    "bad": 0, "warn": 0, "issues": []},
        )

    assert row["状态分栏"] == "执行中"
    assert row["阻塞"] == "无"
    assert "mentor_continuity_status=passed" in row["事实来源"]
    assert "mentor_consecutive_real_roundtrips=2" in row["事实来源"]


def test_cockpit_sync_marks_remote_snapshot_stale_when_fetch_time_is_old():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _team(root, "product-lab")
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "facts").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-05-23 17:11 CST",
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
    row = {row["战场"]: row for row in data["rows"]}["Product Lab 云上"]
    assert row["状态分栏"] == "待核验"
    assert "云上快照拉取已过期" in row["阻塞"]
    assert "fetched_at=2026-05-23 17:11 CST" in row["事实来源"]


def test_snapshot_health_marks_builder_daily_log_without_receipt_as_unverified():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "facts").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-06-01 09:00 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "facts" / "status.json").write_text(json.dumps({
            "agents": {
                "worker_research": {
                    "agent": "worker_research",
                    "status": "ready",
                    "task": "待命",
                    "blocker": "",
                    "updated_at": 1780275540000,
                }
            }
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "facts" / "logs.jsonl").write_text(
            '{"agent":"worker_research","type":"say","content":"📰 **Builder Daily | 2026-06-01**"}\n',
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 1, 9, 1, tzinfo=cockpit_sync._CST))

    assert health["ok"] is False
    assert health["warn"] >= 1
    assert any("Builder Daily" in issue and "receipt" in issue for issue in health["issues"])


def test_remote_fact_source_includes_builder_daily_receipt_details():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "builder-daily").mkdir(parents=True)
        (remote / "docs" / "builder-daily").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-06-01 09:00 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "docs" / "builder-daily" / "2026-06-01.md").write_text(
            "📰 **Builder Daily | 2026-06-01**\n",
            encoding="utf-8",
        )

        source = cockpit_sync._remote_fact_source(
            remote, now=datetime(2026, 6, 1, 9, 1, tzinfo=cockpit_sync._CST))

    assert "builder_daily_receipt=" in source
    assert "builder_daily_report_date=2026-06-01" in source
    assert "builder_daily_receipt_id=builder-daily:2026-06-01:worker_research" in source
    assert "builder_daily_markdown_sha256=sha256_builder_daily_test" in source
    assert "builder_daily_message_id=om_builder_daily" in source
    assert "builder_daily_sent_at=2026-06-01T08:31:00+08:00" in source
    assert "builder_daily_delivered_time=2026-06-01T08:31:00+08:00" in source
    assert "builder_daily_verified_time=2026-06-01T08:31:10+08:00" in source
    assert "builder_daily_schedule_source=cron" in source
    assert "builder_daily_schedule_command=builder-daily-dispatch" in source
    assert "builder_daily_delivery_mode=scheduled" in source
    assert "builder_daily_delivery_delay_minutes=0" in source
    assert "builder_daily_artifact=" in source


def test_snapshot_health_marks_builder_daily_receipt_missing_time_contract_as_unverified():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "builder-daily").mkdir(parents=True)
        (remote / "docs" / "builder-daily").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-06-01 09:00 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(
                schema_version="",
                receipt_id="",
                markdown_sha256="",
                event_time="",
                processed_time="",
                delivered_time="",
                verified_time="",
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "docs" / "builder-daily" / "2026-06-01.md").write_text(
            "📰 **Builder Daily | 2026-06-01**\n",
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 1, 9, 1, tzinfo=cockpit_sync._CST))

    assert health["ok"] is False
    assert health["warn"] >= 1
    assert any("receipt 字段不完整" in issue and "schema_version" in issue for issue in health["issues"])


def test_snapshot_health_marks_builder_daily_manual_receipt_as_not_natural():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "builder-daily").mkdir(parents=True)
        (remote / "docs" / "builder-daily").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-06-01 21:00 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(
                schedule_source="manual",
                schedule_command="builder-daily-verify",
                delivery_mode="catchup",
                processed_time="2026-06-01T20:09:02+08:00",
                delivered_time="2026-06-01T20:09:03+08:00",
                verified_time="2026-06-01T20:09:03+08:00",
                delivery_delay_seconds=41942,
                delivery_delay_minutes=699,
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "docs" / "builder-daily" / "2026-06-01.md").write_text(
            "📰 **Builder Daily | 2026-06-01**\n",
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 1, 21, 10, tzinfo=cockpit_sync._CST))
        layers = cockpit_sync._remote_health_layers(
            remote, now=datetime(2026, 6, 1, 21, 10, tzinfo=cockpit_sync._CST))

    assert health["ok"] is False
    assert health["warn"] >= 1
    assert any("不是自然准点发送" in issue for issue in health["issues"])
    assert any("延迟 699 分钟" in issue for issue in health["issues"])
    assert layers["scheduler"] == "yellow"


def test_snapshot_health_summary_uses_yellow_scheduler_for_manual_builder_daily_receipt():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "builder-daily").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-06-01 21:00 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "health.json").write_text(json.dumps({
            "ok": True,
            "bad": 0,
            "warn": 0,
            "lines": [
                "config:",
                "  ✅ team config: 2 agent(s)",
                "  ✅ chat_id: oc_cloud",
                "  ✅ lark_profile: product-lab",
                "",
                "tmux:",
                "  ✅ tmux session: product-lab-cloud",
                "  ⚠️ worker_research: pane up but CLI not ready yet",
                "",
                "daemons:",
                "  ✅ router: alive (123)",
                "  ✅ watchdog: alive (456)",
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(
                schedule_source="manual",
                schedule_command="builder-daily-verify",
                delivery_mode="catchup",
                processed_time="2026-06-01T20:09:02+08:00",
                delivered_time="2026-06-01T20:09:03+08:00",
                verified_time="2026-06-01T20:09:03+08:00",
                delivery_delay_seconds=41942,
                delivery_delay_minutes=699,
            ), ensure_ascii=False),
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 1, 21, 10, tzinfo=cockpit_sync._CST))

    assert health["issues"][0].startswith("⚠️ 云上健康分层:")
    assert "scheduler=黄" in health["issues"][0]


def test_build_row_uses_fresh_snapshot_warning_copy_instead_of_stale_copy():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _remote_snapshot(
            root,
            "product-lab",
            "product_lab_cloud",
            "Product Lab 云上",
            fetched_at="2026-06-02 00:18 CST",
            task_id="T-301",
            task_title="云上 Builder Daily",
            agent="worker_research",
        )
        (remote / "state" / "builder-daily").mkdir(parents=True, exist_ok=True)
        (remote / "docs" / "builder-daily").mkdir(parents=True, exist_ok=True)
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(
                schedule_source="manual",
                schedule_command="builder-daily-verify",
                delivery_mode="catchup",
                processed_time="2026-06-01T20:09:02+08:00",
                delivered_time="2026-06-01T20:09:03+08:00",
                verified_time="2026-06-01T20:09:03+08:00",
                delivery_delay_seconds=41942,
                delivery_delay_minutes=699,
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "docs" / "builder-daily" / "2026-06-01.md").write_text(
            "📰 **Builder Daily | 2026-06-01**\n",
            encoding="utf-8",
        )
        (remote / "health.json").write_text(json.dumps({
            "ok": True,
            "bad": 0,
            "warn": 0,
            "lines": [
                "config:",
                "  ✅ team config: 2 agent(s)",
                "  ✅ chat_id: oc_cloud",
                "  ✅ lark_profile: product-lab",
                "",
                "tmux:",
                "  ✅ tmux session: product-lab-cloud",
                "  ⚠️ worker_research: pane up but CLI not ready yet",
                "",
                "daemons:",
                "  ✅ router: alive (123)",
                "  ✅ watchdog: alive (456)",
                "",
                "scheduler:",
                "  ✅ cron entries: ensure-up / builder-daily / boss-todo present",
                "  ✅ cron heartbeat: ensure-up source=cron exit=0 age=4m",
            ],
        }, ensure_ascii=False), encoding="utf-8")

        row = cockpit_sync.build_row(
            remote,
            now=datetime(2026, 6, 2, 0, 18, tzinfo=cockpit_sync._CST),
            label="Product Lab 云上",
            health=cockpit_sync._snapshot_health(
                remote, now=datetime(2026, 6, 2, 0, 18, tzinfo=cockpit_sync._CST)),
            source_label="云上",
            fact_source=cockpit_sync._remote_fact_source(
                remote, now=datetime(2026, 6, 2, 0, 18, tzinfo=cockpit_sync._CST)),
        )

    assert row["健康灯"] == "黄｜云上快照已刷新但有风险项"
    assert row["风险详情"] == "黄｜云上快照已刷新但有风险项"
    assert row["核验状态"] == "等待自然窗口"


def test_build_row_cloud_pre_window_surfaces_next_natural_window_in_boss_view():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _remote_snapshot(
            root,
            "product-lab",
            "product_lab_cloud",
            "Product Lab 云上",
            fetched_at="2026-06-02 03:51 CST",
            task_id="T-301",
            task_title="云上 Builder Daily",
            agent="worker_research",
        )
        (remote / "state" / "builder-daily").mkdir(parents=True, exist_ok=True)
        (remote / "docs" / "builder-daily").mkdir(parents=True, exist_ok=True)
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(
                schedule_source="manual",
                schedule_command="builder-daily-verify",
                delivery_mode="catchup",
                processed_time="2026-06-01T20:09:02+08:00",
                delivered_time="2026-06-01T20:09:03+08:00",
                verified_time="2026-06-01T20:09:03+08:00",
                delivery_delay_seconds=41942,
                delivery_delay_minutes=699,
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "docs" / "builder-daily" / "2026-06-01.md").write_text(
            "📰 **Builder Daily | 2026-06-01**\n",
            encoding="utf-8",
        )
        (remote / "health.json").write_text(json.dumps({
            "ok": True,
            "bad": 0,
            "warn": 0,
            "lines": [
                "config:",
                "  ✅ team config: 2 agent(s)",
                "  ✅ chat_id: oc_cloud",
                "  ✅ lark_profile: product-lab",
                "",
                "tmux:",
                "  ✅ tmux session: product-lab-cloud",
                "  ⚠️ worker_research: pane up but CLI not ready yet",
                "",
                "daemons:",
                "  ✅ router: alive (123)",
                "  ✅ watchdog: alive (456)",
                "",
                "scheduler:",
                "  ✅ cron entries: ensure-up / builder-daily / boss-todo present",
                "  ✅ cron heartbeat: ensure-up source=cron exit=0 age=4m",
            ],
        }, ensure_ascii=False), encoding="utf-8")

        row = cockpit_sync.build_row(
            remote,
            now=datetime(2026, 6, 2, 3, 55, tzinfo=cockpit_sync._CST),
            label="Product Lab 云上",
            health=cockpit_sync._snapshot_health(
                remote, now=datetime(2026, 6, 2, 3, 55, tzinfo=cockpit_sync._CST)),
            source_label="云上",
            fact_source=cockpit_sync._remote_fact_source(
                remote, now=datetime(2026, 6, 2, 3, 55, tzinfo=cockpit_sync._CST)),
        )

    assert row["当前状态"] == "等待窗口"
    assert row["状态分栏"] == "等待窗口"
    assert row["老板分组"] == "盯今天窗口"
    assert row["是否需要老板"] == "否"
    assert row["老板动作标签"] == "继续执行"
    assert "等待今天 08:45 自然窗口" in row["老板一句话"]
    assert "连续自然样本 0/2" in row["老板一句话"]
    assert "⚠️ Builder Daily 最新 receipt 距计划时点延迟 699 分钟" in row["阻塞"]


def test_build_row_cloud_marks_same_day_manual_sample_as_not_natural():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _remote_snapshot(
            root,
            "traffic-ops-team",
            "traffic_ops_cloud",
            "Traffic Ops 云上",
            fetched_at="2026-06-02 18:10 CST",
            task_id="T-traffic",
            task_title="云上 traffic brief",
            agent="worker_ops",
        )
        (remote / "state" / "traffic-brief").mkdir(parents=True, exist_ok=True)
        (remote / "state" / "traffic-brief" / "2026-06-02.receipt.json").write_text(
            json.dumps(_traffic_brief_receipt(
                report_date="2026-06-02",
                receipt_id="traffic-brief:2026-06-02:worker_ops",
                schedule_source="manual",
                schedule_command="traffic-brief",
                delivery_mode="manual",
                listener_mode="standby",
                processed_time="2026-06-02T23:01:27+08:00",
                verified_time="2026-06-02T23:01:27+08:00",
                delivery_delay_seconds=18087,
                delivery_delay_minutes=301,
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "health.json").write_text(json.dumps({
            "ok": True,
            "bad": 0,
            "warn": 0,
            "lines": [
                "config:",
                "  ✅ team config: 2 agent(s)",
                "  ✅ chat_id: oc_cloud",
                "  ✅ lark_profile: traffic-ops",
                "",
                "standby:",
                "  ℹ️ standby mode: tmux/router/watchdog not required for scheduled traffic-brief",
            ],
        }, ensure_ascii=False), encoding="utf-8")

        row = cockpit_sync.build_row(
            remote,
            now=datetime(2026, 6, 2, 18, 10, tzinfo=cockpit_sync._CST),
            label="Traffic Ops 云上",
            health=cockpit_sync._snapshot_health(
                remote, now=datetime(2026, 6, 2, 18, 10, tzinfo=cockpit_sync._CST)),
            source_label="云上",
            fact_source=cockpit_sync._remote_fact_source(
                remote, now=datetime(2026, 6, 2, 18, 10, tzinfo=cockpit_sync._CST)),
        )

    assert row["当前状态"] == "未自然闭环"
    assert row["状态分栏"] == "未自然闭环"
    assert row["老板分组"] == "先修当日链路"
    assert row["是否需要老板"] == "否"
    assert row["老板动作标签"] == "继续执行"
    assert "今日样本未自然闭环" in row["老板一句话"]
    assert "manual" in row["老板一句话"]
    assert row["阻塞"].startswith("⚠️ Traffic Brief 最新 receipt 不是自然准点发送")


def test_build_row_cloud_distinguishes_same_day_green_from_continuity_gap():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = _remote_snapshot(
            root,
            "todo002-study-coach",
            "todo002_cloud",
            "TODO002 云上",
            fetched_at="2026-06-02 10:05 CST",
            task_id="T-digest",
            task_title="云上 digest",
            agent="worker_deepsea",
        )
        (remote / "state" / "digest-delivery").mkdir(parents=True, exist_ok=True)
        (remote / "state" / "digest-delivery" / "2026-06-02.receipt.json").write_text(
            json.dumps(_todo002_digest_receipt(
                report_date="2026-06-02",
                date="2026-06-02",
                receipt_id="digest-delivery:2026-06-02:manager",
                digest_file="/srv/ai/projects/todo002-study-coach/knowledge-base/digests/2026-06-02-worker-curator-deepsea-summary.md",
                message_id="om_todo002_digest_today",
                event_time="2026-06-02T10:00:00+08:00",
                processed_time="2026-06-02T10:01:00+08:00",
                delivered_time="2026-06-02T10:01:01+08:00",
                verified_time="2026-06-02T10:01:05+08:00",
                delivery_delay_seconds=60,
                delivery_delay_minutes=1,
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "health.json").write_text(json.dumps({
            "ok": True,
            "bad": 0,
            "warn": 0,
            "lines": [
                "config:",
                "  ✅ team config: 2 agent(s)",
                "  ✅ chat_id: oc_cloud",
                "  ✅ lark_profile: todo002",
                "",
                "daemons:",
                "  ✅ router: alive (123)",
                "  ✅ watchdog: alive (456)",
                "",
                "scheduler:",
                "  ✅ cron heartbeat: deepsea-digest-verify source=cron exit=0 age=2m",
            ],
        }, ensure_ascii=False), encoding="utf-8")

        row = cockpit_sync.build_row(
            remote,
            now=datetime(2026, 6, 2, 10, 5, tzinfo=cockpit_sync._CST),
            label="TODO002 云上",
            health=cockpit_sync._snapshot_health(
                remote, now=datetime(2026, 6, 2, 10, 5, tzinfo=cockpit_sync._CST)),
            source_label="云上",
            fact_source=cockpit_sync._remote_fact_source(
                remote, now=datetime(2026, 6, 2, 10, 5, tzinfo=cockpit_sync._CST)),
        )

    assert row["当前状态"] == "连续性未证明"
    assert row["状态分栏"] == "连续性未证明"
    assert row["老板分组"] == "继续观察连续性"
    assert row["是否需要老板"] == "否"
    assert row["老板动作标签"] == "继续执行"
    assert "今日已自然闭环" in row["老板一句话"]
    assert "连续自然样本仅 1/2" in row["老板一句话"]


def test_snapshot_health_marks_todo002_digest_manual_receipt_as_not_natural():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "todo002-study-coach" / "state" / "remote-teams" / "todo002_cloud"
        (remote / "state" / "digest-delivery").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "todo002_cloud",
            "label": "TODO002 云上",
            "fetched_at": "2026-06-01 21:10 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "digest-delivery" / "2026-06-01.receipt.json").write_text(
            json.dumps(_todo002_digest_receipt(
                schedule_source="manual",
                schedule_command="ensure-up",
                delivery_mode="catchup",
                processed_time="2026-06-01T21:02:14+08:00",
                delivered_time="2026-06-01T21:02:15+08:00",
                verified_time="2026-06-01T21:02:15+08:00",
                delivery_delay_seconds=39734,
                delivery_delay_minutes=662,
            ), ensure_ascii=False),
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 1, 21, 10, tzinfo=cockpit_sync._CST))
        layers = cockpit_sync._remote_health_layers(
            remote, now=datetime(2026, 6, 1, 21, 10, tzinfo=cockpit_sync._CST))

    assert health["ok"] is False
    assert health["warn"] >= 1
    assert any("TODO002 Digest 最新 receipt 不是自然准点发送" in issue for issue in health["issues"])
    assert any("延迟 662 分钟" in issue for issue in health["issues"])
    assert layers["scheduler"] == "yellow"


def test_todo002_digest_receipt_infers_manager_takeover_from_digest_body():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "todo002-study-coach" / "state" / "remote-teams" / "todo002_cloud"
        (remote / "state" / "digest-delivery").mkdir(parents=True)
        (remote / "knowledge-base" / "digests").mkdir(parents=True)
        (remote / "state" / "digest-delivery" / "2026-06-02.receipt.json").write_text(
            json.dumps(_todo002_digest_receipt(
                report_date="2026-06-02",
                date="2026-06-02",
                receipt_id="digest-delivery:2026-06-02:manager",
                digest_file="/srv/ai/projects/todo002-study-coach/knowledge-base/digests/2026-06-02-worker-curator-deepsea-summary.md",
                message_id="om_todo002_digest_takeover",
                source_artifact_file="",
                source_artifact_sha256="",
                source_handoff_mode="",
                event_time="2026-06-02T10:00:00+08:00",
                processed_time="2026-06-02T10:01:00+08:00",
                delivered_time="2026-06-02T10:01:01+08:00",
                verified_time="2026-06-02T10:01:05+08:00",
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "knowledge-base" / "digests" / "2026-06-02-worker-curator-deepsea-summary.md").write_text(
            "\n".join([
                "# worker_curator 今日摘要片段（2026-06-02）",
                "",
                "- 来源 artifact: `/srv/ai/projects/todo002-study-coach/knowledge-base/curated/2026-06-02/deepsea-curation-2026-06-02.md`",
                "- 来源 artifact SHA256: `sha256_takeover`",
                "",
                "## Manager Takeover Note",
                "",
                "- provider 503",
            ]),
            encoding="utf-8",
        )

        receipt = cockpit_sync._todo002_digest_receipt(
            remote, now=datetime(2026, 6, 2, 10, 5, tzinfo=cockpit_sync._CST))
        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 2, 10, 5, tzinfo=cockpit_sync._CST))

    assert receipt["source_handoff_mode"] == "manager_takeover"
    assert receipt["source_artifact_file"].endswith(
        "knowledge-base/curated/2026-06-02/deepsea-curation-2026-06-02.md"
    )
    assert receipt["source_artifact_sha256"] == "sha256_takeover"
    assert any("上游不是自然 worker 交付" in issue for issue in health["issues"])


def test_todo002_digest_receipt_flags_source_artifact_drift():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "todo002-study-coach" / "state" / "remote-teams" / "todo002_cloud"
        (remote / "state" / "digest-delivery").mkdir(parents=True)
        (remote / "knowledge-base" / "digests").mkdir(parents=True)
        (remote / "knowledge-base" / "curated" / "2026-06-02").mkdir(parents=True)
        curated_path = (
            remote / "knowledge-base" / "curated" / "2026-06-02"
            / "deepsea-curation-2026-06-02.md"
        )
        curated_path.write_text(
            "# DeepSea Curation 2026-06-02\n\nworker delivered a later revision.\n",
            encoding="utf-8",
        )
        (remote / "state" / "digest-delivery" / "2026-06-02.receipt.json").write_text(
            json.dumps(_todo002_digest_receipt(
                report_date="2026-06-02",
                date="2026-06-02",
                receipt_id="digest-delivery:2026-06-02:manager",
                digest_file="/srv/ai/projects/todo002-study-coach/knowledge-base/digests/2026-06-02-worker-curator-deepsea-summary.md",
                message_id="om_todo002_digest_drift",
                source_artifact_file="",
                source_artifact_sha256="",
                source_handoff_mode="",
                event_time="2026-06-02T10:00:00+08:00",
                processed_time="2026-06-02T10:01:00+08:00",
                delivered_time="2026-06-02T10:01:01+08:00",
                verified_time="2026-06-02T10:01:05+08:00",
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "knowledge-base" / "digests" / "2026-06-02-worker-curator-deepsea-summary.md").write_text(
            "\n".join([
                "# worker_curator 今日摘要片段（2026-06-02）",
                "",
                "- 来源 artifact: `/srv/ai/projects/todo002-study-coach/knowledge-base/curated/2026-06-02/deepsea-curation-2026-06-02.md`",
                "- 来源 artifact SHA256: `sha256_stale_digest_source`",
                "",
                "## Manager Takeover Note",
                "",
                "- provider 503",
            ]),
            encoding="utf-8",
        )

        receipt = cockpit_sync._todo002_digest_receipt(
            remote, now=datetime(2026, 6, 2, 10, 5, tzinfo=cockpit_sync._CST))
        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 2, 10, 5, tzinfo=cockpit_sync._CST))

    assert receipt["source_artifact_sha256"] == "sha256_stale_digest_source"
    assert receipt["source_artifact_current_sha256"] != "sha256_stale_digest_source"
    assert any("source artifact 已变化" in issue for issue in health["issues"])


def test_snapshot_health_surfaces_todo002_deepsea_freeze_reason():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "todo002-study-coach" / "state" / "remote-teams" / "todo002_cloud"
        (remote / "state").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "todo002_cloud",
            "label": "TODO002 云上",
            "fetched_at": "2026-06-02 18:56 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "deepsea-freeze.json").write_text(json.dumps({
            "frozen": True,
            "reason": "platform risk control freeze",
        }, ensure_ascii=False), encoding="utf-8")

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 2, 18, 56, tzinfo=cockpit_sync._CST))
        source = cockpit_sync._remote_fact_source(
            remote, now=datetime(2026, 6, 2, 18, 56, tzinfo=cockpit_sync._CST))
        layers = cockpit_sync._remote_health_layers(
            remote, now=datetime(2026, 6, 2, 18, 56, tzinfo=cockpit_sync._CST))

    assert any("DeepSea automation frozen" in issue for issue in health["issues"])
    assert any("platform risk control freeze" in issue for issue in health["issues"])
    assert "todo002_deepsea_freeze=" in source
    assert "todo002_deepsea_freeze_reason=platform risk control freeze" in source
    assert layers["scheduler"] == "yellow"


def test_snapshot_health_marks_traffic_brief_manual_receipt_as_not_natural():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "traffic-ops-team" / "state" / "remote-teams" / "traffic_ops_cloud"
        (remote / "state" / "traffic-brief").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "traffic_ops_cloud",
            "label": "Traffic Ops 云上",
            "fetched_at": "2026-06-01 23:05 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "traffic-brief" / "2026-06-01.receipt.json").write_text(
            json.dumps(_traffic_brief_receipt(
                schedule_source="manual",
                schedule_command="traffic-brief",
                delivery_mode="manual",
                processed_time="2026-06-01T23:01:27+08:00",
                verified_time="2026-06-01T23:01:27+08:00",
                delivery_delay_seconds=18087,
                delivery_delay_minutes=301,
            ), ensure_ascii=False),
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 1, 23, 10, tzinfo=cockpit_sync._CST))
        layers = cockpit_sync._remote_health_layers(
            remote, now=datetime(2026, 6, 1, 23, 10, tzinfo=cockpit_sync._CST))

    assert health["ok"] is False
    assert health["warn"] >= 1
    assert any("Traffic Brief 最新 receipt 不是自然准点发送" in issue for issue in health["issues"])
    assert any("延迟 301 分钟" in issue for issue in health["issues"])
    assert layers["scheduler"] == "yellow"


def test_snapshot_health_treats_traffic_ops_standby_tmux_gaps_as_nonfatal():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "traffic-ops-team" / "state" / "remote-teams" / "traffic_ops_cloud"
        (remote / "state" / "traffic-brief").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "traffic_ops_cloud",
            "label": "Traffic Ops 云上",
            "fetched_at": "2026-06-02 00:02 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "health.json").write_text(json.dumps({
            "ok": False,
            "bad": 1,
            "warn": 11,
            "lines": [
                "config:",
                "  ✅ team config: 9 agent(s)",
                "  ✅ chat_id: oc_cloud",
                "  ✅ lark_profile: traffic-ops",
                "",
                "tmux:",
                "  ❌ tmux session traffic-ops-team not running (run `claudeteam start`)",
                "  ⚠️   manager: session down, skip  ♥ 2h ago",
                "",
                "daemons:",
                "  ⚠️ router: no pid file (not running?)",
                "  ⚠️ watchdog: no pid file (not running?)",
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "traffic-brief" / "2026-06-01.receipt.json").write_text(
            json.dumps(_traffic_brief_receipt(
                schedule_source="manual",
                schedule_command="traffic-brief",
                delivery_mode="manual",
                listener_mode="standby",
                processed_time="2026-06-01T23:01:27+08:00",
                verified_time="2026-06-01T23:01:27+08:00",
                delivery_delay_seconds=18087,
                delivery_delay_minutes=301,
            ), ensure_ascii=False),
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 2, 0, 2, tzinfo=cockpit_sync._CST))
        layers = cockpit_sync._remote_health_layers(
            remote, now=datetime(2026, 6, 2, 0, 2, tzinfo=cockpit_sync._CST))

    assert not any("tmux session traffic-ops-team not running" in issue for issue in health["issues"])
    assert not any("session down, skip" in issue for issue in health["issues"])
    assert any("Traffic Brief 最新 receipt 不是自然准点发送" in issue for issue in health["issues"])
    assert layers["process"] == "grey"
    assert layers["cli"] == "grey"
    assert layers["router"] == "grey"
    assert layers["scheduler"] == "yellow"


def test_snapshot_health_flags_missing_or_empty_cron_log_as_evidence_gap():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "traffic-ops-team" / "state" / "remote-teams" / "traffic_ops_cloud"
        (remote / "state" / "traffic-brief").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "traffic_ops_cloud",
            "label": "Traffic Ops 云上",
            "fetched_at": "2026-06-02 00:02 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "health.json").write_text(json.dumps({
            "ok": True,
            "bad": 0,
            "warn": 0,
            "lines": [
                "standby:",
                "  ℹ️ standby mode: tmux/router/watchdog not required for scheduled traffic-brief",
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "logs").mkdir(parents=True)
        (remote / "logs" / "cloud-cron.log").write_text("", encoding="utf-8")
        (remote / "state" / "traffic-brief" / "2026-06-01.receipt.json").write_text(
            json.dumps(_traffic_brief_receipt(
                schedule_source="manual",
                schedule_command="traffic-brief",
                delivery_mode="manual",
                listener_mode="standby",
                processed_time="2026-06-01T23:01:27+08:00",
                verified_time="2026-06-01T23:01:27+08:00",
                delivery_delay_seconds=18087,
                delivery_delay_minutes=301,
            ), ensure_ascii=False),
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 2, 0, 2, tzinfo=cockpit_sync._CST))

    assert any("cron log 为空" in issue for issue in health["issues"])


def test_snapshot_health_prepends_boss_readable_layer_summary():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "builder-daily").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-06-01 09:00 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "health.json").write_text(json.dumps({
            "ok": False,
            "bad": 1,
            "warn": 2,
            "warn_categories": {"agent_runtime": 1, "feishu_config": 1},
            "lines": [
                "config:",
                "  ✅ team config: 2 agent(s)",
                "  ⚠️ lark_profile blank — bot identity required for sends",
                "",
                "tmux:",
                "  ✅ tmux session: product-lab-cloud",
                "  ⚠️ worker_research: pane up but CLI not ready yet",
                "",
                "daemons:",
                "  ❌ router: pid file present but process dead",
                "",
                "router state:",
                "  ⚠️ router cursor: empty",
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(), ensure_ascii=False),
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 1, 9, 1, tzinfo=cockpit_sync._CST))

    assert health["issues"][0].startswith("⚠️ 云上健康分层:")
    assert "process=绿" in health["issues"][0]
    assert "cli=黄" in health["issues"][0]
    assert "router=红" in health["issues"][0]
    assert "deliver=黄" in health["issues"][0]
    assert "scheduler=黄" in health["issues"][0]


def test_remote_fact_source_includes_health_layer_summary():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "builder-daily").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-06-01 09:00 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "health.json").write_text(json.dumps({
            "ok": True,
            "bad": 0,
            "warn": 0,
            "lines": [
                "config:",
                "  ✅ team config: 2 agent(s)",
                "  ✅ chat_id: oc_cloud",
                "  ✅ lark_profile: cloud",
                "",
                "tmux:",
                "  ✅ tmux session: product-lab-cloud",
                "  ✅ worker_research: pane ready (claude-code)",
                "",
                "daemons:",
                "  ✅ router: alive (123)",
                "  ✅ watchdog: alive (456)",
                "",
                "router state:",
                "  ✅ router cursor: om_xxx",
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(), ensure_ascii=False),
            encoding="utf-8",
        )
        (remote / "state" / "builder-daily" / "2026-05-31.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(
                report_date="2026-05-31",
                date="2026-05-31",
                receipt_id="builder-daily:2026-05-31:worker_research",
                markdown_file="docs/builder-daily/2026-05-31.md",
                message_id="om_builder_daily_prev",
                sent_at="2026-05-31T08:31:00+08:00",
                event_time="2026-05-31T08:30:00+08:00",
                processed_time="2026-05-31T08:30:20+08:00",
                delivered_time="2026-05-31T08:31:00+08:00",
                verified_time="2026-05-31T08:31:10+08:00",
            ), ensure_ascii=False),
            encoding="utf-8",
        )

        source = cockpit_sync._remote_fact_source(
            remote, now=datetime(2026, 6, 1, 9, 1, tzinfo=cockpit_sync._CST))

    assert "health_layers=process:green,cli:green,router:green,deliver:green,scheduler:green" in source
    assert "builder_daily_continuity_status=passed" in source
    assert "builder_daily_consecutive_natural_days=2" in source


def test_remote_fact_source_includes_scheduler_history_summary():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        history_dir = remote / "state" / "scheduler" / "history" / "2026-06-01"
        history_dir.mkdir(parents=True)
        (history_dir / "2026-06-01T08-30-00-08-00-builder-daily-dispatch.json").write_text(
            json.dumps({
                "schema_version": "scheduler_history/v1",
                "history_id": "2026-06-01T08-30-00-08-00-builder-daily-dispatch",
                "command": "builder-daily-dispatch",
                "source": "cron",
                "trigger_source": "cron",
                "started_at": "2026-06-01T08:30:00+08:00",
                "event_time": "2026-06-01T08:30:00+08:00",
                "finished_at": "2026-06-01T08:31:00+08:00",
                "processed_time": "2026-06-01T08:31:00+08:00",
                "verified_time": "2026-06-01T08:31:00+08:00",
                "exit_code": 0,
                "recorded_at": "2026-06-01T08:31:00+08:00",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        source = cockpit_sync._remote_fact_source(
            remote, now=datetime(2026, 6, 1, 9, 1, tzinfo=cockpit_sync._CST))

    assert "scheduler_history_dir=" in source
    assert "scheduler_history_today_count=1" in source
    assert "scheduler_history_latest=builder-daily-dispatch:cron:2026-06-01T08:30:00+08:00" in source


def test_remote_fact_source_includes_local_cron_log_path_and_size():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "logs").mkdir(parents=True)
        cron_log = remote / "logs" / "cloud-cron.log"
        cron_log.write_text("[cloud-schedule] 2026-06-01 08:30:00 dispatch builder-daily\n", encoding="utf-8")
        size = cron_log.stat().st_size

        source = cockpit_sync._remote_fact_source(
            remote, now=datetime(2026, 6, 1, 9, 1, tzinfo=cockpit_sync._CST))

    assert f"cron_log={cron_log}" in source
    assert f"cron_log_size_bytes={size}" in source


def test_remote_fact_source_includes_todo002_digest_and_traffic_brief_details():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        todo_remote = root / "todo002-study-coach" / "state" / "remote-teams" / "todo002_cloud"
        (todo_remote / "state" / "digest-delivery").mkdir(parents=True)
        (todo_remote / "state" / "digest-delivery" / "2026-05-31.receipt.json").write_text(
            json.dumps(_todo002_digest_receipt(
                report_date="2026-05-31",
                date="2026-05-31",
                receipt_id="digest-delivery:2026-05-31:manager",
                digest_file="/srv/ai/projects/todo002-study-coach/knowledge-base/digests/2026-05-31-worker-curator-deepsea-summary.md",
                message_id="om_todo002_digest_prev",
                event_time="2026-05-31T10:00:00+08:00",
                processed_time="2026-05-31T10:01:00+08:00",
                delivered_time="2026-05-31T10:01:01+08:00",
                verified_time="2026-05-31T10:01:05+08:00",
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (todo_remote / "state" / "digest-delivery" / "2026-06-01.receipt.json").write_text(
            json.dumps(_todo002_digest_receipt(), ensure_ascii=False),
            encoding="utf-8",
        )
        traffic_remote = root / "traffic-ops-team" / "state" / "remote-teams" / "traffic_ops_cloud"
        (traffic_remote / "state" / "traffic-brief").mkdir(parents=True)
        (traffic_remote / "state" / "traffic-brief" / "2026-05-31.receipt.json").write_text(
            json.dumps(_traffic_brief_receipt(
                report_date="2026-05-31",
                receipt_id="traffic-brief:2026-05-31:worker_ops",
                artifact_file="/srv/ai/projects/traffic-ops-team/state/traffic-ledger/boss-brief-2026-05-31.md",
                boss_view_file="/srv/ai/projects/traffic-ops-team/artifacts/traffic/boss-comms/2026-05-31-status-card.md",
                event_time="2026-05-31T18:00:00+08:00",
                processed_time="2026-05-31T18:03:00+08:00",
                verified_time="2026-05-31T18:03:00+08:00",
            ), ensure_ascii=False),
            encoding="utf-8",
        )
        (traffic_remote / "state" / "traffic-brief" / "2026-06-01.receipt.json").write_text(
            json.dumps(_traffic_brief_receipt(listener_mode="standby"), ensure_ascii=False),
            encoding="utf-8",
        )

        todo_source = cockpit_sync._remote_fact_source(
            todo_remote, now=datetime(2026, 6, 1, 9, 30, tzinfo=cockpit_sync._CST))
        traffic_source = cockpit_sync._remote_fact_source(
            traffic_remote, now=datetime(2026, 6, 1, 18, 30, tzinfo=cockpit_sync._CST))

    assert "todo002_digest_receipt=" in todo_source
    assert "todo002_digest_report_date=2026-06-01" in todo_source
    assert "todo002_digest_schedule_source=cron" in todo_source
    assert "todo002_digest_delivery_mode=scheduled" in todo_source
    assert "todo002_digest_source_handoff_mode=worker_handoff" in todo_source
    assert "todo002_digest_delivery_delay_minutes=1" in todo_source
    assert "todo002_digest_continuity_status=failed" in todo_source
    assert "todo002_digest_consecutive_natural_days=1" in todo_source
    assert "traffic_brief_receipt=" in traffic_source
    assert "traffic_brief_report_date=2026-06-01" in traffic_source
    assert "traffic_brief_schedule_source=cron" in traffic_source
    assert "traffic_brief_delivery_mode=scheduled" in traffic_source
    assert "traffic_brief_listener_mode=standby" in traffic_source
    assert "traffic_brief_delivery_delay_minutes=3" in traffic_source
    assert "traffic_brief_continuity_status=passed" in traffic_source
    assert "traffic_brief_consecutive_natural_days=2" in traffic_source


def test_append_remote_receipt_fact_parts_accepts_legacy_date_alias():
    parts: list[str] = []
    receipt = _todo002_digest_receipt()
    receipt.pop("report_date", None)
    receipt["_path"] = "/tmp/todo002.receipt.json"

    cockpit_sync._append_remote_receipt_fact_parts(parts, "todo002_digest", receipt)

    assert "todo002_digest_report_date=2026-06-01" in parts


def test_snapshot_health_keeps_scheduler_yellow_when_latest_builder_daily_is_natural_but_continuity_unproven():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "builder-daily").mkdir(parents=True)
        (remote / "meta.json").write_text(json.dumps({
            "key": "product_lab_cloud",
            "label": "Product Lab 云上",
            "fetched_at": "2026-06-02 08:50 CST",
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "health.json").write_text(json.dumps({
            "ok": True,
            "bad": 0,
            "warn": 0,
            "lines": [
                "config:",
                "  ✅ team config: 2 agent(s)",
                "  ✅ chat_id: oc_cloud",
                "",
                "tmux:",
                "  ✅ tmux session: product-lab-cloud",
                "",
                "daemons:",
                "  ✅ router: alive (123)",
                "  ✅ watchdog: alive (456)",
                "",
                "scheduler:",
                "  ✅ cron entries: ensure-up / builder-daily / boss-todo present",
                "  ✅ cron heartbeat: builder-daily source=cron exit=0 age=5m",
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "builder-daily" / "2026-06-02.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(
                report_date="2026-06-02",
                date="2026-06-02",
                receipt_id="builder-daily:2026-06-02:worker_research",
                markdown_file="docs/builder-daily/2026-06-02.md",
                message_id="om_builder_daily_today",
                sent_at="2026-06-02T08:31:00+08:00",
                event_time="2026-06-02T08:30:00+08:00",
                processed_time="2026-06-02T08:30:20+08:00",
                delivered_time="2026-06-02T08:31:00+08:00",
                verified_time="2026-06-02T08:31:10+08:00",
            ), ensure_ascii=False),
            encoding="utf-8",
        )

        health = cockpit_sync._snapshot_health(
            remote, now=datetime(2026, 6, 2, 9, 0, tzinfo=cockpit_sync._CST))
        layers = cockpit_sync._remote_health_layers(
            remote, now=datetime(2026, 6, 2, 9, 0, tzinfo=cockpit_sync._CST))
        source = cockpit_sync._remote_fact_source(
            remote, now=datetime(2026, 6, 2, 9, 0, tzinfo=cockpit_sync._CST))

    assert health["ok"] is False
    assert health["warn"] >= 1
    assert any("连续自然样本不足: 1/2" in issue for issue in health["issues"])
    assert layers["scheduler"] == "yellow"
    assert "builder_daily_continuity_status=failed" in source
    assert "builder_daily_consecutive_natural_days=1" in source


def test_remote_health_layers_prefer_real_scheduler_section_over_builder_daily_proxy():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "product-lab" / "state" / "remote-teams" / "product_lab_cloud"
        (remote / "state" / "builder-daily").mkdir(parents=True)
        (remote / "health.json").write_text(json.dumps({
            "ok": False,
            "bad": 1,
            "warn": 0,
            "lines": [
                "scheduler:",
                "  ❌ cron heartbeat: ensure-up source=cron exit=1 age=2m",
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (remote / "state" / "builder-daily" / "2026-06-01.receipt.json").write_text(
            json.dumps(_builder_daily_receipt(), ensure_ascii=False),
            encoding="utf-8",
        )

        layers = cockpit_sync._remote_health_layers(
            remote, now=datetime(2026, 6, 1, 9, 1, tzinfo=cockpit_sync._CST))

    assert layers["scheduler"] == "red"


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


def test_task_rows_surface_open_child_task_rollup():
    with tempfile.TemporaryDirectory() as tmp:
        team = Path(tmp) / "product-lab"
        (team / "state").mkdir(parents=True)
        (team / "state" / "tasks.json").write_text(json.dumps({
            "tasks": [
                {
                    "id": "T-1",
                    "title": "parent battle",
                    "assignee": "manager",
                    "status": "进行中",
                    "created_at": 1779330000000,
                    "updated_at": 1779330400000,
                },
                {
                    "id": "T-2",
                    "title": "child one",
                    "assignee": "worker_a",
                    "status": "进行中",
                    "parent_task_id": "T-1",
                    "created_at": 1779330000000,
                    "updated_at": 1779330300000,
                },
                {
                    "id": "T-3",
                    "title": "child two",
                    "assignee": "worker_b",
                    "status": "待验收",
                    "parent_task_id": "T-1",
                    "created_at": 1779330000000,
                    "updated_at": 1779330200000,
                },
            ],
            "_meta": {"last_id": 3},
        }, ensure_ascii=False), encoding="utf-8")

        rows = {
            row["任务号"]: row
            for row in cockpit_sync.build_task_rows(team)
        }

    assert rows["T-1"]["子任务数"] == 2
    assert rows["T-1"]["未完成子任务数"] == 2
    assert rows["T-1"]["未收口原因"].startswith("子任务未收口")
    assert rows["T-2"]["父任务"] == "T-1"
    assert rows["T-3"]["父任务"] == "T-1"


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
