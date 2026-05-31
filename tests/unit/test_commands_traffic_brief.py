"""Tests for `claudeteam traffic-brief`."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from helpers import run_cli


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_traffic_brief_empty_ledger_is_human_readable():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "traffic-ledger.jsonl"
        rc, out, err = run_cli([
            "traffic-brief", "--ledger", str(ledger), "--today", "2026-05-24",
        ])

    assert rc == 0
    assert err == ""
    assert "今天还没有流量数据" in out
    assert "AI 数据助理只做记录" in out


def test_traffic_brief_sums_today_and_surfaces_anomalies():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "traffic-ledger.jsonl"
        _append_jsonl(ledger, [
            {
                "date": "2026-05-23",
                "platform": "小红书",
                "content": "旧内容",
                "views": 88,
                "comments": 1,
            },
            {
                "date": "2026-05-24",
                "platform": "小红书",
                "content": "深圳 AI 编程局",
                "views": 230,
                "comments": 0,
                "private_messages": 2,
                "add_wechat": 0,
                "effective_leads": 1,
            },
        ])
        rc, out, _ = run_cli([
            "traffic-brief", "--ledger", str(ledger), "--today", "2026-05-24",
        ])

    assert rc == 0
    assert "有效线索 1 / 加微信 0 / 私信 2 / 评论 0 / 浏览 230" in out
    assert "浏览不低但评论为 0" in out
    assert "有私信但未加微" in out


def test_traffic_brief_json_and_out_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "traffic-ledger.jsonl"
        out_file = root / "traffic-brief.md"
        _append_jsonl(ledger, [{
            "date": "2026-05-24",
            "platform": "抖音",
            "content": "AI 学习搭子",
            "views": 100,
            "comments": 3,
            "private_messages": 1,
            "add_wechat": 1,
            "effective_leads": 1,
        }])
        rc, out, _ = run_cli([
            "traffic-brief", "--ledger", str(ledger), "--today", "2026-05-24",
            "--json", "--out", str(out_file),
        ])
        data = json.loads(out)
        written = json.loads(out_file.read_text(encoding="utf-8"))

    assert rc == 0
    assert data["totals"]["views"] == 100
    assert written["today_count"] == 1


def test_traffic_brief_append_json_defaults_today_and_renders():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "traffic-ledger.jsonl"
        rc, out, err = run_cli([
            "traffic-brief",
            "--ledger", str(ledger),
            "--today", "2026-05-24",
            "--append-json",
            json.dumps({
                "platform": "小红书",
                "content": "深圳 AI 编程局",
                "views": 230,
                "comments": 5,
                "private_messages": 2,
                "add_wechat": 1,
                "effective_leads": 1,
            }, ensure_ascii=False),
        ])
        rows = [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8").splitlines()
        ]

    assert rc == 0
    assert err == ""
    assert rows[0]["date"] == "2026-05-24"
    assert "有效线索 1 / 加微信 1 / 私信 2 / 评论 5 / 浏览 230" in out


def test_traffic_brief_help_and_top_level_registration():
    rc, out, _ = run_cli(["traffic-brief", "--help"])
    assert rc == 0
    assert "usage: claudeteam traffic-brief" in out
    rc, out, _ = run_cli([])
    assert rc == 0
    assert "traffic-brief" in out
