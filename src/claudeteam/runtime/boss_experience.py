"""Boss-experience checks used by self-learning commands.

The goal is to turn the boss' repeated corrections into executable checks:
paths are not delivery, cockpit field names are not mobile UX, mentor prompts
must stay single-entrance, and visual/image claims need visible evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


TEXT_EXTS = {".md", ".txt", ".json", ".log"}
LOCAL_PATH_RE = re.compile(
    r"(?:/Users/[^\s`，。；;、)）]+/)?(?:state/)?artifacts/"
    r"[^\s`，。；;、)）]+|"
    r"artifacts/[^\s`，。；;、)）]+"
)
SUMMARY_MARKERS = (
    "交付物", "核心结论", "结论", "当前状态", "状态", "下一步",
    "老板动作", "需要老板", "验收", "完成证据", "风险", "阻塞",
)
ACTION_MARKERS = ("下一步", "老板动作", "需要老板", "无需老板", "可回", "回复")
DONE_MARKERS = ("完成", "已完成", "已还原", "通过", "一致")
VISUAL_EVIDENCE_MARKERS = ("http://", "https://", ".png", ".jpg", ".jpeg", ".webp", "截图：", "附图")


@dataclass(frozen=True)
class ExperienceIssue:
    code: str
    severity: str
    source: str
    message: str
    excerpt: str


@dataclass(frozen=True)
class CorrectionCase:
    case_id: str
    title: str
    expected_code: str
    bad: str
    good: str


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _has_boss_summary(text: str) -> bool:
    marker_hits = sum(1 for marker in SUMMARY_MARKERS if marker in text)
    if marker_hits >= 2:
        return True
    pathless = LOCAL_PATH_RE.sub("", text)
    compact = re.sub(r"[`*_#>\[\]()（）:：,，。；;\-—\s/\\]+", "", pathless)
    return len(compact) >= 30 and any(marker in text for marker in ACTION_MARKERS)


def _issue(code: str, severity: str, source: str, message: str,
           excerpt: str) -> ExperienceIssue:
    return ExperienceIssue(code, severity, source, message, _clip(excerpt))


def _has_ui_context(text: str) -> bool:
    if any(marker in text for marker in ("页面", "视觉", "截图", "还原", "设计稿")):
        return True
    return bool(re.search(r"(?<![A-Za-z])UI(?![A-Za-z])", text))


def audit_text(text: str, *, source: str = "<text>") -> list[ExperienceIssue]:
    """Return boss-experience issues for one text blob."""
    text = str(text or "")
    issues: list[ExperienceIssue] = []
    if not text.strip():
        return issues

    if LOCAL_PATH_RE.search(text) and not _has_boss_summary(text):
        issues.append(_issue(
            "path_only_delivery", "high", source,
            "local artifact path is being used as the visible delivery",
            LOCAL_PATH_RE.search(text).group(0),
        ))

    if "【老板操作】" in text or "【老板决策】" in text:
        issues.append(_issue(
            "cockpit_field_leak", "medium", source,
            "boss-visible copy leaks cockpit/Base field names; use replyable actions",
            text,
        ))

    compact = re.sub(r"\s+", "", text)
    if ("AI刘小排/AI亦仁" in compact or "刘小排/亦仁" in compact
            or re.search(r"请.*两位导师", text)):
        issues.append(_issue(
            "mixed_mentor_prompt", "high", source,
            "mentor-visible prompt mixes the two mentor entrances",
            text,
        ))
    if "请教 AI 刘小排" in text and ("AI 亦仁" in text or "亦仁" in text):
        issues.append(_issue(
            "single_mentor_other_name", "high", source,
            "single Liu Xiaopai prompt mentions the other mentor",
            text,
        ))
    if "请教 AI 亦仁" in text and ("AI 刘小排" in text or "刘小排" in text):
        issues.append(_issue(
            "single_mentor_other_name", "high", source,
            "single Yiren prompt mentions the other mentor",
            text,
        ))

    if "--image" in text and "--image-caption" not in text and "--image-note" not in text:
        issues.append(_issue(
            "image_without_caption", "high", source,
            "image evidence command is missing --image-caption / --image-note",
            text,
        ))
    if ("## 图片证据" in text and re.search(r"\.(png|jpg|jpeg|webp)\b", text, re.I)
            and "预期画面/证据含义" not in text and "--image-caption" not in text):
        issues.append(_issue(
            "image_without_caption", "high", source,
            "image evidence section lacks expected-content caption",
            text,
        ))

    ui_done_line = next((
        line for line in text.splitlines()
        if _has_ui_context(line) and any(marker in line for marker in DONE_MARKERS)
    ), "")
    if ui_done_line:
        if not any(marker in ui_done_line for marker in VISUAL_EVIDENCE_MARKERS):
            issues.append(_issue(
                "ui_done_without_visual_evidence", "high", source,
                "UI/page completion claim lacks screenshot or preview evidence",
                ui_done_line,
            ))

    if "\\n" in text or "\\t" in text:
        issues.append(_issue(
            "escaped_layout_tokens", "medium", source,
            "boss-visible copy contains escaped layout tokens",
            text,
        ))
    if re.search(r"(?m)^\s*\d+[.、]\s*$", text) or "截图：、、" in text:
        issues.append(_issue(
            "empty_delivery_slot", "medium", source,
            "boss-visible copy has empty list or empty media slot",
            text,
        ))
    return issues


def iter_text_files(paths: list[Path], *, max_files: int = 200) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        path = path.expanduser().resolve()
        if path.is_file() and path.suffix.lower() in TEXT_EXTS:
            files.append(path)
        elif path.is_dir():
            for item in sorted(path.rglob("*")):
                if len(files) >= max_files:
                    return files
                if item.is_file() and item.suffix.lower() in TEXT_EXTS:
                    files.append(item)
        if len(files) >= max_files:
            return files[:max_files]
    return files[:max_files]


def audit_paths(paths: list[Path], *, max_files: int = 200) -> tuple[list[dict], list[ExperienceIssue]]:
    files = iter_text_files(paths, max_files=max_files)
    issues: list[ExperienceIssue] = []
    scanned: list[dict] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        file_issues = audit_text(text, source=str(path))
        scanned.append({"path": str(path), "issues": len(file_issues)})
        issues.extend(file_issues)
    return scanned, issues


CORRECTION_CASES: tuple[CorrectionCase, ...] = (
    CorrectionCase(
        "path_only_artifact",
        "artifact path cannot be the delivery",
        "path_only_delivery",
        "已完成，见 artifacts/T-34/q2-blind-guess-test-pack.md",
        "交付物：Q2 盲测包。核心结论：可发 10 人测试。下一步：等反馈。证据索引：artifacts/T-34/q2-blind-guess-test-pack.md",
    ),
    CorrectionCase(
        "absolute_path_only",
        "absolute local paths are also not delivery",
        "path_only_delivery",
        "交付好了：/Users/wsm/Project/x/artifacts/T-1/report.md",
        "状态：已交付。核心结论：报告可读。老板动作：无需处理。证据索引：/Users/wsm/Project/x/artifacts/T-1/report.md",
    ),
    CorrectionCase(
        "cockpit_field_leak",
        "mobile brief must not leak Base fields",
        "cockpit_field_leak",
        "下拉【老板操作】选重新核验；自定义要求写【老板决策】。",
        "可回：1 重核 / 1 说明 你的要求。",
    ),
    CorrectionCase(
        "mixed_mentor_prompt",
        "two mentors cannot share one visible answer prompt",
        "mixed_mentor_prompt",
        "希望 AI 刘小排 / AI 亦仁回答：这个团队该怎么改？",
        "请教 AI 刘小排：从产品执行视角判断这个团队该怎么改。",
    ),
    CorrectionCase(
        "single_mentor_boundary_noise",
        "single-mentor visible prompt must not mention the other mentor",
        "single_mentor_other_name",
        "请教 AI 刘小排：不要替 AI 亦仁回答，只谈产品执行。",
        "请教 AI 刘小排：请从产品执行视角判断下一轮最小动作。",
    ),
    CorrectionCase(
        "image_cli_missing_caption",
        "image CLI evidence needs caption",
        "image_without_caption",
        "node scripts/mentor-loop-run.cjs --image wrong.png --ask",
        "node scripts/mentor-loop-run.cjs --image wrong.png --image-caption \"截图应显示群里只发了路径\" --ask",
    ),
    CorrectionCase(
        "image_section_missing_caption",
        "image evidence section needs expected meaning",
        "image_without_caption",
        "## 图片证据\n- screenshot.png",
        "## 图片证据\n- screenshot.png\n  - 预期画面/证据含义：应显示老板无法消费路径型回复。",
    ),
    CorrectionCase(
        "ui_done_without_screenshot",
        "UI completion needs screenshot or preview",
        "ui_done_without_visual_evidence",
        "页面还原已完成，和设计稿一致。",
        "页面还原已完成，截图：https://example.com/preview.png，下一步等验收。",
    ),
    CorrectionCase(
        "escaped_newlines",
        "escaped layout tokens are not human-readable",
        "escaped_layout_tokens",
        "结论：已完成\\n下一步：等待确认",
        "结论：已完成\n下一步：等待确认",
    ),
    CorrectionCase(
        "empty_media_slot",
        "empty screenshot slots must be regenerated",
        "empty_delivery_slot",
        "页面证据：截图：、、",
        "页面证据：截图：https://example.com/shot.png",
    ),
)


def run_correction_cases() -> list[dict]:
    rows = []
    for case in CORRECTION_CASES:
        bad_codes = {issue.code for issue in audit_text(case.bad, source=case.case_id + ":bad")}
        good_codes = {issue.code for issue in audit_text(case.good, source=case.case_id + ":good")}
        rows.append({
            "id": case.case_id,
            "title": case.title,
            "expected": case.expected_code,
            "bad_detected": case.expected_code in bad_codes,
            "good_clean": case.expected_code not in good_codes,
            "bad_codes": sorted(bad_codes),
            "good_codes": sorted(good_codes),
            "passed": case.expected_code in bad_codes and case.expected_code not in good_codes,
        })
    return rows
