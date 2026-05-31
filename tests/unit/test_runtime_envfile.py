"""Tests for runtime/envfile.py."""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from helpers import attr_patch, env_patch
from claudeteam.runtime import envfile


@contextlib.contextmanager
def _cwd(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _clear_env():
    return env_patch(
        FEISHU_APP_ID=None,
        FEISHU_APP_SECRET=None,
        LARKSUITE_CLI_APP_ID=None,
        LARKSUITE_CLI_APP_SECRET=None,
        LARKSUITE_CLI_TENANT_ACCESS_TOKEN=None,
        CLAUDETEAM_ENABLE_FEISHU_REMOTE=None,
        CWD_ONLY=None,
        REPO_ONLY=None,
        TEAM_ONLY=None,
    )


def test_load_dotenv_prefers_config_dir_feishu_credentials():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = root / "team"
        cwd = root / "cwd"
        fake_repo = root / "repo"
        for path in (team, cwd, fake_repo / "src" / "claudeteam" / "runtime"):
            path.mkdir(parents=True)
        (team / "claudeteam.toml").write_text("", encoding="utf-8")
        (team / ".env").write_text(
            "FEISHU_APP_ID=team_app\nTEAM_ONLY=1\n", encoding="utf-8")
        (cwd / ".env").write_text(
            "FEISHU_APP_ID=cwd_app\nCWD_ONLY=1\n", encoding="utf-8")
        (fake_repo / ".env").write_text(
            "FEISHU_APP_ID=repo_app\nREPO_ONLY=1\n", encoding="utf-8")
        fake_file = fake_repo / "src" / "claudeteam" / "runtime" / "envfile.py"
        fake_file.write_text("", encoding="utf-8")

        with _cwd(cwd), _clear_env(), \
                env_patch(CLAUDETEAM_CONFIG_FILE=str(team / "claudeteam.toml")), \
                attr_patch(envfile, __file__=str(fake_file)):
            envfile.load_dotenv()
            assert os.environ["FEISHU_APP_ID"] == "team_app"
            assert os.environ["TEAM_ONLY"] == "1"
            assert os.environ["CWD_ONLY"] == "1"
            assert os.environ["REPO_ONLY"] == "1"


def test_load_dotenv_skips_shared_repo_feishu_credentials_for_external_config():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team = root / "team"
        cwd = root / "cwd"
        fake_repo = root / "repo"
        for path in (team, cwd, fake_repo / "src" / "claudeteam" / "runtime"):
            path.mkdir(parents=True)
        (team / "claudeteam.toml").write_text("", encoding="utf-8")
        (cwd / ".env").write_text(
            "FEISHU_APP_ID=cwd_app\nCLAUDETEAM_ENABLE_FEISHU_REMOTE=1\n",
            encoding="utf-8",
        )
        (fake_repo / ".env").write_text(
            "FEISHU_APP_SECRET=repo_secret\nREPO_ONLY=1\n", encoding="utf-8")
        fake_file = fake_repo / "src" / "claudeteam" / "runtime" / "envfile.py"
        fake_file.write_text("", encoding="utf-8")

        with _cwd(cwd), _clear_env(), \
                env_patch(CLAUDETEAM_CONFIG_FILE=str(team / "claudeteam.toml")), \
                attr_patch(envfile, __file__=str(fake_file)):
            envfile.load_dotenv()
            assert "FEISHU_APP_ID" not in os.environ
            assert "FEISHU_APP_SECRET" not in os.environ
            assert os.environ["CLAUDETEAM_ENABLE_FEISHU_REMOTE"] == "1"
            assert os.environ["REPO_ONLY"] == "1"
