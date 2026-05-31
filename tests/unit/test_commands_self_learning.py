"""Tests for self-learning / boss-experience commands."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from helpers import run_cli


def test_boss_experience_audit_catches_path_only_delivery():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.md"
        bad.write_text("已完成，见 artifacts/T-1/report.md\n", encoding="utf-8")

        rc, out, _ = run_cli(["boss-experience-audit", "--json", str(bad)])

    assert rc == 1
    data = json.loads(out)
    assert data["ok"] is False
    assert data["issues"][0]["code"] == "path_only_delivery"


def test_boss_experience_audit_allows_summary_plus_index():
    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "good.md"
        good.write_text(
            "交付物：报告。\n核心结论：可继续。\n下一步：等验收。\n证据索引：artifacts/T-1/report.md\n",
            encoding="utf-8",
        )

        rc, out, _ = run_cli(["boss-experience-audit", "--json", str(good)])

    assert rc == 0
    data = json.loads(out)
    assert data["ok"] is True


def test_correction_cases_runs_historical_regressions():
    rc, out, _ = run_cli(["correction-cases", "--json"])

    assert rc == 0
    data = json.loads(out)
    assert data["total"] >= 10
    assert data["passed"] == data["total"]


def test_evolution_health_reports_medium_patch_system():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".learnings").mkdir()
        (root / ".learnings" / "LEARNINGS.md").write_text(
            "Source: user_feedback\nSource: correction\n", encoding="utf-8")
        src = root / "src" / "claudeteam" / "commands"
        src.mkdir(parents=True)
        for name, marker in {
            "say.py": "path_only_delivery",
            "mentor_request.py": "--image-caption",
            "cockpit_brief.py": "老板简报",
            "boss_experience_audit.py": "boss-experience-audit",
            "correction_cases.py": "correction-cases",
        }.items():
            (src / name).write_text(marker, encoding="utf-8")

        rc, out, _ = run_cli(["evolution-health", "--root", str(root), "--json"])

    assert rc == 0
    data = json.loads(out)
    assert data["metrics"]["correction_case_pass_rate"] == 1.0
    assert data["metrics"]["guardrails_present"] == 5
    assert data["level"] == "medium_patch_system"


def test_self_learning_commands_are_registered():
    rc, out, _ = run_cli([])

    assert rc == 0
    assert "boss-experience-audit" in out
    assert "correction-cases" in out
    assert "evolution-health" in out
