"""Tests for runtime/team_command.py — team-safe shell-out wrapper."""
from __future__ import annotations

import os

from helpers import isolated_env
from claudeteam.runtime import team_command


def test_ensure_wrapper_pins_state_and_config_and_is_executable():
    with isolated_env() as tmp:
        wrapper = team_command.ensure_wrapper()
        assert wrapper == tmp / "state" / "bin" / "ct"
        assert os.access(wrapper, os.X_OK)

        text = wrapper.read_text(encoding="utf-8")
        assert f"export CLAUDETEAM_STATE_DIR={tmp / 'state'}" in text
        assert f"export CLAUDETEAM_CONFIG_FILE={tmp / 'claudeteam.toml'}" in text
        assert 'exec ' in text and '"$@"' in text

        pointer = tmp / "state" / team_command.CONFIG_POINTER_NAME
        assert pointer.read_text(encoding="utf-8").strip() == str(tmp / "claudeteam.toml")


def test_safe_cli_cmd_can_return_path_without_writing():
    with isolated_env() as tmp:
        wrapper = tmp / "state" / "bin" / "ct"
        assert team_command.safe_cli_cmd() == str(wrapper)
        assert not wrapper.exists()
