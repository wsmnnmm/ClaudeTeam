"""Moonshot Kimi Code adapter."""
from __future__ import annotations

from .base import CliAdapter


class KimiCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # model is currently a no-op for kimi; CLI picks per its config
        return f"DISABLE_UPDATE_CHECK=1 KIMI_AGENT={agent} kimi --yolo"

    def ready_markers(self) -> list[str]:
        return [
            "Welcome to Kimi Code CLI",
            "Send /help for help information",
            "── input",
            "context:",
        ]

    def busy_markers(self) -> list[str]:
        return [
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",
            "Thinking", "Using Shell", "Booting",
        ]

    def process_name(self) -> str:
        return "kimi"

    def submit_keys(self) -> list[str]:
        # Same multi-line input contract as Codex: M-Enter to submit.
        return ["M-Enter", "Enter", "C-m", "C-j"]

    def rate_limit_markers(self) -> list[str]:
        return ["rate limit", "429", "quota exceeded"]
