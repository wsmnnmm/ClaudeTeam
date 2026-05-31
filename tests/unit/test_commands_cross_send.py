"""Tests for real cross-team dispatch.

`claudeteam send` is intentionally local-team only.  These tests cover the
separate cross-team entrypoint so a source team cannot accidentally create a
fake local assignee named after another team.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
from pathlib import Path

from helpers import FakeProc, env_patch, isolated_env, run_cli
from claudeteam.commands import cross_send
from claudeteam.store import local_facts, tasks


def _write_team(team_dir: Path, *, session: str = "TargetTeam") -> None:
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "claudeteam.toml").write_text(
        "\n".join([
            f"[team]\nsession = \"{session}\"",
            "",
            "[team.agents.manager]",
            "cli = \"claude-code\"",
            "",
        ]),
        encoding="utf-8",
    )


def test_cross_send_path_writes_target_state_not_source_state():
    with isolated_env() as source:
        target = source / "target-team"
        _write_team(target)

        rc, out, err = run_cli([
            "cross-send", str(target), "manager", "product_lab_manager",
            "please review PL-1", "--no-inject",
        ])

        assert rc == 0, err
        assert "target=target-team" in out
        assert "resolved_target=manager" in out
        assert "local_id=msg_" in out
        assert "task_id=T-1" in out
        assert tasks.list_tasks() == []
        assert local_facts.list_messages("manager") == []

        with env_patch(
            CLAUDETEAM_STATE_DIR=str(target / "state"),
            CLAUDETEAM_CONFIG_FILE=str(target / "claudeteam.toml"),
        ):
            rows = local_facts.list_messages("manager")
            assert len(rows) == 1
            assert rows[0]["from"] == "product_lab_manager"
            assert rows[0]["content"] == "please review PL-1"
            assert tasks.get("T-1")["assignee"] == "manager"


def test_cross_send_registry_aliases_external_manager_to_target_manager():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "website-chuhai-team"
        _write_team(target, session="WebsiteChuhai")
        registry = tmp_path / "registry.py"
        registry.write_text(
            "import json\n"
            "print(json.dumps({'teams': [{"
            "'key': 'website_chuhai', "
            "'label': 'WebsiteChuhai', "
            f"'config_path': {str(target / 'claudeteam.toml')!r}"
            "}]}))\n",
            encoding="utf-8",
        )

        with isolated_env():
            rc, out, err = run_cli([
                "cross-send", "website_chuhai", "WebsiteChuhai_manager",
                "product_lab_manager", "strategy packet", "--registry-script",
                str(registry), "--no-inject",
            ])

        assert rc == 0, err
        assert "target=WebsiteChuhai" in out
        assert "requested_target=WebsiteChuhai_manager" in out
        assert "resolved_target=manager" in out

        with env_patch(
            CLAUDETEAM_STATE_DIR=str(target / "state"),
            CLAUDETEAM_CONFIG_FILE=str(target / "claudeteam.toml"),
        ):
            rows = local_facts.list_messages("manager")
            assert len(rows) == 1
            assert "strategy packet" in rows[0]["content"]
            assert "原请求目标 `WebsiteChuhai_manager`" in rows[0]["content"]


def test_cross_send_cloud_config_uses_runtime_state_dir():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_dir = tmp_path / "projects" / "product-lab" / "ops" / "claudeteam-cloud"
        _write_team(config_dir, session="ProductLabCloud")
        cloud_config = config_dir / "claudeteam.cloud.toml"
        (config_dir / "claudeteam.toml").rename(cloud_config)
        runtime_root = tmp_path / "runtime"

        with isolated_env(), env_patch(CLAUDETEAM_CLOUD_RUNTIME_ROOT=str(runtime_root)):
            rc, out, err = run_cli([
                "cross-send", str(cloud_config), "manager",
                "todo002_cloud_manager", "cloud callback", "--no-inject",
            ])

        assert rc == 0, err
        assert "target=claudeteam-cloud" in out
        runtime_state = runtime_root / "product-lab-cloud" / "state"
        with env_patch(
            CLAUDETEAM_STATE_DIR=str(runtime_state),
            CLAUDETEAM_CONFIG_FILE=str(cloud_config),
        ):
            rows = local_facts.list_messages("manager")
            assert len(rows) == 1
            assert rows[0]["content"] == "cloud callback"
        assert not (config_dir / "state" / "facts" / "inbox.json").exists()


def test_cross_send_remote_uses_ssh_with_target_env():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        root = tmp_path / "fleet"
        registry_dir = root / "product-lab" / "scripts"
        registry_dir.mkdir(parents=True)
        cloud_config = tmp_path / "todo002-cloud.toml"
        cloud_config.write_text(
            "[team]\nsession = \"TODO002Cloud\"\n\n"
            "[team.agents.manager]\ncli = \"claude-code\"\n",
            encoding="utf-8",
        )
        registry = registry_dir / "team-registry.py"
        registry.write_text(
            "import json\n"
            "print(json.dumps({'teams': [{"
            "'key': 'todo002_cloud', "
            "'label': 'TODO002 云上', "
            f"'config_path': {str(cloud_config)!r}"
            "}]}))\n",
            encoding="utf-8",
        )
        remote = root / "product-lab" / "state" / "remote-teams" / "todo002_cloud"
        remote.mkdir(parents=True)
        (remote / "meta.json").write_text(
            json.dumps({
                "key": "todo002_cloud",
                "label": "TODO002 云上",
                "remote_host": "cloud-box",
                "remote_product": "/srv/ai/projects/todo002-study-coach",
                "remote_runtime": "/srv/ai/runtime/todo002-study-coach-cloud",
                "remote_config": "/srv/ai/projects/todo002-study-coach/claudeteam.cloud.toml",
            }),
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            calls.append(list(args))
            return FakeProc(
                returncode=0,
                stdout="📥 inbox: manager ← product_lab_manager  [local_id=msg_remote]  [task_id=T-9]\n",
                stderr="",
            )

        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cross_send.main([
                "todo002_cloud", "todo002_manager", "product_lab_manager",
                "demand pack", "--root", str(root), "--no-inject",
            ], run=fake_run)

        assert rc == 0, err.getvalue()
        assert calls and len(calls[0]) == 3
        assert calls[0][:2] == ["ssh", "cloud-box"]
        assert calls[0][2].startswith("bash --noprofile --norc -c ")
        remote_cmd = calls[0][2]
        assert "CLAUDETEAM_STATE_DIR=/srv/ai/runtime/todo002-study-coach-cloud/state" in remote_cmd
        assert "CLAUDETEAM_CONFIG_FILE=/srv/ai/projects/todo002-study-coach/claudeteam.cloud.toml" in remote_cmd
        assert "claudeteam send manager product_lab_manager" in remote_cmd
        assert "demand pack" in remote_cmd
        assert "resolved_target=manager" in out.getvalue()
        assert "task_id=T-9" in out.getvalue()
