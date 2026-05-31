"""Tests for `claudeteam fleet-health`."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from helpers import attr_patch, run_cli
from claudeteam.commands import fleet_health as fleet_health_cmd


def _fake_payload(path):
    name = path.name
    if name == "red-team":
        return {
            "team": name,
            "path": str(path),
            "ok": False,
            "bad": 1,
            "warn": 0,
            "issues": ["❌ lark-cli stuck process(es): 1"],
        }
    return {
        "team": name,
        "path": str(path),
        "ok": True,
        "bad": 0,
        "warn": 1,
        "issues": ["⚠️ manager heartbeat is stale"],
    }


def test_fleet_health_text_summarizes_teams():
    with attr_patch(fleet_health_cmd, _health_payload=_fake_payload):
        rc, out, _ = run_cli(["fleet-health", "/tmp/red-team", "/tmp/yellow-team"])
    assert rc == 1
    assert "fleet health: 2 team(s)" in out
    assert "RED    red-team" in out
    assert "YELLOW yellow-team" in out
    assert "summary: green=0, yellow=1, red=1" in out


def test_fleet_health_json_emits_machine_readable_rows():
    with attr_patch(fleet_health_cmd, _health_payload=_fake_payload):
        rc, out, _ = run_cli(["fleet-health", "--json", "/tmp/yellow-team"])
    assert rc == 0
    data = json.loads(out)
    assert data["ok"] is True
    assert data["teams"][0]["team"] == "yellow-team"
    assert data["teams"][0]["warn"] == 1


def test_fleet_health_discovers_claudeteam_toml_under_root():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "claudeteam.toml").write_text("chat_id = 'x'\n")
        (tmp_path / "b").mkdir()

        seen = []

        def fake(path):
            seen.append(path.name)
            return {
                "team": path.name,
                "path": str(path),
                "ok": True,
                "bad": 0,
                "warn": 0,
                "issues": [],
            }

        with attr_patch(fleet_health_cmd, _health_payload=fake):
            rc, out, _ = run_cli(["fleet-health", "--root", str(tmp_path)])
        assert rc == 0
        assert seen == ["a"]
        assert "GREEN  a" in out


def test_fleet_health_help_and_top_level_usage():
    rc, out, _ = run_cli(["fleet-health", "--help"])
    assert rc == 0
    assert "usage: claudeteam fleet-health" in out
    rc, out, _ = run_cli([])
    assert rc == 0
    assert "fleet-health" in out


def test_fleet_health_report_dir_writes_c1_files():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "runtime-health"
        with attr_patch(fleet_health_cmd, _health_payload=_fake_payload):
            rc, out, _ = run_cli([
                "fleet-health",
                "--report-dir", str(out_dir),
                "/tmp/red-team",
                "/tmp/yellow-team",
            ])
        assert rc == 1
        assert "reports:" in out
        expected = {
            "fleet-status.md",
            "daily-boss-brief.md",
            "night-shift-plan.md",
            "dashboard.html",
        }
        assert {path.name for path in out_dir.iterdir()} == expected
        brief = (out_dir / "daily-boss-brief.md").read_text(encoding="utf-8")
        assert "不发飞书" in brief
        assert "RED red-team" in brief


def test_task_gate_flags_done_task_with_missing_artifact():
    with tempfile.TemporaryDirectory() as tmp:
        team_dir = Path(tmp) / "team"
        state_dir = team_dir / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "tasks.json").write_text(json.dumps({
            "tasks": [
                {"id": "T-1", "status": "已完成", "artifact_path": ""},
                {"id": "T-2", "status": "待验收", "artifact_path": "artifacts/T-2/out.md"},
                {"id": "T-3", "status": "进行中", "artifact_path": ""},
            ],
        }), encoding="utf-8")

        issues = fleet_health_cmd._task_gate_issues(team_dir)

    assert "T-1 is 已完成 but artifact_path is empty" in issues[0]
    assert "T-2 is 待验收 but artifact is missing" in issues[1]
    assert len(issues) == 2


def test_task_gate_accepts_existing_file_and_urls():
    with tempfile.TemporaryDirectory() as tmp:
        team_dir = Path(tmp) / "team"
        artifact = team_dir / "artifacts" / "T-1" / "out.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("ok", encoding="utf-8")
        state_dir = team_dir / "state"
        state_dir.mkdir()
        (state_dir / "tasks.json").write_text(json.dumps({
            "tasks": [
                {"id": "T-1", "status": "已完成", "artifact_path": "artifacts/T-1/out.md"},
                {"id": "T-2", "status": "待验收", "artifact_path": "https://example.com/report"},
            ],
        }), encoding="utf-8")

        issues = fleet_health_cmd._task_gate_issues(team_dir)

    assert issues == []


def test_task_gate_flags_ui_done_task_without_screenshot_preview_bundle():
    with tempfile.TemporaryDirectory() as tmp:
        team_dir = Path(tmp) / "team"
        artifact = team_dir / "artifacts" / "T-1" / "report.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            "# 页面还原交付\n\n"
            "Preview: http://localhost:5173/#/dashboard?visualPreview=1\n",
            encoding="utf-8",
        )
        state_dir = team_dir / "state"
        state_dir.mkdir()
        (state_dir / "tasks.json").write_text(json.dumps({
            "tasks": [
                {
                    "id": "T-1",
                    "title": "页面还原 /dashboard",
                    "description": "",
                    "status": "已完成",
                    "artifact_path": "artifacts/T-1/report.md",
                },
            ],
        }), encoding="utf-8")

        issues = fleet_health_cmd._task_gate_issues(team_dir)

    assert len(issues) == 1
    assert "UI/page restoration" in issues[0]
    assert "screenshot image" in issues[0]


def test_task_gate_accepts_ui_report_with_screenshot_and_preview():
    with tempfile.TemporaryDirectory() as tmp:
        team_dir = Path(tmp) / "team"
        artifact = team_dir / "artifacts" / "T-1" / "report.md"
        image = artifact.parent / "shot.png"
        artifact.parent.mkdir(parents=True)
        image.write_bytes(b"fake image")
        artifact.write_text(
            "# 页面还原交付\n\n"
            "Preview: http://localhost:5173/#/dashboard?visualPreview=1\n"
            "![screenshot](shot.png)\n",
            encoding="utf-8",
        )
        state_dir = team_dir / "state"
        state_dir.mkdir()
        (state_dir / "tasks.json").write_text(json.dumps({
            "tasks": [
                {
                    "id": "T-1",
                    "title": "页面还原 /dashboard",
                    "description": "",
                    "status": "待验收",
                    "artifact_path": "artifacts/T-1/report.md",
                },
            ],
        }), encoding="utf-8")

        issues = fleet_health_cmd._task_gate_issues(team_dir)

    assert issues == []
