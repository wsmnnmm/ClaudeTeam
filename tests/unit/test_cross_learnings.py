"""Tests for store/cross_learnings.py — cross-team shared learning pool."""
from __future__ import annotations

from pathlib import Path

from helpers import attr_patch, isolated_env, run_cli
from claudeteam.store import cross_learnings, memory


def _isolated_pool(tmp: Path):
    """Make _pool_file/_pool_lock return paths inside `tmp` for test isolation."""
    pool_dir = tmp / "test-pool"
    pool_dir.mkdir(parents=True, exist_ok=True)
    pool_path = pool_dir / "learnings.jsonl"
    lock_path = pool_dir / "learnings.lock"
    return attr_patch(
        cross_learnings,
        _pool_file=lambda: pool_path,
        _pool_lock=lambda: lock_path,
    )


# ── mirror_learning ──────────────────────────────────────────────


def test_mirror_learning_writes_to_pool():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            result = cross_learnings.mirror_learning(
                "worker_a", "learning", "use defer-after-await for shell commands",
                ref="T-4",
            )
    assert result is not None
    assert result["team"] is not None
    assert result["agent"] == "worker_a"
    assert result["kind"] == "learning"
    assert "defer-after-await" in result["content"]


def test_mirror_learning_is_idempotent_across_multiple_appends():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            for i in range(3):
                cross_learnings.mirror_learning(
                    "worker_a", "learning", f"tip {i}", ref=f"T-{i}")
            entries = cross_learnings.list_shared(limit=10)
    assert len(entries) == 3
    contents = {e["content"] for e in entries}
    assert contents == {"tip 0", "tip 1", "tip 2"}


def test_mirror_learning_skips_non_learning_kinds():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            result = cross_learnings.mirror_learning(
                "worker_a", "blocker", "cannot reproduce", ref="T-5")
    assert result is None


def test_mirror_learning_handles_disabled_tunable():
    with isolated_env() as tmp:
        (tmp / "claudeteam.toml").write_text(
            "[cross_learnings]\n"
            f'pool_path = "{tmp / "pool"}"\n'
            "mirror_enabled = false\n",
            encoding="utf-8",
        )
        with _isolated_pool(tmp):
            from claudeteam.runtime import tunables
            tunables.reset_cache()
            result = cross_learnings.mirror_learning(
                "worker_a", "learning", "should not appear")
            entries = cross_learnings.list_shared(limit=10)
    assert result is None
    assert entries == []


# ── list_shared ──────────────────────────────────────────────────


def test_list_shared_returns_newest_first():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            cross_learnings.mirror_learning("w1", "learning", "first")
            cross_learnings.mirror_learning("w2", "learning", "second")
            rows = cross_learnings.list_shared(limit=10)
    assert rows[0]["content"] == "second"
    assert rows[1]["content"] == "first"


def test_list_shared_filters_by_team():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            cross_learnings.mirror_learning("w1", "learning", "a1")
            cross_learnings.mirror_learning("w2", "learning", "a2")
            rows = cross_learnings.list_shared(limit=10, team="ClaudeTeam")
    assert len(rows) == 2

    with isolated_env() as tmp2:
        with _isolated_pool(tmp2):
            rows = cross_learnings.list_shared(limit=10, team="nonexistent")
    assert len(rows) == 0


def test_list_shared_empty_pool_returns_empty():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            rows = cross_learnings.list_shared()
    assert rows == []


# ── render_for_prompt ────────────────────────────────────────────


def test_render_for_prompt_includes_team_and_content():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            cross_learnings.mirror_learning(
                "w1", "learning", "use p-limit for concurrency", ref="T-3")
            text = cross_learnings.render_for_prompt(limit=5)
    assert "跨团队共享经验" in text
    assert "w1" in text
    assert "p-limit" in text
    assert "T-3" in text


def test_render_for_prompt_empty_pool_returns_empty_string():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            text = cross_learnings.render_for_prompt()
    assert text == ""


# ── memory.append hook ────────────────────────────────────────────


def test_memory_append_learning_mirrors_to_cross_pool():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            memory.append("manager", "learning", "always validate before dispatch",
                          ref="T-10")
            entries = cross_learnings.list_shared(limit=10)
    assert len(entries) == 1
    assert entries[0]["kind"] == "learning"
    assert "validate before dispatch" in entries[0]["content"]


def test_memory_append_non_learning_does_not_mirror():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            memory.append("manager", "task_completed", "done with T-5",
                          ref="T-5")
            memory.append("manager", "note", "remember to check logs")
            entries = cross_learnings.list_shared(limit=10)
    assert len(entries) == 0


# ── CLI ──────────────────────────────────────────────────────────


def test_cli_cross_learnings_empty():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            rc, out, _ = run_cli(["cross-learnings"])
    assert rc == 0
    assert "no shared learnings" in out


def test_cli_cross_learnings_shows_entries():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            cross_learnings.mirror_learning("w1", "learning", "tip one", ref="T-1")
            cross_learnings.mirror_learning("w2", "learning", "tip two")
            rc, out, _ = run_cli(["cross-learnings"])
    assert rc == 0
    assert "2 shared learnings" in out
    assert "tip one" in out
    assert "tip two" in out
    assert "T-1" in out


def test_cli_cross_learnings_stats():
    with isolated_env() as tmp:
        with _isolated_pool(tmp):
            cross_learnings.mirror_learning("w1", "learning", "tip one")
            cross_learnings.mirror_learning("w2", "learning", "tip two")
            rc, out, _ = run_cli(["cross-learnings", "--stats"])
    assert rc == 0
    assert "total shared learnings: 2" in out
