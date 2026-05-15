"""OpenAI Codex CLI adapter.

Codex only accepts OpenAI-native model names (gpt-/o1/o3/o4/codex prefixes);
other aliases (sonnet/opus/haiku) are silently dropped so Codex falls back
to its configured default.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from .base import CliAdapter, MULTILINE_SUBMIT_KEYS, SPINNER_CHARS


def ensure_workdir_trusted(workdir: Path,
                           config_path: Path | None = None) -> None:
    """Pre-trust `workdir` in ~/.codex/config.toml so the first-run
    "Do you trust this directory?" prompt doesn't block a freshly-spawned
    pane. Idempotent: a no-op if the entry already exists.

    `config_path` is injectable for tests.
    """
    if config_path is not None:
        cfg = config_path
    else:
        codex_home = os.environ.get("CODEX_HOME", "").strip()
        if codex_home:
            cfg = Path(codex_home) / "config.toml"
        else:
            cfg = Path.home() / ".codex" / "config.toml"
    entry = f'[projects."{workdir}"]\ntrust_level = "trusted"\n'
    if cfg.exists():
        existing = cfg.read_text(encoding="utf-8")
        if f'[projects."{workdir}"]' in existing:
            return
        cfg.write_text(existing.rstrip() + "\n\n" + entry, encoding="utf-8")
    else:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(entry, encoding="utf-8")


_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "codex")


class CodexCliAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        args = ["--dangerously-bypass-approvals-and-sandbox"]
        if model and any(model.startswith(p) for p in _OPENAI_PREFIXES):
            args += ["--model", model]
        quoted = " ".join(shlex.quote(a) for a in args)
        return f"CODEX_AGENT={shlex.quote(agent)} codex {quoted}"

    def ready_markers(self) -> list[str]:
        # Banner/status lines after CLI 0.124+ becomes interactive.  The
        # reasoning-effort markers catch compact captures where only the
        # bottom status line remains, e.g. "gpt-5.5 xhigh · /work".
        return [
            " default · ",
            " low · ",
            " medium · ",
            " high · ",
            " xhigh · ",
            " max · ",
        ]

    def busy_markers(self) -> list[str]:
        return [
            "esc to interrupt",
            "Booting MCP server",
            "Starting MCP servers",
            *SPINNER_CHARS,
        ]

    def process_name(self) -> str:
        return "codex"

    def submit_keys(self) -> list[str]:
        return list(MULTILINE_SUBMIT_KEYS)

    def rate_limit_markers(self) -> list[str]:
        # Codex TUI keeps recent scrollback in-pane; marker-based preflight
        # can mistake old errors or numeric fragments for a current limit and
        # silently leave boss messages in inbox. Let Codex receive the prompt
        # and surface any real rate-limit error itself.
        return []
