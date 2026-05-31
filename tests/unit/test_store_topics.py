"""Tests for `store/topics.py` — lightweight conversation lanes."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.store import topics


def test_parse_topic_prefix_handles_space_and_colon_forms():
    assert topics.parse_topic_prefix("#工作Bug 查 T-164") == (
        "工作Bug", "查 T-164", True)
    assert topics.parse_topic_prefix("  #TeamOps：恢复上下文") == (
        "TeamOps", "恢复上下文", True)
    assert topics.parse_topic_prefix("## markdown heading") == (
        "", "## markdown heading", False)


def test_apply_message_switches_and_continues_current_topic():
    with isolated_env():
        event = topics.apply_message("#工作Bug 查 T-164", msg_id="om_1")
        assert event["kind"] == "switch"
        assert event["topic"]["name"] == "工作Bug"
        assert event["body"] == "查 T-164"
        assert topics.current_name() == "工作Bug"
        assert topics.current()["capsule"] == "本轮说明：查 T-164"

        event2 = topics.apply_message("继续刚才那条链路", msg_id="om_2")
        assert event2["kind"] == "continue"
        assert event2["topic"]["name"] == "工作Bug"
        assert topics.current()["last_message_id"] == "om_2"


def test_new_topic_body_initializes_empty_capsule():
    with isolated_env():
        topics.apply_message("#测试话题 这是测试，不要派活，只确认机制是否生效",
                             msg_id="om_test")
        row = topics.current()
        assert row["name"] == "测试话题"
        assert "本轮说明：这是测试" in row["capsule"]
        assert "不要派活" in row["capsule"]


def test_clear_fuzzy_terms_resolve_existing_topic():
    with isolated_env():
        topics.set_capsule("工作Bug", "T-164 暂停；恢复时先查三维定位接口。")

        by_name = topics.get("bug")
        assert by_name["name"] == "工作Bug"
        assert by_name["_matched_by"] == "bug"

        by_capsule = topics.get("T-164")
        assert by_capsule["name"] == "工作Bug"
        assert by_capsule["_matched_by"] == "T-164"

        switched = topics.switch("工作")
        assert switched["name"] == "工作Bug"
        assert topics.current_name() == "工作Bug"


def test_set_capsule_creates_exact_topic_even_if_name_appears_in_another_capsule():
    with isolated_env():
        topics.set_capsule("QiaChat-v3.3", "下一步：新飞书文档发出前先校验权限。")
        topics.set_capsule("飞书文档", "目标：处理云文档权限。")

        assert topics.get("QiaChat-v3.3")["capsule"].startswith("下一步")
        assert topics.get("飞书文档")["name"] == "飞书文档"
        assert topics.get("飞书文档")["capsule"] == "目标：处理云文档权限。"


def test_capsule_notes_are_clipped_for_prompt_injection():
    with isolated_env():
        topics.switch("TeamOps")
        row = topics.set_capsule("TeamOps", "A" * 5000)
        assert len(row["capsule"]) <= topics.MAX_CAPSULE_CHARS
        rendered = topics.render_event_for_prompt({
            "kind": "continue",
            "topic": row,
            "changed": False,
            "previous": "",
        })
        assert "胶囊过长" in rendered
        assert len(rendered) < topics.MAX_PROMPT_CAPSULE_CHARS + 300


def test_close_current_topic_clears_pointer_but_keeps_row():
    with isolated_env():
        topics.switch("TeamOps")
        closed = topics.close()
        assert closed["status"] == "closed"
        assert topics.current() is None
        assert topics.get("teamops")["status"] == "closed"


# ── _extract_key_terms ──────────────────────────────────────────


def test_extract_key_terms_chinese_text():
    terms = topics._extract_key_terms("用户压力高，禁止刷收到。")
    assert "用户" in terms or "用户压力" in terms
    assert "禁止" in terms or "禁止刷" in terms
    assert len(terms) >= 3


def test_extract_key_terms_mixed_chinese_english():
    terms = topics._extract_key_terms("ClaudeTeam 部署到 Docker 环境")
    assert "claudeteam" in terms
    assert "docker" in terms
    assert len(terms) >= 2


def test_extract_key_terms_short_text():
    terms = topics._extract_key_terms("继续")
    assert "继续" in terms


def test_extract_key_terms_empty():
    assert topics._extract_key_terms("") == set()
    assert topics._extract_key_terms("   ") == set()


# ── _jaccard ─────────────────────────────────────────────────────


def test_jaccard_identical_sets():
    assert topics._jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets():
    assert topics._jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial_overlap():
    result = topics._jaccard({"a", "b", "c"}, {"b", "c", "d"})
    assert 0.4 < result < 0.6


def test_jaccard_empty_sets():
    assert topics._jaccard(set(), set()) == 1.0
    assert topics._jaccard({"a"}, set()) == 0.0
    assert topics._jaccard(set(), {"a"}) == 0.0


# ── topic_drift_detected ──────────────────────────────────────────


def test_drift_detected_different_topics():
    assert topics.topic_drift_detected(
        "飞书文档权限要改成公开可读并且还要加水印限制下载",
        "T-164 暂停；恢复时先查三维定位接口。",
    )


def test_drift_not_detected_similar_topics():
    assert not topics.topic_drift_detected(
        "T-164 的接口现在恢复正常了吗",
        "T-164 暂停；恢复时先查三维定位接口。",
    )


def test_drift_not_detected_short_text():
    """Short follow-ups like '继续' should never trigger drift."""
    assert not topics.topic_drift_detected(
        "继续，只给一个下一步",
        "用户压力高，禁止刷收到。",
    )


def test_drift_not_detected_empty_capsule():
    assert not topics.topic_drift_detected("任何内容", "")


def test_drift_not_detected_empty_text():
    assert not topics.topic_drift_detected("", "有胶囊的内容")


# ── auto_topic_name ──────────────────────────────────────────────


def test_auto_topic_name_from_first_sentence():
    name = topics.auto_topic_name("飞书文档权限要改成公开可读，另外还有几个问题")
    assert "飞书文档权限" in name


def test_auto_topic_name_strips_common_prefixes():
    name = topics.auto_topic_name("另外飞书文档权限要改成公开")
    assert not name.startswith("另外")
    assert "飞书文档" in name or "权限" in name


def test_auto_topic_name_strips_leading_topic_marker():
    name = topics.auto_topic_name("#TeamOps 恢复上下文")
    assert not name.startswith("#")
    assert len(name) >= 2


def test_auto_topic_name_falls_back_for_empty_text():
    name = topics.auto_topic_name("")
    assert len(name) >= 2


def test_auto_topic_name_truncates_long_text():
    name = topics.auto_topic_name("飞书文档权限需要改成公开可读并且还要设置水印和下载限制")
    assert len(name) <= topics._TOPIC_NAME_MAX_CHARS


# ── record_msg_topic / lookup_parent_topic ────────────────────────


def test_record_and_lookup_parent_topic():
    with isolated_env():
        topics.switch("TeamOps")
        topics.record_msg_topic("om_parent_1", "TeamOps")
        assert topics.lookup_parent_topic("om_parent_1") == "TeamOps"


def test_lookup_parent_topic_unknown_msg():
    with isolated_env():
        assert topics.lookup_parent_topic("nonexistent") is None


def test_lookup_parent_topic_empty_msg_id():
    assert topics.lookup_parent_topic("") is None
    assert topics.lookup_parent_topic("  ") is None


def test_record_msg_topic_ignores_empty_args():
    with isolated_env():
        topics.record_msg_topic("", "TeamOps")
        topics.record_msg_topic("om_x", "")
        assert topics.lookup_parent_topic("") is None
        assert topics.lookup_parent_topic("om_x") is None


# ── apply_message records msg_id ──────────────────────────────────


def test_apply_message_records_msg_id_for_lookup():
    with isolated_env():
        topics.apply_message("#TeamOps 开始排查", msg_id="om_sw")
        assert topics.lookup_parent_topic("om_sw") == "TeamOps"


def test_apply_message_continue_records_msg_id():
    with isolated_env():
        topics.switch("TeamOps")
        topics.apply_message("继续跟进这条", msg_id="om_follow")
        assert topics.lookup_parent_topic("om_follow") == "TeamOps"
