"""Tests for runtime/team_registry.py."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from claudeteam.runtime import team_registry


class _Proc:
    def __init__(self, *, returncode: int = 0, stdout: str = ""):
        self.returncode = returncode
        self.stdout = stdout


def test_default_script_finds_product_lab_registry():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        script = root / "product-lab" / "scripts" / "team-registry.py"
        script.parent.mkdir(parents=True)
        script.write_text("print('x')\n", encoding="utf-8")
        assert team_registry.default_script(root) == script


def test_load_returns_team_rows_from_json_script():
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "registry.py"
        script.write_text("ignored\n", encoding="utf-8")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _Proc(stdout=json.dumps({
                "teams": [{"label": "Product Lab 云上"}, "bad"],
            }, ensure_ascii=False))

        rows = team_registry.load(script, run=fake_run)
    assert rows == [{"label": "Product Lab 云上"}]
    assert calls[0][-1] == "--json"


def test_load_degrades_to_empty_on_failure_or_missing_script():
    assert team_registry.load(None) == []

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "registry.py"
        script.write_text("ignored\n", encoding="utf-8")

        def bad_run(cmd, **kwargs):
            return _Proc(returncode=1, stdout="{}")

        assert team_registry.load(script, run=bad_run) == []
