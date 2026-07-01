"""Tests for runtime/paths.py — env-driven state directory layout."""
from __future__ import annotations

import tempfile
from pathlib import Path

from helpers import env_patch
from claudeteam.runtime import paths


def _state_env(value):
    """Sugar over env_patch; legacy callers in this file still pass a
    single positional value."""
    return env_patch(CLAUDETEAM_STATE_DIR=value)


def test_state_dir_defaults_beside_config_when_env_unset():
    """No CLAUDETEAM_STATE_DIR → state lives in `state/` next to the config
    file (config location = team identity), so two teams can't bleed into one
    shared ~/.claudeteam."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "claudeteam.toml"
        with env_patch(CLAUDETEAM_STATE_DIR=None, CLAUDETEAM_CONFIG_FILE=str(cfg)):
            assert paths.state_dir() == Path(tmp) / "state"


def test_state_dir_uses_env_when_set():
    with tempfile.TemporaryDirectory() as tmp:
        with _state_env(tmp):
            assert paths.state_dir() == Path(tmp)


def test_agent_dir_is_state_agents_subdir():
    """Per-agent state (identity.md + memory.jsonl, plus workspace/ and the
    home/ subdir) lives under agents/<name>/."""
    with tempfile.TemporaryDirectory() as tmp:
        with _state_env(tmp):
            assert paths.agent_dir("worker_cc") == Path(tmp) / "agents" / "worker_cc"


def test_agent_workspace_is_under_agent_dir():
    """Each agent's private scratch area is workspace/ inside its own dir."""
    with tempfile.TemporaryDirectory() as tmp:
        with _state_env(tmp):
            assert (paths.agent_workspace("worker_cc")
                    == Path(tmp) / "agents" / "worker_cc" / "workspace")


def test_agent_home_nests_under_agent_dir_by_default():
    """Merged layout: the CLI HOME is the home/ subdir of the agent's own
    dir, so everything for one agent lives in one tree."""
    with tempfile.TemporaryDirectory() as tmp:
        with _state_env(tmp):
            assert (paths.agent_home("worker_cc")
                    == str(Path(tmp) / "agents" / "worker_cc" / "home"))


def test_agent_home_root_env_overrides_nesting():
    """CLAUDETEAM_AGENT_HOME_ROOT relocates homes onto a separate mount
    (e.g. a Docker credential volume)."""
    with tempfile.TemporaryDirectory() as tmp, \
            tempfile.TemporaryDirectory() as homes:
        with _state_env(tmp), env_patch(CLAUDETEAM_AGENT_HOME_ROOT=homes):
            assert (paths.agent_home("worker_cc")
                    == str(Path(homes) / "worker_cc"))


def test_ensure_state_dir_creates_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        sd = Path(tmp) / "state"
        with _state_env(sd):
            assert not sd.exists()
            paths.ensure_state_dir()
            assert sd.exists()


def test_state_dir_re_reads_env_each_call():
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        with _state_env(tmp1):
            assert paths.state_dir() == Path(tmp1)
        with _state_env(tmp2):
            assert paths.state_dir() == Path(tmp2)


def test_state_dir_uses_relative_path_from_cwd_config():
    with tempfile.TemporaryDirectory() as tmp:
        team = Path(tmp) / "team-a"
        team.mkdir(parents=True)
        (team / "claudeteam.toml").write_text(
            'state_dir = "state"\n',
            encoding="utf-8",
        )
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(team)
            with env_patch(CLAUDETEAM_STATE_DIR=None, CLAUDETEAM_CONFIG_FILE=None):
                assert paths.state_dir() == (team / "state").resolve()
        finally:
            os.chdir(old_cwd)


def test_state_dir_prefers_explicit_config_file_when_env_unset():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
        cfg_dir = Path(tmp) / "cloud"
        cfg_dir.mkdir(parents=True)
        cfg = cfg_dir / "claudeteam.cloud.toml"
        cfg.write_text('state_dir = "runtime-state"\n', encoding="utf-8")
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(cwd)
            with env_patch(CLAUDETEAM_STATE_DIR=None,
                           CLAUDETEAM_CONFIG_FILE=str(cfg)):
                assert paths.state_dir() == (cfg_dir / "runtime-state").resolve()
        finally:
            os.chdir(old_cwd)


def test_config_file_infers_team_root_from_state_dir_when_cwd_drifted():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
        team = Path(tmp) / "team-a"
        state = team / "state"
        state.mkdir(parents=True)
        cfg = team / "claudeteam.toml"
        cfg.write_text("chat_id = \"oc_x\"\n", encoding="utf-8")
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(other)
            with env_patch(CLAUDETEAM_STATE_DIR=str(state),
                           CLAUDETEAM_CONFIG_FILE=None):
                assert paths.config_file() == cfg
        finally:
            os.chdir(old_cwd)


def test_config_file_prefers_state_pointer_over_cwd_config():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
        state = Path(tmp) / "state"
        state.mkdir(parents=True)
        cloud_cfg = Path(tmp) / "cloud" / "claudeteam.cloud.toml"
        cloud_cfg.parent.mkdir()
        cloud_cfg.write_text("chat_id = \"oc_cloud\"\n", encoding="utf-8")
        (state / "config-file.path").write_text(str(cloud_cfg) + "\n", encoding="utf-8")
        local_cfg = Path(cwd) / "claudeteam.toml"
        local_cfg.write_text("chat_id = \"oc_local\"\n", encoding="utf-8")
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(cwd)
            with env_patch(CLAUDETEAM_STATE_DIR=str(state),
                           CLAUDETEAM_CONFIG_FILE=None):
                assert paths.config_file() == cloud_cfg
        finally:
            os.chdir(old_cwd)
