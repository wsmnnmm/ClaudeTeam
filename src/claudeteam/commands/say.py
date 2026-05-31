"""`claudeteam say <agent> <message> [--reply <message_id>]`

Post a chat message as `<agent>`.  Default identity is bot; pass
`--as user` to post as the logged-in lark-cli user.  A persistent default
can be set via `CLAUDETEAM_LARK_SEND_AS=user|bot` for the whole shell.

Successful messages are mirrored to the local audit log — pass
`--no-local` to skip that. Failed Feishu sends are recorded as
`say_failed` and escalated back into manager's inbox so an agent cannot
mistake a local attempted reply for a boss-visible reply.

Exits non-zero if `chat_id` is unset (run setup or set runtime_config.json).
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from claudeteam.feishu import chat as feishu_chat
from claudeteam.feishu.cards import simple_card
from claudeteam.feishu.text import normalize_visible_escapes as _normalize_visible_escapes
from claudeteam.runtime import artifact_gate, config, manager_action_guard, paths
from claudeteam.store import local_facts
from claudeteam.util import env_str, error_exit, pop_bool_flag, pop_flag, usage_error


USAGE = (
    "usage: claudeteam say <agent> [<message>] "
    "[--image <path-or-image_key>] [--attach <path-or-image_key>] "
    "[--reply <message_id>] [--as user|bot] [--no-local] "
    "[--to user|manager|worker_<name>]\n"
    "       use '-' as <message> to read message body from stdin"
)


# Card colors per agent. manager → blue (fixed visual weight, "boss
# answer" channel). Workers auto-cycle through _WORKER_PALETTE in
# team-config order so each worker reads as a distinct color in chat —
# 2026-05-09: previously every worker fell back to "green", making
# multi-worker dispatch cards visually indistinguishable. Per-agent
# `card_color` in claudeteam.toml still wins (override).
_AGENT_CARD_COLORS = {
    "manager": "blue",
}
_WORKER_PALETTE = ("green", "purple", "orange", "yellow")
_EMPTY_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s*$")
_EMPTY_MEDIA_LIST_RE = re.compile(
    r"(截图|图片|标注图|附件)[^。\n]*[：:]\s*(?:[、,，` ]{2,}[。]?|[。])\s*(?:$|\n)")
_IMAGE_FILE_NAME_RE = re.compile(
    r"(截图|图片|标注图|附件)[^。\n]*(?:[\w./-]+\.(?:png|jpe?g|webp))",
    re.IGNORECASE,
)
_LOCAL_ARTIFACT_PATH_RE = re.compile(
    r"`?(?:"
    r"(?:/Users/[^\s`，。；;、)）]+/)?(?:state/)?artifacts/"
    r"|"
    r"artifacts/"
    r")[^\s`，。；;、)）]+`?",
    re.IGNORECASE,
)
_CLI_FLAG_RE = re.compile(r"(?<!\S)--[A-Za-z][\w-]*")
_UI_VISUAL_MARKERS = (
    "ui", "UI", "页面", "截图", "图片", "标注", "视觉", "设计", "验收",
    "样式", "像素", "可视", "首屏",
)
_UI_STRONG_VISUAL_MARKERS = tuple(
    marker for marker in _UI_VISUAL_MARKERS if marker != "验收"
)
_UI_OK_MARKERS = (
    "没问题", "无问题", "没有问题", "没有明显问题", "看起来没问题",
    "大致没问题", "结构没问题", "无结构问题", "可以确认", "可确认",
    "确认没问题", "验收通过", "已验收", "接近确认", "接近门槛",
    "足够给老板", "可以给老板", "完成", "已完成", "交付", "已交付",
    "OK", "ok",
)
_UI_DIRECT_OK_MARKERS = tuple(
    marker for marker in _UI_OK_MARKERS
    if marker not in {"完成", "已完成", "交付", "已交付"}
)
# Only block messages that claim UI *verification*, not just "没问题".
# "页面没问题" is a status update; "UI验收通过" is a verification claim.
_UI_VERIFICATION_CLAIM_MARKERS = (
    "验收通过", "已验收", "可以验收", "可验收", "确认通过",
    "UI验收", "ui验收", "视觉验收", "页面验收", "样式验收",
    "像素验收", "首屏验收", "设计验收", "标注验收",
)
_UI_VISUAL_COMPLETION_RE = re.compile(
    r"(?:UI|ui|页面|截图|图片|视觉|样式|首屏|设计)"
    r"[^。\n]{0,12}(?:完成|已完成|交付|已交付)"
)
# Weak markers that should NOT trigger the image requirement alone.
# Messages like "截图没问题" or "UI OK" are progress updates, not claims.
_UI_WEAK_OK_MARKERS = ("没问题", "OK", "ok", "可以确认", "可确认")
_UI_BLOCKER_MARKERS = (
    "blocker", "Blocker", "阻塞", "卡点", "登录态", "登录页", "白屏",
    "空白", "截图链路失败", "没有可用截图", "缺真实", "缺截图",
    "不能确认", "不得确认", "不建议", "无法截图", "无法验收",
)
_UI_EVIDENCE_META_MARKERS = (
    "证据链", "三件套", "三个文件", "html", "csv", "json",
    "layer", "基线", "哪一批", "哪一层", "唯一口径", "打通",
)
_BOSS_SUMMARY_FIELD_MARKERS = (
    "结论", "状态", "核心产出", "关键结论", "核心判断", "证据",
    "为什么", "今天做", "下一步", "需要老板", "交给谁", "建议",
    "风险", "阻塞", "待验收", "请验收", "无需动作", "不用管",
)
_BOSS_ACTION_MARKERS = (
    "请", "需要", "建议", "验收", "确认", "授权", "转交", "阻塞",
    "待核验", "已核验", "包含", "决定", "不用管", "无需动作",
)
_INTERNAL_TOKEN_PATTERNS = (
    ("internal task id", re.compile(r"\bT-\d+\b")),
    ("internal worker name", re.compile(r"\bworker_[a-z_]+\b")),
    ("internal manager slug", re.compile(r"\bmanager\b")),
    ("internal gate jargon", re.compile(r"\bgate\b|门禁|三棒", re.I)),
    ("internal artifact jargon", re.compile(r"\bartifact\b", re.I)),
    ("internal CLI flag", re.compile(r"--(?:task-id|artifact|done)\b")),
)
_STATUS_ANSWER_MARKERS = (
    "现在什么情况", "现在没有", "当前没有", "活跃任务", "已收口",
    "进度", "待你拍板", "待老板", "下一步",
)
_REALTIME_STATUS_REQUIRED = (
    "实时状态", "当前活跃任务", "最近流水线", "最新待确认产物", "系统健康",
)
_PROGRESS_REPORT_MARKERS = (
    "进度更新", "正在", "初步判断", "预计", "负责人", "下一步", "稍后",
    "下次更新",
)
_PROGRESS_FINAL_CLAIM_MARKERS = (
    "已完成", "任务完成", "交付完成", "已交付", "已通过", "验收通过",
    "已验收", "可以验收", "确认通过",
)
_COMPLETION_CLAIM_RE = re.compile(
    r"(?<!未)(?:任务完成|交付完成|已交付|验收通过|已验收|"
    r"可以验收|可验收|已修好|修好了|完成了|确认通过|"
    r"(?:任务|问题|bug|Bug|BUG|修复|改造|上线|部署|验收|交付|需求|项目|T-\d+)"
    r"[^。\n]{0,20}(?:已完成|已通过))"
)
_COMPLETION_EVIDENCE_MARKERS = (
    "证据", "验证", "测试", "截图", "图片", "预览", "链接", "http://",
    "https://", "日志", "diff", "commit", "PR", "审计路径", "核心产出",
    "产物", "artifact", "Artifact", "T-", "任务号", "复现", "通过率",
    "截图附件", "文件", "路径",
)
_PROGRESS_NEXT_STEP_MARKERS = (
    "预计", "下一步", "下次", "稍后", "分钟后", "负责人", "正在",
)
_PROGRESS_FALLBACK_ALLOWED_REASONS = (
    "boss-visible UI/visual confirmation must attach an image",
    "boss-visible UI/visual confirmation must include a clickable http(s) preview URL",
    "Feishu image send failed",
)
_RESPONSE_CONTRACT_MARKERS = {
    "quick_answer": (),
    "research": (
        "资料", "理论", "依据", "案例", "查到", "来源", "调研", "研究",
        "导师", "刘小排", "根据",
    ),
    "verification": (
        "核对", "验证", "确认", "数据", "日志", "证据", "耗时", "测试",
        "截图", "结果", "链路",
    ),
    "dispatch": (
        "负责人", "派", "分工", "交给", "worker", "员工", "主管", "截止",
        "下一步", "owner",
    ),
    "clarification": (
        "确认", "需要你", "请你", "缺", "具体", "选择", "拍板", "澄清",
    ),
    "blocker": (
        "阻塞", "卡点", "权限", "失败", "无法", "依赖", "需要", "blocker",
        "风险",
    ),
}

# Default emoji per agent name. Used when claudeteam.toml doesn't
# provide an explicit `emoji` field. The card sender header
# (`{emoji} {agent} · {role}`) signals who's talking at a glance.
_DEFAULT_AGENT_EMOJI = {
    "manager": "🎯",
    "worker_cc": "💎",
    "worker_codex": "🟦",
    "worker_kimi": "🟧",
    "worker_gemini": "🟩",
    "worker_qwen": "🟪",
}


def _role_of(name: str) -> str:
    """Map agent name → role bucket used by chat.publish keys.
    Convention: 'manager' → manager; 'worker_*' → worker; 'user' → user;
    anything else → user (safe default; "对老板说" is the most common
    intent when receiver is unrecognized)."""
    if name == "manager":
        return "manager"
    if name == "user" or not name:
        return "user"
    if name.startswith("worker"):
        return "worker"
    return "user"


def _known_sender(name: str) -> bool:
    if not name or name.startswith("--"):
        return False
    try:
        return name in set(config.agent_names())
    except Exception:
        return False


def _is_worker(name: str) -> bool:
    return bool(name) and name.startswith("worker")


def _publish_allowed(sender: str, to_target: str) -> bool:
    """Look up publish rule for sender→receiver, with agent-level override.

    Priority:
      1. team.agents.<sender>.publish_overrides.{key}  (single-agent override)
      2. chat.publish.{key}                             (team-wide tunable)
      3. default True                                    (preserves pre-Step-3 behavior)

    `key` = "{sender_role}_to_{receiver_role}".

    "always" is treated as True — schema uses it as a "don't silence"
    hint but the runtime semantic is just "send".

    Agent-level override is for cases like "I want worker_cc 完工卡进群,
    but worker_codex 完工卡静默" — set worker_codex.publish_overrides
    = {worker_to_user = false} without touching the global rule.
    """
    from claudeteam.runtime import tunables
    sender_role = _role_of(sender)
    receiver_role = _role_of(to_target)
    key = f"{sender_role}_to_{receiver_role}"

    # 1. Agent-level override
    try:
        agent_cfg = config.agent_config(sender)
    except KeyError:
        agent_cfg = {}
    overrides = agent_cfg.get("publish_overrides") or {}
    if key in overrides:
        v = overrides[key]
        return v == "always" or bool(v)

    # 2. Global tunable
    val = tunables.tunable(f"chat.publish.{key}", True)
    if val == "always":
        return True
    return bool(val)


def _internal_worker_ack_reason(sender: str, to_target: str,
                                message: str) -> str:
    """Return a silence reason for low-signal worker public chatter.

    `claudeteam say` always posts to the team chat when it is allowed by
    publish rules. The `--to` value is an intent tag, not a private DM.
    That means a worker can accidentally turn an internal manager loop
    into a boss-visible card. Keep the guard narrow: only suppress obvious
    alignment / standby / loop-monitoring acknowledgements. Real worker
    direct reports still pass through when they contain a deliverable,
    blocker, boss action, URL, screenshot, artifact, or commit evidence.
    """
    if not _is_worker(sender):
        return ""
    text = str(message or "").strip()
    if not text:
        return ""

    receiver_role = _role_of(to_target)
    # Only apply the content guard to boss-visible worker cards. Teams
    # that want to forbid public worker→manager cards should set
    # [chat.publish].worker_to_manager = false; overrides still work.
    if receiver_role != "user":
        return ""

    public_markers = (
        "artifact", "Artifact", "截图", "录屏", "链接", "http://", "https://",
        "commit", "diff", "已提交", "已落盘", "已创建", "已修复", "已验证",
        "复现", "根因", "blocker", "Blocker", "阻塞", "卡点", "失败",
        "报错", "需要老板", "请老板", "授权", "扫码", "确认", "补材料",
        "P0", "P1",
    )
    if any(marker in text for marker in public_markers):
        return ""

    internal_markers = (
        "对齐", "待命", "保持", "按你口径", "收到", "继续只监控",
        "只监控", "不重复回报", "三类触发条件", "其余场景",
        "其他场景", "主线推进", "保持主线", "等待老板确认",
        "待老板确认", "回你", "回我", "ready", "health 视为",
        "不再阻塞", "继续作为主线",
    )
    strong_markers = (
        "对齐", "待命", "继续只监控", "不重复回报", "三类触发条件", "按你口径",
    )
    hits = [marker for marker in internal_markers if marker in text]
    if any(marker in text for marker in strong_markers) or len(hits) >= 2:
        return "worker internal alignment/standby update was silenced; manager should summarize if needed"
    return ""


def _boss_visible_quality_error(to_target: str, message: str, *,
                                image: str = "") -> str:
    """Catch malformed public cards before they reach the boss.

    This is intentionally narrow. It blocks shapes that are almost always
    generation/escaping failures in boss-visible messages: empty numbered
    bullets (`1.` with no text), empty screenshot lists (`截图：、、`), and
    double-escaped layout tokens that would render as literal `\n`.
    """
    if _role_of(to_target) != "user":
        return ""
    text = str(message or "")
    if not text.strip():
        return ""
    if _cli_flag_only_delivery(text):
        return (
            "boss-visible message only contains CLI flags; write a human "
            "summary with status, key result, evidence, next step, and boss "
            "action instead"
        )
    if (_require_visual_status_image_enabled()
            and _looks_like_status_answer(text)
            and not image):
        return (
            "boss-visible status answer must attach a visual field-report image; "
            "generate the team's visual status artifact and send it with --image"
        )
    if (_require_realtime_status_enabled()
            and _looks_like_status_answer(text)
            and not image):
        missing = [marker for marker in _REALTIME_STATUS_REQUIRED if marker not in text]
        if missing:
            return (
                "boss-visible status answer must use the realtime workflow "
                f"card shape; missing: {', '.join(missing)}. Run the "
                "team realtime status generator and gate before sending"
            )
    if _reject_internal_tokens_enabled():
        leaks = _boss_visible_internal_token_leaks(text)
        if leaks:
            return (
                "boss-visible message leaks internal execution jargon "
                f"({', '.join(leaks)}); translate it to human-readable "
                "status, evidence, next step, and boss decision"
            )
    if _EMPTY_LIST_ITEM_RE.search(text):
        return "boss-visible message has an empty list item; regenerate it before sending"
    progress_error = _natural_progress_report_error(text)
    if progress_error:
        return progress_error
    if _looks_like_natural_progress_report(text):
        return ""
    completion_error = _completion_claim_evidence_error(text)
    if completion_error:
        return completion_error
    if _EMPTY_MEDIA_LIST_RE.search(text):
        return "boss-visible message has an empty image/screenshot list; attach or name the images before sending"
    if not image and _IMAGE_FILE_NAME_RE.search(text):
        return "boss-visible message names an image file but does not attach it; send with --image"
    if _path_only_delivery(text, image=image):
        return (
            "boss-visible delivery only gives a local artifact path; add status, "
            "core output, accessible link/screenshot if needed, next step, and keep "
            "the path only as an audit index"
        )
    meta_evidence_answer = any(marker in text for marker in _UI_EVIDENCE_META_MARKERS)
    has_visual_context = _has_ui_visual_context(text)
    has_blocker_context = any(marker in text for marker in _UI_BLOCKER_MARKERS)
    if not image:
        # Only block when the text makes a verification-level claim (e.g.
        # "UI验收通过"), not a weak status update ("页面没问题"/"截图OK").
        # Weak OK markers are progress updates, not visual confirmations.
        if (has_visual_context
                and not _has_only_weak_ok_claim(text)
                and _has_ui_verification_claim(text)
                and not has_blocker_context
                and not meta_evidence_answer):
            return "boss-visible UI/visual confirmation must attach an image; otherwise report it as a blocker"
    else:
        if (has_visual_context
                and not _has_only_weak_ok_claim(text)
                and _has_ui_verification_claim(text)
                and not has_blocker_context
                and not artifact_gate.preview_urls(text)
                and not meta_evidence_answer):
            return "boss-visible UI/visual confirmation must include a clickable http(s) preview URL"
    if "\\\\n" in text or "\\\\t" in text:
        return "boss-visible message still contains double-escaped layout tokens; normalize newlines before sending"
    return ""


def _looks_like_natural_progress_report(text: str) -> bool:
    compact = str(text or "").strip()
    if not compact:
        return False
    hits = [marker for marker in _PROGRESS_REPORT_MARKERS if marker in compact]
    if compact.startswith("进度更新"):
        return True
    return len(hits) >= 2


def _natural_progress_report_error(text: str) -> str:
    if not _looks_like_natural_progress_report(text):
        return ""
    if any(marker in text for marker in _PROGRESS_FINAL_CLAIM_MARKERS):
        return (
            "natural progress update must not claim completion/pass/acceptance; "
            "send it as current state plus next update time"
        )
    if not any(marker in text for marker in _PROGRESS_NEXT_STEP_MARKERS):
        return (
            "natural progress update must include current action plus next update "
            "time/owner"
        )
    return ""


def _completion_claim_evidence_error(text: str) -> str:
    if not _COMPLETION_CLAIM_RE.search(str(text or "")):
        return ""
    if any(marker in text for marker in _COMPLETION_EVIDENCE_MARKERS):
        return ""
    return (
        "completion/acceptance claim must include verification evidence "
        "(test/log/screenshot/link/diff/artifact) or be reported as 待验证"
    )


def _can_send_progress_fallback(agent: str, to_target: str, reason: str) -> bool:
    if agent != "manager" or _role_of(to_target) != "user":
        return False
    text = str(reason or "")
    return any(marker in text for marker in _PROGRESS_FALLBACK_ALLOWED_REASONS)


def _natural_progress_fallback(reason: str, *, image: str = "") -> str:
    if image:
        evidence = "这条回复涉及图片/截图证据，图片发送或预览校验遇到问题。"
    else:
        evidence = "这条回复涉及图片/截图/预览证据，当前可见证据还没补齐。"
    if "preview" in str(reason).lower() or "预览" in str(reason):
        action = "正在补可点击预览链接和可查看截图"
    elif "image" in str(reason).lower() or "图片" in str(reason) or "截图" in str(reason):
        action = "正在补可达图片或压缩后的截图"
    else:
        action = "正在补齐老板可直接查看的证据"
    return (
        f"进度更新：{evidence}"
        f"当前动作：{action}，先不把这条消息当作正式结论发送。"
        "预计 5 分钟内补发完整报告，或明确说明卡点和需要谁处理。"
        "负责人：manager。"
    )


def _reject_internal_tokens_enabled() -> bool:
    from claudeteam.runtime import tunables
    return bool(tunables.tunable(
        "chat.visible_quality_guard.reject_internal_tokens", False))


def _require_realtime_status_enabled() -> bool:
    from claudeteam.runtime import tunables
    return bool(tunables.tunable(
        "chat.visible_quality_guard.require_realtime_status_card", False))


def _require_visual_status_image_enabled() -> bool:
    from claudeteam.runtime import tunables
    return bool(tunables.tunable(
        "chat.visible_quality_guard.require_visual_status_image", False))


def _boss_visible_internal_token_leaks(text: str) -> list[str]:
    leaks: list[str] = []
    for label, pattern in _INTERNAL_TOKEN_PATTERNS:
        if pattern.search(text):
            leaks.append(label)
    return leaks


def _looks_like_status_answer(text: str) -> bool:
    hits = [marker for marker in _STATUS_ANSWER_MARKERS if marker in text]
    if "实时状态" in text:
        return True
    return len(hits) >= 2


def _path_only_delivery(text: str, *, image: str = "") -> bool:
    """Reject public "done, see artifacts/..." handoffs.

    Local artifact paths are useful audit indexes, but they are not a
    boss-consumable delivery surface. Keep this guard narrow: allow a path
    when the same message has a human summary, a clickable URL, or an attached
    image. Block only the common path-only completion shape.
    """
    if not _LOCAL_ARTIFACT_PATH_RE.search(text):
        return False
    if image or artifact_gate.preview_urls(text):
        return False
    return not _has_boss_summary(text)


def _cli_flag_only_delivery(text: str) -> bool:
    """Reject leaked command flags like `- --task-id T-3` in public cards.

    This catches the common LLM mistake of using `claudeteam say` as if it
    accepted `send` flags. A real boss-facing update should be natural
    language; task ids and artifacts belong inside a short audit line, not as
    the whole card body.
    """
    lines = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Feishu renders a leading "- --task-id T-3" as a bullet, so strip
        # only the list marker before checking for raw CLI flags.
        line = re.sub(r"^[-*•]\s+", "", line).strip()
        if line:
            lines.append(line)
    if not lines:
        return False
    joined = " ".join(lines)
    if not _CLI_FLAG_RE.search(joined):
        return False
    if not joined.startswith("--"):
        return False
    return not _has_boss_summary(text)


def _has_ui_visual_context(text: str) -> bool:
    if any(marker in text for marker in _UI_STRONG_VISUAL_MARKERS):
        return True
    compact = re.sub(r"\s+", "", text)
    return any(marker in compact for marker in ("UI验收", "ui验收", "视觉验收", "页面验收"))


def _has_ui_ok_claim(text: str) -> bool:
    if any(marker in text for marker in _UI_DIRECT_OK_MARKERS):
        return True
    return _UI_VISUAL_COMPLETION_RE.search(text) is not None


def _has_ui_verification_claim(text: str) -> bool:
    """True when the text claims UI *verification*, not just a casual 'OK'.
    '页面没问题' is a progress update; 'UI验收通过' is a verification claim.
    Only verification claims trigger the image-required guard."""
    if any(marker in text for marker in _UI_VERIFICATION_CLAIM_MARKERS):
        return True
    return _UI_VISUAL_COMPLETION_RE.search(text) is not None


def _has_only_weak_ok_claim(text: str) -> bool:
    """True when the only OK markers are weak ones like '没问题'/'OK'.
    These should NOT trigger the image requirement — they're status updates."""
    has_weak = any(marker in text for marker in _UI_WEAK_OK_MARKERS)
    has_strong = any(
        marker in text for marker in _UI_DIRECT_OK_MARKERS
        if marker not in _UI_WEAK_OK_MARKERS
    )
    has_visual_completion = _UI_VISUAL_COMPLETION_RE.search(text) is not None
    return has_weak and not has_strong and not has_visual_completion


def _has_boss_summary(text: str) -> bool:
    pathless = _LOCAL_ARTIFACT_PATH_RE.sub("", text)
    marker_hits = {marker for marker in _BOSS_SUMMARY_FIELD_MARKERS if marker in text}
    if len(marker_hits) >= 2:
        return True

    compact = re.sub(r"[`*_#>\[\]()（）:：,，。；;\-—\s/\\]+", "", pathless)
    if len(compact) < 30:
        return False
    return any(marker in text for marker in _BOSS_ACTION_MARKERS)


def _color_for(agent: str, cfg_color: str | None = None) -> str:
    """Resolve card header color. Per-agent `card_color` (or legacy
    `color`) in claudeteam.toml wins; else manager → blue (fixed);
    else worker_* → cycle through `_WORKER_PALETTE` in team-config
    order so multiple workers' cards are visually distinct; else
    fallback blue."""
    if cfg_color:
        return cfg_color
    if agent in _AGENT_CARD_COLORS:
        return _AGENT_CARD_COLORS[agent]
    if agent.startswith("worker"):
        try:
            agents = config.load_team().get("agents", {}) or {}
            workers = [n for n in agents if n != "manager" and n.startswith("worker")]
            idx = workers.index(agent) if agent in workers else 0
        except Exception:
            idx = 0
        return _WORKER_PALETTE[idx % len(_WORKER_PALETTE)]
    return "blue"


def _emoji_for(agent: str, cfg_emoji: str | None = None) -> str:
    """Resolve sender emoji. team.json `emoji` field wins, otherwise
    fall back to `_DEFAULT_AGENT_EMOJI`, otherwise ⚙️ (system)."""
    if cfg_emoji:
        return cfg_emoji
    return _DEFAULT_AGENT_EMOJI.get(agent, "⚙️")


def _agent_card_title(agent: str, cfg: dict) -> str:
    """Card title format ported from `main`'s `_agent_card_title`:
    `{emoji} {agent} · {role}` — English agent id + Chinese role at a
    glance, no more bare `[agent]` brackets that boss flagged as too
    bland."""
    emoji = _emoji_for(agent, cfg.get("emoji"))
    role = cfg.get("role") or "系统"
    return f"{emoji} {agent} · {role}"


def _escape_card_body(text: str) -> str:
    """Protect Feishu markdown from swallowing angle-bracket placeholders.

    Feishu card markdown can treat `<...>` as markup-ish content, which
    makes snippets like `<server>` or `<public>` disappear in cards.
    Escaping only the body keeps the visible text while preserving
    markdown formatting elsewhere.
    """
    return html.escape(text, quote=False)


def _audit_content(message: str, image: str) -> str:
    content = message
    if image:
        image_note = f"[image] {image}"
        content = f"{content}\n{image_note}".strip() if content else image_note
    return content


def _append_log_best_effort(agent: str, kind: str, content: str, *,
                            ref: str = "") -> None:
    try:
        local_facts.append_log(agent, kind, content, ref=ref)
    except OSError as e:
        print(f"  ⚠️ audit log write failed for {agent}: {e}",
              file=sys.stderr)


@dataclass(frozen=True)
class _ResponseContractCheck:
    row: dict | None = None
    original_ok: bool = True
    adjusted: bool = False
    note: str = ""


def _apply_response_contract_guard(args: "_Args", message: str) -> tuple[str, _ResponseContractCheck]:
    if not args.local:
        return message, _ResponseContractCheck()
    if args.agent != "manager" or _role_of(args.to) != "user":
        return message, _ResponseContractCheck()
    if not str(message or "").strip():
        return message, _ResponseContractCheck()
    try:
        row = local_facts.latest_unfulfilled_response_contract(
            "manager",
            max_age_ms=_response_contract_max_age_ms(),
        )
    except OSError as e:
        print(f"  ⚠️ response contract lookup failed: {e}", file=sys.stderr)
        return message, _ResponseContractCheck()
    if not row:
        return message, _ResponseContractCheck()
    contract = row.get("first_response_contract") or {}
    if _response_contract_addressed(message, contract):
        return message, _ResponseContractCheck(row=row, original_ok=True, note="matched")
    prefix = _response_contract_prefix(contract)
    guarded = f"{prefix}\n\n{message}".strip()
    return guarded, _ResponseContractCheck(
        row=row,
        original_ok=False,
        adjusted=True,
        note="guard_prefix_added",
    )


def _response_contract_max_age_ms() -> int:
    from claudeteam.runtime import tunables
    raw = tunables.tunable("chat.response_contract.max_age_ms", 6 * 60 * 60 * 1000)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 6 * 60 * 60 * 1000


def _response_contract_addressed(message: str, contract: dict) -> bool:
    text = str(message or "")
    if not text.strip():
        return False
    ctype = str(contract.get("type") or "quick_answer").strip()
    if ctype == "quick_answer":
        return len(text.strip()) >= 8
    next_step = str(contract.get("next_step") or "").strip()
    if next_step and next_step in text:
        return True
    if _meaningful_next_step_overlap(text, next_step):
        return True
    markers = _RESPONSE_CONTRACT_MARKERS.get(ctype, ())
    low = text.lower()
    return any(marker in text or marker.lower() in low for marker in markers)


def _meaningful_next_step_overlap(message: str, next_step: str) -> bool:
    if not next_step:
        return False
    tokens = re.findall(r"[A-Za-z0-9_./:-]{3,}|[\u4e00-\u9fff]{2,}", next_step)
    if not tokens:
        return False
    hits = [tok for tok in tokens if tok in message]
    return len(hits) >= 2 or (len(hits) == 1 and len(tokens) <= 2)


def _response_contract_prefix(contract: dict) -> str:
    step = " ".join(str(contract.get("next_step") or "").strip().split())
    if not step:
        step = "补上刚才首响承诺的下一步"
    return f"先把刚才首响承诺的下一步对齐：{step}。"


def _record_response_contract_result(check: _ResponseContractCheck, *,
                                     ref: str = "") -> None:
    if not check.row:
        return
    local_id = str(check.row.get("local_id") or "")
    contract = check.row.get("first_response_contract") or {}
    if not local_id:
        return
    try:
        local_facts.mark_response_contract_fulfilled(
            local_id,
            ok=True,
            note=check.note,
            response_message_id=ref,
        )
    except OSError as e:
        print(f"  ⚠️ response contract mark failed: {e}", file=sys.stderr)
        return
    detail = (
        f"trace={local_id}; type={contract.get('type', '')}; "
        f"next_step={contract.get('next_step', '')}; note={check.note}; "
        f"original_ok={check.original_ok}; response_message_id={ref}"
    )
    _append_log_best_effort("manager", "response_contract_fulfilled", detail, ref=local_id)
    if check.adjusted:
        _append_log_best_effort("manager", "response_contract_guarded", detail, ref=local_id)


def _record_send_failure(args: "_Args", message: str, chat: str,
                         profile: str, reason: str) -> None:
    """Persist a failed boss-visible send as a blocker, not a fake `say`.

    The old flow wrote a `say` audit row before calling Feishu. When lark-cli
    later returned HTTP 400, logs looked like the agent had replied even
    though the boss never saw it. This helper records the attempt as
    `say_failed` and puts a high-priority message back into manager's inbox so
    the team has to close the communication gap explicitly.
    """
    if not args.local:
        return
    content = _audit_content(message, args.image)
    detail = (
        f"{reason}\n"
        f"to={args.to} chat_id={chat} profile={profile or '(default)'}\n"
        f"content={content}"
    )
    _append_log_best_effort(args.agent, "say_failed", detail)
    blocker = (
        f"Feishu 回群失败，老板可能没收到 {args.agent} 的回复。\n"
        f"原因：{reason}\n"
        f"发送目标：{args.agent} -> {args.to}；profile={profile or '(default)'}；chat_id={chat}\n"
        f"原回复：{content}\n"
        "处理要求：不要把原用户消息当已完成；先修复发送上下文/权限，"
        "或用明确可达通道补发真实回执。"
    )
    try:
        local_facts.append_message("manager", "system", blocker, priority="高")
    except OSError as e:
        print(f"  ⚠️ failure escalation write failed for manager: {e}",
              file=sys.stderr)


def _record_progress_fallback(args: "_Args", original: str,
                              fallback: str, reason: str) -> None:
    if not args.local:
        return
    original_content = _audit_content(original, args.image)
    _append_log_best_effort(
        args.agent,
        "say_progress_fallback",
        f"reason={reason}\nfallback={fallback}\noriginal={original_content}",
        ref="chat.publish.natural_progress_fallback",
    )
    blocker = (
        "老板可见正式回复被图片/预览类门禁拦住，已先发自然语言进度汇报，"
        "但原完整回复还必须补证据后正式收口。\n"
        f"门禁原因：{reason}\n"
        f"已发进度汇报：{fallback}\n"
        f"原回复：{original_content}\n"
        "处理要求：补齐图片/截图/预览链接，或在 5 分钟内回一个真实 blocker；"
        "不要把进度汇报当作最终验收。"
    )
    try:
        local_facts.append_message("manager", "system", blocker, priority="高")
    except OSError as e:
        print(f"  ⚠️ progress fallback escalation write failed for manager: {e}",
              file=sys.stderr)


def _send_visible_card_or_text(args: "_Args", body: str, chat: str,
                               profile: str, agent_cfg: dict) -> dict | None:
    title = _agent_card_title(args.agent, agent_cfg)
    cfg_color = agent_cfg.get("card_color") or agent_cfg.get("color")
    card = simple_card(
        title,
        _escape_card_body(body),
        color=_color_for(args.agent, cfg_color),
    )
    result = feishu_chat.send_card(
        chat,
        card,
        profile=profile,
        as_user=args.as_user,
    )
    if result is None:
        result = feishu_chat.send_text(
            chat,
            body,
            profile=profile,
            as_user=args.as_user,
        )
        if result is not None:
            print("  ⚠️ card send failed; plain-text fallback posted")
    return result


@dataclass(frozen=True)
class _Args:
    agent: str
    message: str = ""
    image: str = ""
    reply_to: str = ""
    as_user: bool = False
    local: bool = True
    to: str = "user"   # receiver hint for chat.publish filter; default
                       # "user" preserves backwards-compat for callers
                       # that don't pass --to (manager → user is the
                       # typical case)


def _parse(argv: list[str]) -> _Args | None:
    if len(argv) < 2:
        return None
    rest = list(argv)
    # `--card` / `--no-card` are accepted but ignored — every
    # `claudeteam say` posts a v2 card. The flags are consumed for
    # backwards-compat with operators / docs that still pass them.
    pop_bool_flag(rest, "--card")
    pop_bool_flag(rest, "--no-card")
    no_local = pop_bool_flag(rest, "--no-local")
    reply_to = pop_flag(rest, "--reply") or ""
    image = pop_flag(rest, "--image") or ""
    attach = pop_flag(rest, "--attach") or ""
    if image and attach:
        return None
    image = image or attach
    as_explicit = pop_flag(rest, "--as")
    to_explicit = pop_flag(rest, "--to") or "user"
    if "--reply" in rest or "--as" in rest or "--to" in rest or "--image" in rest or "--attach" in rest:
        return None  # flag present but value missing
    if len(rest) < 1:
        return None
    agent = rest[0]
    rest = rest[1:]
    # `feishu.send_as` cascade: --as flag > legacy env > tunable > "bot" default.
    if as_explicit is not None:
        as_value = as_explicit
    else:
        legacy = env_str("CLAUDETEAM_LARK_SEND_AS")
        if legacy:
            as_value = legacy
        else:
            from claudeteam.runtime import tunables
            as_value = str(tunables.tunable("feishu.send_as", "bot"))
    if not rest and not image:
        return None
    return _Args(
        agent=agent,
        message=" ".join(rest),
        image=image,
        reply_to=reply_to,
        as_user=(as_value == "user"),
        local=not no_local,
        to=to_explicit,
    )


def _message_body(raw: str) -> str:
    """Resolve the user-provided message argument.

    A single '-' follows the common CLI convention: read the actual
    message body from stdin. This lets agents safely send generated
    report files with `cat report.md | claudeteam say worker_x - --to user`.
    """
    if raw == "-":
        return sys.stdin.read().strip()
    return raw


def _dedup_state_path() -> Path:
    return paths.state_file("say-dedup.json")


def _normalize_for_dedup(text: str) -> str:
    """Collapse whitespace and lowercase for fingerprint comparison."""
    return " ".join(str(text or "").strip().lower().split())


def _is_duplicate_message(agent: str, message: str,
                          *, window_s: int = 300,
                          read_json=None) -> bool:
    """True if `agent` sent a near-identical message within `window_s`.

    Dedup is intentionally strict (exact normalized match) and short-window
    to avoid suppressing legitimate follow-ups. The goal is catching the
    common "收到" × N pattern, not blocking real conversation.
    """
    if not str(message or "").strip():
        return False
    from claudeteam.runtime import tunables
    if not bool(tunables.tunable("say.dedup.enabled", True)):
        return False
    window_s = int(tunables.tunable("say.dedup.window_s", 300))
    min_length = int(tunables.tunable("say.dedup.min_length", 4))
    normalized = _normalize_for_dedup(message)
    if len(normalized) < min_length:
        return False

    fp = hashlib.sha256(f"{agent}\n{normalized}".encode()).hexdigest()[:16]
    now = time.time()
    path = _dedup_state_path()
    state: dict[str, float] = {}
    if read_json is not None:
        state = read_json(path) or {}
    else:
        try:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    if not isinstance(state, dict):
        state = {}

    last = state.get(fp, 0.0)
    if last and now - last < window_s:
        return True

    # Record this message
    state[fp] = now
    # Prune entries older than 2x window
    cutoff = now - window_s * 2
    state = {k: v for k, v in state.items() if v > cutoff}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return False


def main(argv: list[str]) -> int:
    args = _parse(argv)
    if args is None:
        return usage_error(USAGE)
    if not _known_sender(args.agent):
        return error_exit(
            f"❌ unknown sender agent: {args.agent}. "
            "Use a configured team agent name such as manager or worker_xxx."
        )
    message = _normalize_visible_escapes(_message_body(args.message))
    if args.message == "-" and not message and not args.image:
        return error_exit("❌ empty stdin message for `claudeteam say <agent> -`")

    if _is_duplicate_message(args.agent, message):
        if args.local:
            _append_log_best_effort(
                args.agent, "say_deduped", _audit_content(message, args.image),
                ref="say.dedup",
            )
        print(f"📝 {args.agent} → deduped (near-identical message sent recently)")
        return 0

    chat = config.chat_id()
    if not chat:
        return error_exit("❌ chat_id not set in runtime_config.json")

    profile = config.lark_profile()

    local_facts.touch_heartbeat(args.agent)

    # Resolve agent's role + emoji + color from claudeteam.toml. Used
    # for the card title (`{emoji} {agent} · {role}`) and for color
    # override. Missing config falls back to the per-agent default
    # tables defined at the top of this file.
    try:
        agent_cfg = config.agent_config(args.agent)
    except KeyError:
        agent_cfg = {}

    # Every `claudeteam say` sends a v2 card. `reply_to` is silently
    # ignored because Feishu interactive cards don't thread.
    if args.reply_to:
        print(f"  ⚠️ --reply ignored (Feishu cards don't thread)",
              file=sys.stderr)
    title = _agent_card_title(args.agent, agent_cfg)
    # `card_color` is the new field name (more specific than just "color");
    # fall back to legacy "color" so old team.json keeps working.
    cfg_color = agent_cfg.get("card_color") or agent_cfg.get("color")

    # Step 3: chat.publish filter — operator can silence specific
    # sender→receiver channels via toml (default all true = preserve
    # pre-Step-3 behavior). Silenced messages still leave a trail, but
    # not as `say` because they were intentionally not boss-visible.
    internal_ack_reason = _internal_worker_ack_reason(args.agent, args.to, message)
    if internal_ack_reason:
        if args.local:
            _append_log_best_effort(
                args.agent, "say_silenced", _audit_content(message, args.image),
                ref="chat.publish.internal_worker_ack",
            )
        print(f"📝 {args.agent} → silenced: {internal_ack_reason}; logged only")
        return 0

    if not _publish_allowed(args.agent, args.to):
        from claudeteam.runtime import tunables
        sender_role = _role_of(args.agent)
        receiver_role = _role_of(args.to)
        key = f"chat.publish.{sender_role}_to_{receiver_role}"
        if args.local:
            _append_log_best_effort(
                args.agent, "say_silenced", _audit_content(message, args.image),
                ref=key,
            )
        print(f"📝 {args.agent} → silenced by [{key}]=false; logged only")
        return 0

    message, contract_check = _apply_response_contract_guard(args, message)

    quality_error = _boss_visible_quality_error(args.to, message, image=args.image)
    if quality_error:
        # L5 self-evolution: capture quality guard block as an incident
        try:
            from claudeteam.runtime import incident_learning
            incident_learning.capture(
                incident_learning.from_quality_guard(args.agent, quality_error))
        except Exception:
            pass
        if _can_send_progress_fallback(args.agent, args.to, quality_error):
            fallback = _natural_progress_fallback(quality_error, image=args.image)
            fallback_error = _boss_visible_quality_error(
                args.to, fallback, image="")
            if fallback_error:
                return error_exit(
                    f"❌ {quality_error}; generated progress fallback was invalid: "
                    f"{fallback_error}"
                )
            result = _send_visible_card_or_text(args, fallback, chat, profile, agent_cfg)
            if result is None:
                _record_send_failure(
                    args, fallback, chat, profile,
                    f"Feishu progress fallback send failed for {args.agent}",
                )
                return error_exit(
                    f"❌ {quality_error}; progress fallback send failed"
                )
            _record_progress_fallback(args, message, fallback, quality_error)
            if args.local:
                _append_log_best_effort(
                    args.agent, "say", fallback,
                    ref=result.get("message_id", ""),
                )
            if args.agent == "manager" and _role_of(args.to) == "user":
                manager_action_guard.mark_boss_say(
                    fallback, ref=result.get("message_id", ""))
            print(
                f"✅ {args.agent} → chat "
                f"(progress_update_id={result.get('message_id', '')})"
            )
            return 0
        if args.local:
            _append_log_best_effort(
                args.agent, "say_blocked", _audit_content(message, args.image),
                ref="chat.publish.visible_quality_guard",
            )
        return error_exit(f"❌ {quality_error}")

    image_result = None
    if args.image:
        image_result = feishu_chat.send_image(
            chat, args.image,
            profile=profile,
            as_user=args.as_user,
        )
        if image_result is None:
            reason = f"Feishu image send failed for {args.agent}"
            if _can_send_progress_fallback(args.agent, args.to, reason):
                fallback = _natural_progress_fallback(reason, image=args.image)
                result = _send_visible_card_or_text(
                    args, fallback, chat, profile, agent_cfg)
                if result is not None:
                    _record_send_failure(args, message, chat, profile, reason)
                    _record_progress_fallback(args, message, fallback, reason)
                    if args.local:
                        _append_log_best_effort(
                            args.agent, "say", fallback,
                            ref=result.get("message_id", ""),
                        )
                    manager_action_guard.mark_boss_say(
                        fallback, ref=result.get("message_id", ""))
                    print(
                        f"✅ {args.agent} → chat "
                        f"(progress_update_id={result.get('message_id', '')})"
                    )
                    return 0
            _record_send_failure(args, message, chat, profile,
                                 reason)
            return error_exit(f"❌ Feishu image send failed for {args.agent}")

    result = {}
    if message:
        card = simple_card(title, _escape_card_body(message),
                           color=_color_for(args.agent, cfg_color))
        result = feishu_chat.send_card(
            chat, card,
            profile=profile,
            as_user=args.as_user,
        )
        if result is None:
            result = feishu_chat.send_text(
                chat, message,
                profile=profile,
                as_user=args.as_user,
            )
            if result is None:
                _record_send_failure(args, message, chat, profile,
                                     f"Feishu send failed for {args.agent}")
                return error_exit(f"❌ Feishu send failed for {args.agent}")
            print("  ⚠️ card send failed; plain-text fallback posted")

    image_msg_id = image_result.get("message_id", "") if image_result else ""
    msg_id = result.get("message_id", "") if result else ""
    if args.local:
        # Audit log is best-effort — a disk-full or permission-denied
        # error here should NOT invalidate a message that already landed
        # in the group. The log is written only after successful Feishu
        # delivery so local history cannot pretend a failed reply was sent.
        audit_ref = msg_id or image_msg_id
        _append_log_best_effort(args.agent, "say",
                                _audit_content(message, args.image),
                                ref=audit_ref)

    if args.agent == "manager" and _role_of(args.to) == "user":
        _record_response_contract_result(
            contract_check,
            ref=msg_id or image_msg_id,
        )
        manager_action_guard.mark_boss_say(
            message, image=args.image, ref=msg_id or image_msg_id)
    if image_msg_id and msg_id:
        print(f"✅ {args.agent} → chat (image_id={image_msg_id}, message_id={msg_id})")
    elif image_msg_id:
        print(f"✅ {args.agent} → chat (image_id={image_msg_id})")
    else:
        print(f"✅ {args.agent} → chat (message_id={msg_id})")
    return 0
