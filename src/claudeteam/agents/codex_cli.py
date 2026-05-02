"""OpenAI Codex CLI adapter.

Codex only accepts OpenAI-native model names (gpt-/o1/o3/o4/codex prefixes);
other aliases (sonnet/opus/haiku) are silently dropped so Codex falls back
to its configured default.
"""
from __future__ import annotations

import shlex

from .base import CliAdapter


_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "codex")


class CodexCliAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        args = ["--dangerously-bypass-approvals-and-sandbox"]
        if model and any(model.startswith(p) for p in _OPENAI_PREFIXES):
            args += ["--model", model]
        quoted = " ".join(shlex.quote(a) for a in args)
        return f"CODEX_AGENT={shlex.quote(agent)} codex {quoted}"

    def ready_markers(self) -> list[str]:
        # Banner lines after CLI 0.124+ becomes interactive.  Avoids matching
        # the spawn-command echo that includes "gpt-5".
        return ["OpenAI Codex", "permissions: YOLO"]

    def busy_markers(self) -> list[str]:
        return [
            "esc to interrupt",
            "Booting MCP server",
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",
        ]

    def process_name(self) -> str:
        return "codex"

    def submit_keys(self) -> list[str]:
        # Ink/prompt_toolkit multi-line input: Enter inserts newline, M-Enter
        # is the canonical submit.  Keep Enter as fallback for single-line.
        return ["M-Enter", "Enter", "C-m", "C-j"]

    def rate_limit_markers(self) -> list[str]:
        return ["rate limit", "429", "RateLimitError", "you exceeded your"]
