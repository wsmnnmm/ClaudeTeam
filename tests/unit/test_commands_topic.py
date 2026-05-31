"""Tests for `claudeteam topic` command."""
from __future__ import annotations

from helpers import isolated_env, run_cli
from claudeteam.cli import COMMANDS
from claudeteam.store import tasks


def test_topic_registered_in_cli():
    assert "topic" in COMMANDS


def test_topic_switch_show_note_and_list():
    with isolated_env():
        rc, out, err = run_cli(["topic", "switch", "工作Bug"])
        assert rc == 0, err
        assert "topic: #工作Bug" in out

        rc, out, err = run_cli(["topic", "note", "T-164 降噪暂停，不代表结论作废"])
        assert rc == 0, err
        assert "topic note added: #工作Bug" in out

        rc, out, _ = run_cli(["topic", "show"])
        assert "T-164 降噪暂停" in out

        rc, out, _ = run_cli(["topic", "list"])
        assert "* #工作Bug" in out


def test_topic_json_current_empty_is_machine_readable():
    with isolated_env():
        rc, out, err = run_cli(["topic", "--json"])
        assert rc == 0, err
        assert out.strip() == "{}"


def test_topic_set_sources_and_close():
    with isolated_env():
        rc, out, err = run_cli([
            "topic", "set", "TeamOps", "目标：只用短胶囊恢复上下文",
            "--source", "docs/claudeteam/topic-index.md",
        ])
        assert rc == 0, err
        assert "topic capsule set: #TeamOps" in out

        rc, out, _ = run_cli(["topic", "show", "teamops"])
        assert "短胶囊" in out
        assert "docs/claudeteam/topic-index.md" in out

        rc, out, err = run_cli(["topic", "close", "TeamOps"])
        assert rc == 0, err
        assert "status: closed" in out


def test_topic_show_accepts_clear_partial_terms():
    with isolated_env():
        rc, out, err = run_cli([
            "topic", "set", "工作Bug", "T-164 暂停；恢复时先查三维定位接口",
        ])
        assert rc == 0, err

        rc, out, err = run_cli(["topic", "show", "T-164"])
        assert rc == 0, err
        assert "topic: #工作Bug" in out
        assert "三维定位接口" in out


def test_topic_digest_lists_linked_active_tasks():
    with isolated_env():
        run_cli([
            "topic", "set", "TeamOps",
            "目标：把多话题聊天压成一屏恢复卡",
            "--source", "docs/claudeteam/topic-index.md",
        ])
        tasks.create("manager", "补 topic 回执", topic="TeamOps")
        tasks.create("worker", "历史完成项", topic="TeamOps",
                     artifact_path="artifacts/T-2/out.md")
        tasks.update("T-2", status="已完成", _force=True)

        rc, out, err = run_cli(["topic", "digest"])
        assert rc == 0, err
        assert "# TeamOps [active]" in out
        assert "补 topic 回执" in out
        assert "历史完成项" not in out


def test_topic_digest_can_write_daily_markdown_file():
    with isolated_env() as tmp:
        run_cli(["topic", "set", "TeamOps", "目标：每日沉淀恢复卡"])
        tasks.create("manager", "写 topic digest", topic="TeamOps")

        out_dir = tmp / "reports" / "topic-digests"
        rc, out, err = run_cli(["topic", "digest", "--write", str(out_dir)])

        assert rc == 0, err
        assert "written:" in out
        files = list(out_dir.glob("topic-digest-*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "# TeamOps [active]" in text
        assert "写 topic digest" in text
