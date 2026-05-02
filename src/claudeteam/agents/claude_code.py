"""Anthropic Claude Code adapter."""
from __future__ import annotations

from .base import CliAdapter, SPINNER_CHARS


class ClaudeCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        return (
            f"IS_SANDBOX=1 claude --dangerously-skip-permissions "
            f"--model {model} --name {agent}"
        )

    def ready_markers(self) -> list[str]:
        return ["bypass permissions on", "? for shortcuts"]

    def busy_markers(self) -> list[str]:
        return [
            *SPINNER_CHARS,
            "◐", "◑", "◒", "◓",
            "Thinking", "Running tool",
        ]

    def process_name(self) -> str:
        return "claude"

    def rate_limit_markers(self) -> list[str]:
        return [
            "Approaching usage limit",
            "5-hour limit reached",
            "Try again at",
            "rate limit",
        ]
