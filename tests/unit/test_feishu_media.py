"""Tests for Feishu media artifact downloads."""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import attr_patch, isolated_env

from claudeteam.feishu import lark, media


def test_download_message_resource_writes_under_state_artifacts():
    calls = []

    def fake_run(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        Path(kwargs["cwd"], "image-img_v3_xxx.png").write_bytes(b"png")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")

    with isolated_env() as tmp, \
            attr_patch(lark, resolve_cli_prefix=lambda: ["lark-cli"]):
        path = media.download_message_resource(
            "om_123",
            "img_v3_xxx",
            "image",
            profile="prod",
            run=fake_run,
        )

    assert path is not None
    assert path.name == "image-img_v3_xxx.png"
    expected_dir = (tmp / "state" / "artifacts" / "feishu" / "om_123").resolve()
    assert str(path).startswith(str(expected_dir))
    assert calls[0]["kwargs"]["cwd"].endswith("/state/artifacts/feishu/om_123")
    assert calls[0]["args"][:3] == ["lark-cli", "--profile", "prod"]
    assert "--message-id" in calls[0]["args"]
    assert "--file-key" in calls[0]["args"]
