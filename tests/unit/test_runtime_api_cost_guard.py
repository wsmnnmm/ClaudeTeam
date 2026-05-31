"""Tests for runtime/api_cost_guard.py — API cost interception."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.runtime import api_cost_guard as guard


def test_detect_openai_chat_call():
    result = guard.check("curl https://api.openai.com/v1/chat/completions -d '{}'")
    assert result["action"] in ("pass", "warn", "block")
    assert result["provider"] == "openai"
    assert result["estimated_cost_usd"] == 0.02


def test_detect_anthropic_call():
    result = guard.check("curl https://api.anthropic.com/v1/messages -H 'x-api-key: ...'")
    assert result["provider"] == "anthropic"


def test_detect_deepseek_call():
    result = guard.check("curl https://api.deepseek.com/v1/chat/completions")
    assert result["provider"] == "deepseek"


def test_detect_evolink_video():
    result = guard.check("curl evolink.example.com/videos/generations")
    assert result["provider"] == "evolink-video"


def test_detect_seedance():
    result = guard.check("python -c 'import seedance; seedance.generate()'")
    assert result["provider"] == "seedance"


def test_no_match_ordinary_command():
    result = guard.check("ls -la")
    assert result["action"] == "pass"
    assert result["reason"] == "no paid API detected"


def test_no_match_empty():
    result = guard.check("")
    assert result["action"] == "pass"


def test_read_budget_defaults():
    with isolated_env():
        b = guard._read_budget()
        assert "limit_usd" in b
        assert "spent_usd" in b


def test_budget_spend_tracking():
    with isolated_env():
        guard.reset_budget(limit_usd=10.0)
        guard.record_spend(0.50)
        guard.record_spend(0.30)
        status = guard.budget_status()
        assert status["spent_usd"] == 0.80
        assert status["calls"] == 2


def test_budget_reset():
    with isolated_env():
        guard.reset_budget(limit_usd=20.0)
        guard.record_spend(5.0)
        data = guard.reset_budget(limit_usd=15.0)
        assert data["spent_usd"] == 0.0
        assert data["limit_usd"] == 15.0


def test_budget_warns_at_80_percent():
    with isolated_env():
        guard.reset_budget(limit_usd=1.0)
        guard.record_spend(0.82)  # 82% used
        result = guard.check("curl https://api.openai.com/v1/chat/completions")
        assert result["action"] == "warn"
        assert "BUDGET WARNING" in result["reason"]


def test_budget_blocks_when_exceeded():
    with isolated_env():
        guard.reset_budget(limit_usd=1.0)
        guard.record_spend(0.99)  # already 99%, next call would exceed
        result = guard.check("curl https://api.anthropic.com/v1/messages")
        assert result["action"] == "block"
        assert "BUDGET EXCEEDED" in result["reason"]


def test_budget_zero_limit_disables():
    with isolated_env():
        guard.reset_budget(limit_usd=0.0)
        result = guard.check("curl https://api.openai.com/v1/chat/completions")
        # Zero limit means no cap
        assert result["action"] != "block"
