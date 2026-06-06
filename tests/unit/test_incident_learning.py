"""Tests for runtime/incident_learning.py — the L5 self-evolution engine."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.runtime import incident_learning as il


# ── Incident dataclass ──────────────────────────────────────────────


def test_incident_fingerprint_is_stable():
    a = il.Incident("first_output_failure", "worker_cc", "空话", "vague reply")
    b = il.Incident("first_output_failure", "worker_cc", "空话", "different detail")
    assert a.fingerprint() == b.fingerprint()


def test_incident_fingerprint_differs_by_type():
    a = il.Incident("first_output_failure", "worker_cc", "空话", "vague")
    b = il.Incident("quality_guard", "worker_cc", "空话", "vague")
    assert a.fingerprint() != b.fingerprint()


def test_incident_fingerprint_differs_by_agent():
    a = il.Incident("first_output_failure", "worker_cc", "空话", "vague")
    b = il.Incident("first_output_failure", "worker_kimi", "空话", "vague")
    assert a.fingerprint() != b.fingerprint()


def test_incident_to_dict():
    inc = il.Incident("first_output_failure", "worker_cc", "空话",
                      "vague reply", task_id="T-1", severity="critical")
    d = inc.to_dict()
    assert d["incident_type"] == "first_output_failure"
    assert d["agent"] == "worker_cc"
    assert d["pattern"] == "空话"
    assert d["task_id"] == "T-1"
    assert d["severity"] == "critical"


# ── classify_root_cause ─────────────────────────────────────────────


def test_classify_first_output_failure():
    inc = il.Incident("first_output_failure", "worker_cc", "空话", "vague")
    cause = il.classify_root_cause(inc)
    assert "vague acknowledgement" in cause.lower() or "acknowledgement" in cause.lower()


def test_classify_artifact_missing():
    inc = il.Incident("artifact_missing", "worker_cc",
                      "screenshot image", "no screenshot")
    cause = il.classify_root_cause(inc)
    assert "screenshot" in cause.lower()


def test_classify_quality_guard():
    inc = il.Incident("quality_guard", "worker_cc",
                      "internal_token_leak", "leaked jargon")
    cause = il.classify_root_cause(inc)
    assert "jargon" in cause.lower()


def test_classify_unknown_pattern():
    inc = il.Incident("first_output_failure", "worker_cc",
                      "weird_new_pattern", "something odd")
    cause = il.classify_root_cause(inc)
    assert "unknown pattern" in cause.lower() or "weird_new_pattern" in cause


def test_classify_includes_task_id():
    inc = il.Incident("first_output_failure", "worker_cc", "空话",
                      "vague", task_id="T-42")
    cause = il.classify_root_cause(inc)
    assert "T-42" in cause


# ── capture + dedup ─────────────────────────────────────────────────


def test_capture_creates_learning_on_first_incident():
    with isolated_env():
        inc = il.Incident("first_output_failure", "worker_cc", "空话",
                          "vague reply")
        lr = il.capture(inc, now_ms_fn=lambda: 1_000_000)
        assert lr is not None
        assert lr.category == "first_output_failure"
        assert lr.seen_count == 1


def test_capture_debounces_same_fingerprint():
    with isolated_env():
        inc_a = il.Incident("first_output_failure", "worker_cc", "空话",
                            "vague reply")
        inc_b = il.Incident("first_output_failure", "worker_cc", "空话",
                            "different detail")
        lr1 = il.capture(inc_a, now_ms_fn=lambda: 1_000_000)
        assert lr1 is not None
        # Same fingerprint within 600s debounce window — should skip
        lr2 = il.capture(inc_b, now_ms_fn=lambda: 1_000_001)
        assert lr2 is None


def test_capture_allows_after_debounce_window():
    with isolated_env():
        inc = il.Incident("first_output_failure", "worker_cc", "空话",
                          "vague reply")
        lr1 = il.capture(inc, now_ms_fn=lambda: 1_000_000)
        assert lr1 is not None
        # After debounce window (default 600s)
        lr2 = il.capture(inc, now_ms_fn=lambda: 1_600_000)
        assert lr2 is not None
        assert lr2.seen_count >= 1


def test_capture_different_patterns_dont_debounce_each_other():
    with isolated_env():
        inc_a = il.Incident("first_output_failure", "worker_cc", "空话",
                            "vague")
        inc_b = il.Incident("first_output_failure", "worker_cc", "无证据",
                            "no evidence")
        lr1 = il.capture(inc_a, now_ms_fn=lambda: 1_000_000)
        lr2 = il.capture(inc_b, now_ms_fn=lambda: 1_000_001)
        assert lr1 is not None
        assert lr2 is not None
        assert lr1.learning_id != lr2.learning_id


# ── _render_lesson ──────────────────────────────────────────────────


def test_render_lesson_first_output():
    inc = il.Incident("first_output_failure", "worker_cc", "空话",
                      "vague reply")
    lesson = il._render_lesson(inc)
    assert "worker_cc" in lesson
    assert "首产物不合格" in lesson or "first_output" in lesson.lower()


def test_render_lesson_artifact_missing():
    inc = il.Incident("artifact_missing", "worker_cc",
                      "screenshot image", "no screenshot")
    lesson = il._render_lesson(inc)
    assert "worker_cc" in lesson
    assert "缺少证据" in lesson or "artifact" in lesson.lower()


def test_render_lesson_quality_guard():
    inc = il.Incident("quality_guard", "worker_cc",
                      "internal_token_leak", "leaked jargon")
    lesson = il._render_lesson(inc)
    assert "worker_cc" in lesson
    assert ("门禁" in lesson or "quality" in lesson.lower() or "guard" in lesson.lower())


def test_render_lesson_api_cost_warning():
    inc = il.Incident("api_cost_warning", "worker_cc", "deepseek",
                      "cost warning")
    lesson = il._render_lesson(inc)
    assert "80%" in lesson or "预算预警" in lesson or "api" in lesson.lower()


def test_render_lesson_api_cost_block():
    inc = il.Incident("api_cost_block", "worker_cc", "deepseek",
                      "cost block")
    lesson = il._render_lesson(inc)
    assert ("budget" in lesson.lower() or "预算" in lesson
            or "api" in lesson.lower())


# ── mark_applied / mark_recurred ────────────────────────────────────


def test_mark_applied_increments_prevented_count():
    with isolated_env():
        inc = il.Incident("first_output_failure", "worker_cc", "空话",
                          "vague reply")
        lr = il.capture(inc, now_ms_fn=lambda: 1_000_000)
        assert lr is not None
        assert il.mark_applied(lr.learning_id, now_ms_fn=lambda: 2_000_000)
        rows = il.list_learnings()
        assert rows[0]["prevented_count"] == 1


def test_mark_recurred_increments_failed_count():
    with isolated_env():
        inc = il.Incident("first_output_failure", "worker_cc", "空话",
                          "vague reply")
        lr = il.capture(inc, now_ms_fn=lambda: 1_000_000)
        assert lr is not None
        assert il.mark_recurred(lr.learning_id, now_ms_fn=lambda: 2_000_000)
        rows = il.list_learnings()
        assert rows[0]["failed_count"] == 1


def test_mark_applied_unknown_id_returns_false():
    with isolated_env():
        assert not il.mark_applied("nonexistent_id")


def test_mark_recurred_unknown_id_returns_false():
    with isolated_env():
        assert not il.mark_recurred("nonexistent_id")


def test_register_task_context_links_relevant_learning_ids():
    with isolated_env():
        il.capture(il.Incident(
            "first_output_failure", "worker_cc", "空话",
            "artifact evidence URL screenshot",
        ), now_ms_fn=lambda: 1_000_000)

        rows = il.register_task_context(
            "T-9",
            task_title="artifact evidence verification",
            assignee="worker_cc",
        )

        assert len(rows) == 1
        state = il._load_state()
        assert state["task_links"]["T-9"]["learning_ids"] == [rows[0]["learning_id"]]


def test_mark_task_applied_marks_linked_learning_once():
    with isolated_env():
        lr = il.capture(il.Incident(
            "first_output_failure", "worker_cc", "空话",
            "artifact evidence URL screenshot",
        ), now_ms_fn=lambda: 1_000_000)
        assert lr is not None

        il.register_task_context(
            "T-9",
            task_title="artifact evidence verification",
            assignee="worker_cc",
        )

        applied = il.mark_task_applied("T-9", now_ms_fn=lambda: 2_000_000)
        assert applied == [lr.learning_id]
        assert il.mark_task_applied("T-9", now_ms_fn=lambda: 3_000_000) == []

        rows = il.list_learnings()
        row = next(r for r in rows if r["learning_id"] == lr.learning_id)
        assert row["prevented_count"] == 1


def test_capture_marks_recurred_for_linked_task_learning():
    with isolated_env():
        lr = il.capture(il.Incident(
            "first_output_failure", "worker_cc", "空话",
            "artifact evidence URL screenshot",
        ), now_ms_fn=lambda: 1_000_000)
        assert lr is not None

        il.register_task_context(
            "T-9",
            task_title="artifact evidence verification",
            assignee="worker_cc",
        )

        il.capture(il.Incident(
            "first_output_failure", "worker_cc", "无证据",
            "still no evidence", task_id="T-9",
        ), now_ms_fn=lambda: 2_000_000)

        rows = il.list_learnings()
        row = next(r for r in rows if r["learning_id"] == lr.learning_id)
        assert row["failed_count"] == 1


# ── stats ───────────────────────────────────────────────────────────


def test_stats_empty_state():
    with isolated_env():
        s = il.stats()
        assert s["total_learnings"] == 0
        assert s["learnings_applied"] == 0
        assert s["application_rate"] == 0.0


def test_stats_with_learnings():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc", "空话",
                               "vague"), now_ms_fn=lambda: 1_000_000)
        s = il.stats()
        assert s["total_learnings"] == 1
        assert "first_output_failure" in s["by_category"]


def test_stats_tracks_severity():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc", "空话",
                               "vague", severity="critical"),
                   now_ms_fn=lambda: 1_000_000)
        s = il.stats()
        assert "critical" in s["by_severity"]


# ── list_learnings ──────────────────────────────────────────────────


def test_list_learnings_returns_newest_first():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc", "空话",
                               "v1"), now_ms_fn=lambda: 1_000_000)
        il.capture(il.Incident("quality_guard", "worker_cc",
                               "empty_list_item", "v2"),
                   now_ms_fn=lambda: 3_000_000)
        rows = il.list_learnings()
        assert len(rows) == 2
        # Newest first (by created_at)
        assert rows[0]["created_at"] >= rows[1]["created_at"]


def test_list_learnings_filter_by_category():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc", "空话",
                               "v1"), now_ms_fn=lambda: 1_000_000)
        il.capture(il.Incident("quality_guard", "worker_cc",
                               "empty_list_item", "v2"),
                   now_ms_fn=lambda: 2_000_000)
        rows = il.list_learnings(category="quality_guard")
        assert len(rows) == 1
        assert rows[0]["category"] == "quality_guard"


def test_list_learnings_respects_limit():
    with isolated_env():
        for i in range(10):
            il.capture(il.Incident("first_output_failure", "worker_cc",
                                   f"pattern_{i}", f"detail {i}"),
                       now_ms_fn=lambda i=i: 1_000_000 + i * 1000)
        rows = il.list_learnings(limit=3)
        assert len(rows) == 3


# ── find_relevant ───────────────────────────────────────────────────


def test_find_relevant_term_overlap():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc",
                               "空话", "artifact evidence URL screenshot"),
                   now_ms_fn=lambda: 1_000_000)
        il.capture(il.Incident("quality_guard", "worker_cc",
                               "internal_token_leak", "API token leaked"),
                   now_ms_fn=lambda: 2_000_000)
        # Use terms that appear in the rendered lesson ("artifact", "evidence", "URL")
        rows = il.find_relevant(task_title="artifact evidence verification")
        assert len(rows) >= 1


def test_find_relevant_assignee_matching():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc",
                               "空话", "vague"), now_ms_fn=lambda: 1_000_000)
        il.capture(il.Incident("quality_guard", "worker_kimi",
                               "empty_list_item", "empty"),
                   now_ms_fn=lambda: 2_000_000)
        rows = il.find_relevant(assignee="worker_kimi")
        assert len(rows) >= 1


def test_find_relevant_no_match_returns_empty():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc",
                               "空话", "vague reply"),
                   now_ms_fn=lambda: 1_000_000)
        rows = il.find_relevant(task_title="xyzzy completely unrelated")
        assert rows == []


def test_find_relevant_respects_limit():
    with isolated_env():
        for i in range(5):
            il.capture(il.Incident("first_output_failure", "worker_cc",
                                   f"pattern_{i}", "artifact evidence"),
                       now_ms_fn=lambda i=i: 1_000_000 + i * 1000)
        rows = il.find_relevant(task_title="artifact evidence", limit=2)
        assert len(rows) <= 2


# ── Factory helpers ─────────────────────────────────────────────────


def test_from_first_output_gate():
    inc = il.from_first_output_gate("worker_cc", "T-1", "空话", "vague reply")
    assert inc.incident_type == "first_output_failure"
    assert inc.agent == "worker_cc"
    assert inc.pattern == "空话"
    assert inc.task_id == "T-1"
    assert inc.severity == "warn"


def test_from_artifact_gate():
    inc = il.from_artifact_gate("worker_cc", "T-2",
                                ["screenshot image", "http(s) preview URL"])
    assert inc.incident_type == "artifact_missing"
    assert "screenshot" in inc.pattern
    assert inc.task_id == "T-2"


def test_from_artifact_gate_empty_missing():
    inc = il.from_artifact_gate("worker_cc", "T-3", [])
    assert inc.pattern == "unknown"


def test_from_quality_guard():
    inc = il.from_quality_guard("worker_cc",
                                "boss-visible message leaks internal execution jargon")
    assert inc.incident_type == "quality_guard"
    assert inc.pattern == "internal_token_leak"


def test_from_quality_guard_fallback_pattern():
    inc = il.from_quality_guard("worker_cc",
                                "some random quality issue XYZ")
    assert inc.incident_type == "quality_guard"
    assert len(inc.pattern) <= 50


def test_from_api_cost_guard_warning():
    inc = il.from_api_cost_guard("worker_cc", "warn", "deepseek", 0.05)
    assert inc.incident_type == "api_cost_warning"
    assert inc.severity == "warn"
    assert "deepseek" in inc.pattern


def test_from_api_cost_guard_block():
    inc = il.from_api_cost_guard("worker_cc", "block", "deepseek", 0.15)
    assert inc.incident_type == "api_cost_block"
    assert inc.severity == "critical"


# ── render_for_prompt ───────────────────────────────────────────────


def test_render_for_prompt_empty_state():
    with isolated_env():
        assert il.render_for_prompt() == ""


def test_render_for_prompt_includes_learnings():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc", "空话",
                               "vague reply"), now_ms_fn=lambda: 1_000_000)
        text = il.render_for_prompt()
        assert "自进化学习记录" in text or "Incident Learnings" in text
        assert "空话" in text or "首产物" in text


def test_render_for_prompt_filter_by_agent():
    with isolated_env():
        il.capture(il.Incident("first_output_failure", "worker_cc", "空话",
                               "vague"), now_ms_fn=lambda: 1_000_000)
        il.capture(il.Incident("quality_guard", "worker_kimi",
                               "empty_list_item", "empty"),
                   now_ms_fn=lambda: 2_000_000)
        text = il.render_for_prompt(agent="worker_cc")
        assert "worker_cc" in text or "空话" in text


def test_render_for_prompt_respects_limit():
    with isolated_env():
        for i in range(10):
            il.capture(il.Incident("first_output_failure", "worker_cc",
                                   f"pattern_{i}", f"detail {i}"),
                       now_ms_fn=lambda i=i: 1_000_000 + i * 1000)
        text = il.render_for_prompt(limit=3)
        lines = [l for l in text.splitlines() if l.strip().startswith(tuple("1234567890"))]
        assert len(lines) <= 3


# ── LearningRecord dataclass ────────────────────────────────────────


def test_learning_record_defaults():
    lr = il.LearningRecord(
        learning_id="L1",
        lesson="test lesson",
        category="test_cat",
    )
    assert lr.learning_id == "L1"
    assert lr.seen_count == 0
    assert lr.prevented_count == 0
    assert lr.failed_count == 0
    assert lr.source_incidents == []
