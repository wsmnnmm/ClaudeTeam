"""Tests for the top-level claudeteam CLI dispatcher."""
from __future__ import annotations

from helpers import run_cli
from claudeteam import cli


def test_no_args_prints_usage_and_returns_zero():
    rc, out, _ = run_cli([])
    assert rc == 0
    assert "usage: claudeteam" in out


def test_help_prints_usage():
    rc, out, _ = run_cli(["--help"])
    assert rc == 0
    assert "commands:" in out


def test_unknown_command_returns_one_and_writes_to_stderr():
    rc, _, err = run_cli(["__definitely_unknown__"])
    assert rc == 1
    assert "unknown command" in err


def test_registered_handler_runs_and_propagates_exit_code():
    captured = []

    def handler(argv: list[str]) -> int:
        captured.append(argv)
        return 7

    cli.COMMANDS["echo"] = handler
    try:
        rc, _, _ = run_cli(["echo", "a", "b"])
    finally:
        del cli.COMMANDS["echo"]
    assert rc == 7
    assert captured == [["a", "b"]]


def test_handler_returning_none_is_treated_as_zero():
    cli.COMMANDS["noop"] = lambda argv: None
    try:
        rc, _, _ = run_cli(["noop"])
    finally:
        del cli.COMMANDS["noop"]
    assert rc == 0
