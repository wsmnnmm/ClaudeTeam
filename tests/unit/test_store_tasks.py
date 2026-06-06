"""Tests for store/tasks.py — local task store."""
from __future__ import annotations


from helpers import isolated_env
from claudeteam.runtime import incident_learning as il
from claudeteam.store import tasks


# ── create ────────────────────────────────────────────────────────


def test_create_returns_task_id_and_persists():
    with isolated_env():
        tid = tasks.create("worker", "do thing")
        assert tid == "T-1"
        rows = tasks.list_tasks()
        assert len(rows) == 1
        assert rows[0]["title"] == "do thing"
        assert rows[0]["status"] == "待处理"


def test_ids_increment_across_creates():
    with isolated_env():
        a = tasks.create("x", "first")
        b = tasks.create("y", "second")
        assert a == "T-1" and b == "T-2"


def test_create_with_metadata_persists_creator_and_description():
    with isolated_env():
        tid = tasks.create("worker", "fix X",
                           description="root cause is Y",
                           creator="manager")
        t = tasks.get(tid)
        assert t["creator"] == "manager"
        assert t["description"] == "root cause is Y"


def test_create_with_artifact_persists_path():
    with isolated_env():
        tid = tasks.create("worker", "fix X", artifact_path="artifacts/T-1/out.md")
        t = tasks.get(tid)
        assert t["artifact_path"] == "artifacts/T-1/out.md"


def test_create_with_topic_persists_clean_name():
    with isolated_env():
        tid = tasks.create("worker", "fix X", topic="#学测2025")
        t = tasks.get(tid)
        assert t["topic"] == "学测2025"


def test_create_with_founder_os_metadata_persists():
    with isolated_env():
        tid = tasks.create(
            "worker", "validate buyer pain",
            founder_stage="idea",
            stage_exit_evidence="3 buyer interviews with current workaround",
            evidence_action="book 1 interview today",
            non_goal="do not build demo before evidence")
        t = tasks.get(tid)
        assert t["founder_stage"] == "idea"
        assert t["stage_exit_evidence"] == "3 buyer interviews with current workaround"
        assert t["evidence_action"] == "book 1 interview today"
        assert t["non_goal"] == "do not build demo before evidence"


def test_create_with_truth_surface_metadata_persists_canonical_fields():
    with isolated_env():
        tid = tasks.create(
            "worker", "修 Product Lab receipt 断点",
            issue_class="业务局部",
            current_segment="receipt",
            next_natural_window="2026-06-03 08:45 CST",
            base_absorb_needed="否")
        t = tasks.get(tid)
        assert t["issue_class"] == "local-business"
        assert t["current_segment"] == "receipt"
        assert t["next_natural_window"] == "2026-06-03 08:45 CST"
        assert t["base_absorb_needed"] == "no"


def test_create_child_task_persists_parent_task_id():
    with isolated_env():
        parent = tasks.create("manager", "parent battle")
        child = tasks.create("worker", "child unit", parent_task_id=parent)
        t = tasks.get(child)
        assert t["parent_task_id"] == parent


def test_create_child_task_inherits_parent_truth_surface_when_omitted():
    with isolated_env():
        parent = tasks.create(
            "manager", "parent battle",
            issue_class="local-business",
            current_segment="receipt",
            next_natural_window="2026-06-03 08:45 CST",
            base_absorb_needed="no")
        child = tasks.create("worker", "child unit", parent_task_id=parent)
        t = tasks.get(child)
        assert t["issue_class"] == "local-business"
        assert t["base_absorb_needed"] == "no"


def test_create_rejects_unknown_parent_task():
    with isolated_env():
        try:
            tasks.create("worker", "child unit", parent_task_id="T-99")
        except ValueError as e:
            assert "parent task not found" in str(e)
        else:
            raise AssertionError("expected ValueError on unknown parent")


def test_create_empty_title_rejects():
    with isolated_env():
        try:
            tasks.create("worker", "   ")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError on empty title")


# ── update ────────────────────────────────────────────────────────


def test_update_status_advances_state():
    with isolated_env():
        tid = tasks.create("w", "task")
        assert tasks.update(tid, status="进行中") is True
        assert tasks.get(tid)["status"] == "进行中"


def test_update_invalid_status_rejects():
    with isolated_env():
        tid = tasks.create("w", "task")
        try:
            tasks.update(tid, status="not-a-status")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")


def test_update_missing_task_returns_false():
    with isolated_env():
        assert tasks.update("T-99", status="已完成", _force=True) is False


def test_update_terminal_status_sets_completed_at():
    with isolated_env():
        tid = tasks.create("w", "x")
        tasks.update(tid, status="已完成", artifact_path="artifacts/test.md", _force=True)
        t = tasks.get(tid)
        assert t["completed_at"] is not None


def test_update_back_from_terminal_clears_completed_at():
    with isolated_env():
        tid = tasks.create("w", "x")
        tasks.update(tid, status="已完成", artifact_path="artifacts/test.md", _force=True)
        tasks.update(tid, status="进行中")
        assert tasks.get(tid)["completed_at"] is None


def test_update_only_changes_specified_fields():
    with isolated_env():
        tid = tasks.create("w1", "title-1", description="d-1", creator="c-1")
        tasks.update(tid, status="进行中")
        t = tasks.get(tid)
        # other fields untouched
        assert t["assignee"] == "w1"
        assert t["title"] == "title-1"
        assert t["description"] == "d-1"
        assert t["creator"] == "c-1"


def test_update_can_store_artifact_and_reviewer():
    with isolated_env():
        tid = tasks.create("w1", "old")
        tasks.update(tid, status="待验收",
                     artifact_path="artifacts/T-1/report.md",
                     reviewed_by="manager")
        t = tasks.get(tid)
        assert t["status"] == "待验收"
        assert t["artifact_path"] == "artifacts/T-1/report.md"
        assert t["reviewed_by"] == "manager"
        assert t["reviewed_at"] is not None


def test_complete_requires_reviewer_without_force():
    with isolated_env() as tmp:
        tid = tasks.create("w1", "old")
        artifact = tmp / "artifacts" / "T-1" / "report.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("evidence", encoding="utf-8")
        try:
            tasks.update(tid, status="已完成",
                         artifact_path="artifacts/T-1/report.md")
        except ValueError as e:
            assert "requires reviewed_by" in str(e)
        else:
            raise AssertionError("expected reviewer gate for completed task")


def test_update_can_store_topic():
    with isolated_env():
        tid = tasks.create("w1", "old")
        tasks.update(tid, topic="TeamOps")
        assert tasks.get(tid)["topic"] == "TeamOps"


def test_update_can_store_founder_os_metadata():
    with isolated_env():
        tid = tasks.create("w1", "old")
        tasks.update(
            tid,
            founder_stage="launch",
            stage_exit_evidence="support triage no longer depends on boss",
            evidence_action="ship one support SOP card",
            non_goal="do not open a second market")
        t = tasks.get(tid)
        assert t["founder_stage"] == "launch"
        assert t["stage_exit_evidence"] == "support triage no longer depends on boss"
        assert t["evidence_action"] == "ship one support SOP card"
        assert t["non_goal"] == "do not open a second market"


def test_update_can_store_truth_surface_metadata():
    with isolated_env():
        tid = tasks.create("w1", "old")
        tasks.update(
            tid,
            issue_class="cross-team",
            current_segment="本地快照",
            next_natural_window="今晚 18:00 CST",
            base_absorb_needed="yes")
        t = tasks.get(tid)
        assert t["issue_class"] == "cross-team"
        assert t["current_segment"] == "local_snapshot"
        assert t["next_natural_window"] == "今晚 18:00 CST"
        assert t["base_absorb_needed"] == "yes"


def test_update_rejects_unknown_truth_surface_segment():
    with isolated_env():
        tid = tasks.create("w1", "old")
        try:
            tasks.update(tid, current_segment="mystery-hop")
        except ValueError as e:
            assert "invalid current segment" in str(e)
        else:
            raise AssertionError("expected ValueError")


def test_update_can_attach_parent_task_id():
    with isolated_env():
        parent = tasks.create("manager", "parent")
        child = tasks.create("worker", "child")
        tasks.update(child, parent_task_id=parent)
        assert tasks.get(child)["parent_task_id"] == parent


def test_parent_cannot_close_while_child_task_is_open():
    with isolated_env() as tmp:
        parent = tasks.create("manager", "parent")
        tasks.create("worker", "child", parent_task_id=parent)
        artifact = tmp / "artifacts" / "T-1" / "report.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("evidence", encoding="utf-8")
        try:
            tasks.update(
                parent,
                status="已完成",
                artifact_path="artifacts/T-1/report.md",
                reviewed_by="manager",
            )
        except ValueError as e:
            assert "open child tasks" in str(e)
        else:
            raise AssertionError("expected open child gate")


def test_update_can_reassign_and_retitle():
    with isolated_env():
        tid = tasks.create("w1", "old", description="old-d")
        tasks.update(tid, assignee="w2", title="new", description="new-d")
        t = tasks.get(tid)
        assert (t["assignee"], t["title"], t["description"]) == ("w2", "new", "new-d")


def test_complete_task_marks_relevant_learning_applied():
    with isolated_env():
        lr = il.capture(il.Incident(
            "first_output_failure", "worker_cc", "空话",
            "artifact evidence URL screenshot",
        ), now_ms_fn=lambda: 1_000_000)
        assert lr is not None

        tid = tasks.create("worker_cc", "artifact evidence verification")
        assert tasks.update(
            tid, status="已完成",
            artifact_path="artifacts/test.md",
            _force=True,
        ) is True

        rows = il.list_learnings()
        row = next(r for r in rows if r["learning_id"] == lr.learning_id)
        assert row["prevented_count"] == 1


# ── list/get ──────────────────────────────────────────────────────


def test_list_filters_by_status():
    with isolated_env():
        a = tasks.create("w", "a")
        b = tasks.create("w", "b")
        tasks.update(a, status="已完成", artifact_path="artifacts/test.md", _force=True)
        only_done = tasks.list_tasks(status="已完成")
        only_open = tasks.list_tasks(status="待处理")
        assert [t["id"] for t in only_done] == [a]
        assert [t["id"] for t in only_open] == [b]


def test_list_filters_by_assignee():
    with isolated_env():
        tasks.create("alice", "task-a")
        tasks.create("bob", "task-b")
        tasks.create("alice", "task-a2")
        out = tasks.list_tasks(assignee="alice")
        assert {t["title"] for t in out} == {"task-a", "task-a2"}


def test_list_filters_by_topic_case_insensitive():
    with isolated_env():
        tasks.create("alice", "task-a", topic="TeamOps")
        tasks.create("bob", "task-b", topic="学测2025")
        tasks.create("alice", "task-c")
        out = tasks.list_tasks(topic="teamops")
        assert [t["title"] for t in out] == ["task-a"]


def test_list_filters_by_parent_task_id():
    with isolated_env():
        parent = tasks.create("manager", "parent")
        tasks.create("alice", "child-a", parent_task_id=parent)
        tasks.create("alice", "child-b", parent_task_id=parent)
        tasks.create("alice", "standalone")
        out = tasks.list_tasks(parent_task_id=parent)
        assert [t["title"] for t in out] == ["child-a", "child-b"]


def test_list_returns_empty_when_store_missing():
    with isolated_env():
        assert tasks.list_tasks() == []


def test_audit_tasks_flags_missing_truth_surface_fields_on_active_tasks():
    with isolated_env():
        tid = tasks.create("worker", "broken active task")
        report = tasks.audit_tasks()
        assert report["ok"] is False
        assert report["scanned_tasks"] == 1
        assert report["finding_count"] == 1
        row = report["findings"][0]
        assert row["task_id"] == tid
        assert row["finding_code"] == "missing_truth_surface"
        assert row["missing_fields"] == [
            "issue_class",
            "current_segment",
            "next_natural_window",
            "base_absorb_needed",
        ]


def test_audit_tasks_flags_parent_truth_surface_mismatches():
    with isolated_env():
        parent = tasks.create(
            "manager", "parent battle",
            issue_class="local-business",
            current_segment="receipt",
            next_natural_window="2026-06-03 08:45 CST",
            base_absorb_needed="no")
        tasks.create(
            "worker", "child unit",
            parent_task_id=parent,
            issue_class="base-common",
            current_segment="artifact",
            next_natural_window="2026-06-03 08:46 CST",
            base_absorb_needed="yes")
        report = tasks.audit_tasks()
        assert report["ok"] is False
        codes = [row["finding_code"] for row in report["findings"]]
        assert codes == [
            "parent_issue_class_mismatch",
            "parent_base_absorb_needed_mismatch",
        ]


def test_get_returns_none_for_unknown_id():
    with isolated_env():
        assert tasks.get("T-doesnotexist") is None


def test_list_sorted_by_id():
    with isolated_env():
        for i in range(5):
            tasks.create(f"w{i}", f"task {i}")
        rows = tasks.list_tasks()
        assert [t["id"] for t in rows] == ["T-1", "T-2", "T-3", "T-4", "T-5"]
