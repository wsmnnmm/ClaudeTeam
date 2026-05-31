"""Tests for Feishu-visible text cleanup."""
from __future__ import annotations

from claudeteam.feishu.text import normalize_visible_escapes, sanitize_card_payload


def test_normalize_visible_escapes_decodes_layout_sequences():
    assert normalize_visible_escapes(r"汇总：\n- 一\t完成") == "汇总：\n- 一\t完成"


def test_normalize_visible_escapes_keeps_path_like_backslashes():
    raw = r"路径 C:\new\test 和 /tmp/\n/cache 保持原样"
    assert normalize_visible_escapes(raw) == raw


def test_normalize_visible_escapes_unwraps_accidental_json_string():
    raw = r'"今日主线\n1. 找真实对象\n2. 推试用"'
    assert normalize_visible_escapes(raw) == "今日主线\n1. 找真实对象\n2. 推试用"


def test_normalize_visible_escapes_unwraps_escaped_quotes():
    raw = r'"老板说：\"只看下一步\"\n收到"'
    assert normalize_visible_escapes(raw) == '老板说："只看下一步"\n收到'


def test_sanitize_card_payload_recurses_strings_only():
    card = {
        "header": {"title": {"content": r'"标题\n副标题"'}},
        "body": {"elements": [{"tag": "markdown", "content": r"A\nB"}]},
        "number": 3,
    }
    cleaned = sanitize_card_payload(card)
    assert cleaned["header"]["title"]["content"] == "标题\n副标题"
    assert cleaned["body"]["elements"][0]["content"] == "A\nB"
    assert cleaned["number"] == 3
