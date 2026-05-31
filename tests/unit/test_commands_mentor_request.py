"""Tests for `claudeteam mentor-request`."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from helpers import isolated_env, run_cli


def _team():
    return {"session": "WebsiteChuhai", "agents": {"manager": {"cli": "codex-cli"}}}


def _json(out: str) -> dict:
    return json.loads(out)


def test_mentor_request_dry_run_creates_package():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "requests"
        with isolated_env(team=_team()):
            rc, out, err = run_cli([
                "mentor-request",
                "--mentor", "liu",
                "--topic", "AI只发路径",
                "--out-dir", str(out_dir),
                "--dry-run",
                "老板说：AI 员工只发 artifacts 路径，用起来别扭。",
            ])
        assert rc == 0, err
        data = _json(out)
        assert data["ok"] is True
        assert data["mentors"] == "liu"
        request = json.loads(Path(data["packageDir"], "request.json").read_text(encoding="utf-8"))
        assert request["sourceDir"].endswith("/Users/wsm/Project/website-chuhai-team")
        brief = Path(data["brief"])
        text = brief.read_text(encoding="utf-8")
        assert "Target mentors: AI 刘小排" in text
        assert "AI 员工只发 artifacts 路径" in text
        assert "如果图片说明与图片内容不一致" in text
        assert "mentor-loop-return.cjs --run-dir <loop-run-dir>" in text


def test_mentor_request_auto_routes_yiren_from_text():
    with tempfile.TemporaryDirectory() as tmp:
        with isolated_env(team=_team()):
            rc, out, err = run_cli([
                "mentor-request",
                "--topic", "是否继续做",
                "--out-dir", tmp,
                "--dry-run",
                "问一下亦仁：这个产品是否应该暂停？",
            ])
        assert rc == 0, err
        data = _json(out)
        assert data["mentors"] == "yiren"
        assert "AI 亦仁" in Path(data["brief"]).read_text(encoding="utf-8")


def test_mentor_request_auto_routes_both_when_boss_names_both():
    with tempfile.TemporaryDirectory() as tmp:
        with isolated_env(team=_team()):
            rc, out, err = run_cli([
                "mentor-request",
                "--topic", "双导师判断",
                "--out-dir", tmp,
                "--dry-run",
                "老板说：刘小排和亦仁都问一下。",
            ])
        assert rc == 0, err
        data = _json(out)
        assert data["mentors"] == "liu,yiren"
        text = Path(data["brief"]).read_text(encoding="utf-8")
        assert "AI 刘小排 / AI 亦仁" in text


def test_mentor_request_requires_image_caption():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        image = root / "shot.png"
        image.write_bytes(b"not really png")
        with isolated_env(team=_team()):
            rc, _, err = run_cli([
                "mentor-request",
                "--topic", "截图问题",
                "--image", str(image),
                "--out-dir", str(root / "out"),
                "--dry-run",
                "请导师看截图。",
            ])
    assert rc == 1
    assert "matching --image-caption" in err


def test_mentor_request_copies_captioned_image():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        image = root / "shot.png"
        image.write_bytes(b"fake")
        with isolated_env(team=_team()):
            rc, out, err = run_cli([
                "mentor-request",
                "--topic", "截图问题",
                "--image", str(image),
                "--image-caption", "应该显示飞书群里 manager 只发了 artifacts 路径。",
                "--out-dir", str(root / "out"),
                "--dry-run",
                "请导师看截图。",
            ])
        assert rc == 0, err
        data = _json(out)
        package = Path(data["packageDir"])
        copied = list((package / "images").glob("*.png"))
        assert len(copied) == 1
        text = Path(data["brief"]).read_text(encoding="utf-8")
        assert "应该显示飞书群里 manager 只发了 artifacts 路径" in text
        assert "包内相对路径：images/01-shot.png" in text
        assert "传给导师时必须作为图片附件上传" in text
        assert "图片附件：" in data["delivery"]["message"]
        assert f"{package}/images/01-shot.png" in data["delivery"]["message"]
        assert "--image 实际上传给导师" in data["delivery"]["message"]
        assert "scripts/mentor-loop-return.cjs --run-dir <loop-run-dir>" in data["delivery"]["message"]


def test_mentor_request_records_traffic_ops_source_dir():
    team = {"session": "traffic-ops-team", "agents": {"manager": {"cli": "codex-cli"}}}
    with tempfile.TemporaryDirectory() as tmp:
        with isolated_env(team=team):
            rc, out, err = run_cli([
                "mentor-request",
                "--mentor", "yiren",
                "--topic", "深圈活动获客帖v4复核",
                "--out-dir", tmp,
                "--dry-run",
                "请单独新开对话问亦仁。",
            ])
        assert rc == 0, err
        data = _json(out)
        request = json.loads(Path(data["packageDir"], "request.json").read_text(encoding="utf-8"))
        assert request["sourceTeam"] == "traffic-ops-team"
        assert request["sourceDir"] == "/Users/wsm/Project/traffic-ops-team"
        assert "--source-team traffic-ops-team --source-dir /Users/wsm/Project/traffic-ops-team" in data["delivery"]["message"]
