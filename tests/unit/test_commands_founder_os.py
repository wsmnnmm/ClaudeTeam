"""Tests for `claudeteam founder-os`."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from helpers import run_cli


def test_founder_os_default_prints_all_stage_gates():
    rc, out, err = run_cli(["founder-os"])
    assert rc == 0, err
    assert "Founder OS v1" in out
    assert "Idea / 创意验证" in out
    assert "MVP / 最小可行" in out
    assert "Launch / 上线增长" in out
    assert "Scale / 规模扩张" in out
    assert "今天哪一个动作" in out
    assert "Product Lab" in out


def test_founder_os_stage_filter_focuses_output():
    rc, out, err = run_cli(["founder-os", "--stage", "mvp"])
    assert rc == 0, err
    assert "MVP / 最小可行" in out
    assert "CLAUDE.md" in out
    assert "PMF" in out
    assert "Idea / 创意验证" not in out
    assert "Scale / 规模扩张" not in out


def test_founder_os_json_is_machine_readable():
    rc, out, err = run_cli(["founder-os", "--json"])
    assert rc == 0, err
    data = json.loads(out)
    assert data["rule"].startswith("No stage")
    assert [stage["id"] for stage in data["stages"]] == [
        "idea", "mvp", "launch", "scale",
    ]
    assert "阶段" in data["cockpit_fields"]
    assert any("ClaudeTeam" in row for row in data["team_roles"])


def test_founder_os_rejects_unknown_stage():
    rc, _, err = run_cli(["founder-os", "--stage", "unknown"])
    assert rc == 1
    assert "unknown stage" in err


def _audit_team(root: Path, name: str, task: dict) -> Path:
    team = root / name
    (team / "state").mkdir(parents=True)
    (team / "claudeteam.toml").write_text("chat_id = 'oc_x'\n", encoding="utf-8")
    (team / "state" / "tasks.json").write_text(json.dumps({
        "tasks": [task],
        "_meta": {"last_id": 1},
    }, ensure_ascii=False), encoding="utf-8")
    return team


def test_founder_os_audit_reports_missing_task_fields():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _audit_team(root, "work-team", {
            "id": "T-1",
            "title": "排查真实 bug",
            "assignee": "worker_frontend",
            "status": "进行中",
        })
        rc, out, err = run_cli(["founder-os", "--audit-root", str(root), "--json"])
    assert rc == 1
    assert err == ""
    data = json.loads(out)
    assert data["ok"] is False
    assert data["missing_open_tasks"] == 1
    row = data["missing"][0]
    assert row["task_id"] == "T-1"
    assert row["missing_fields"] == [
        "当前阶段", "阶段出口证据", "今天最小证据动作", "不做什么",
    ]


def test_founder_os_audit_passes_when_open_tasks_have_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _audit_team(root, "product-team", {
            "id": "T-1",
            "title": "验证付款意愿",
            "assignee": "manager",
            "status": "进行中",
            "founder_stage": "mvp",
            "stage_exit_evidence": "1 个真实用户愿意试用并回来",
            "evidence_action": "今天邀请 1 个用户试用核心流程",
            "non_goal": "不扩展设置页",
        })
        rc, out, err = run_cli(["founder-os", "--audit-root", str(root)])
    assert rc == 0, err
    assert "missing=0" in out


def test_founder_os_audit_includes_registry_only_sources():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _audit_team(root, "product-team", {
            "id": "T-1",
            "title": "验证付款意愿",
            "assignee": "manager",
            "status": "进行中",
            "founder_stage": "mvp",
            "stage_exit_evidence": "1 个真实用户愿意试用并回来",
            "evidence_action": "今天邀请 1 个用户试用核心流程",
            "non_goal": "不扩展设置页",
        })
        registry = root / "registry.py"
        registry.write_text(
            "import json\n"
            "print(json.dumps({'teams': [\n"
            "  {'key': 'product_lab_cloud', 'label': 'Product Lab 云上',"
            "   'status': '需云机核验', 'chat_id': 'oc_cloud',"
            "   'config_path': '/srv/ai/projects/product-lab/ops.toml'},\n"
            "  {'key': 'smart_partner', 'label': '智能伙伴',"
            "   'status': '需接入驾驶舱', 'chat_id': 'oc_partner'}\n"
            "]}, ensure_ascii=False))\n",
            encoding="utf-8",
        )
        rc, out, err = run_cli([
            "founder-os", "--audit-root", str(root),
            "--registry-script", str(registry), "--json",
        ])
    assert rc == 1
    assert err == ""
    data = json.loads(out)
    assert data["external_sources"]
    labels = {row["label"] for row in data["external_sources"]}
    assert labels == {"Product Lab 云上", "智能伙伴"}
    assert data["external_sources"][0]["audit_status"] in {
        "待核验", "需要老板动作"
    }


def test_founder_os_audit_rejects_stage_filter_mix():
    rc, _, err = run_cli(["founder-os", "--audit-root", "/tmp", "--stage", "mvp"])
    assert rc == 1
    assert "--stage cannot be used with --audit-root" in err


def test_founder_os_help_and_top_level_registration():
    rc, out, _ = run_cli(["founder-os", "--help"])
    assert rc == 0
    assert "usage: claudeteam founder-os" in out
    rc, out, _ = run_cli([])
    assert rc == 0
    assert "founder-os" in out
