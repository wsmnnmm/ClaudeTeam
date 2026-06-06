"""Validate worker first-output evidence for manager_watch."""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from claudeteam.runtime import paths, tunables


@dataclass(frozen=True)
class FirstOutputCheck:
    valid: bool
    reason: str
    detail: str = ""


_URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&*+,;=%-]+")
_MARKDOWN_URL_RE = re.compile(r"\[[^\]]+\]\((?P<url>https?://[^)]+)\)")
_REFERENCE_FIELD_RE = re.compile(
    r"(?:artifact|Artifact|产物|链接|URL|url|截图|报告|文件|路径|目录|证据|receipt|日志|"
    r"commit|diff|PR|pr|预览|preview)\s*[:：]\s*(?P<value>.+)"
)
_PATH_RE = re.compile(
    r"(?P<value>(?:/|(?:\./)|(?:\.\./)|(?:[A-Za-z0-9._-]+/))"
    r"[A-Za-z0-9._~!$&'()*+,;=:@%/\-]+)"
)
_BLOCKER_RE = re.compile(
    r"(?:blocker|Blocker|卡点|阻塞|卡住|失败原因|需要授权|需要登录|"
    r"缺少数据|缺少|429|接口报错|报错)\s*[:：]?\s*(?P<detail>.*)"
)
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_EMBEDDED_REFERENCE_CUES = (
    "artifact", "产物", "链接", "url", "截图", "报告", "文件", "路径",
    "目录", "证据", "receipt", "日志", "log", "raw", "已落盘", "已生成",
)
_VAGUE_OUTPUT_MARKERS = (
    "收到", "已收到", "在看", "处理中", "正在处理", "正在生成",
    "生成中", "马上", "稍后", "待会", "继续推进", "有进展再同步",
    "暂无新事实", "资料在整理", "卡住了", "遇到问题", "有点卡",
)


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _clean_ref(value: str) -> str:
    raw = str(value or "").strip()
    md = _MARKDOWN_URL_RE.search(raw)
    if md:
        raw = md.group("url")
    else:
        url = _URL_RE.search(raw)
        if url:
            raw = url.group(0)
    return raw.strip().strip("`'\"<>").rstrip(".,，。；;)")


def _text_is_vague_output(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if not compact:
        return True
    return any(marker in compact for marker in _VAGUE_OUTPUT_MARKERS) and len(compact) < 120


def _task_context(task: dict) -> str:
    return "\n".join([
        str(task.get("title") or ""),
        str(task.get("description") or ""),
        str(task.get("topic") or ""),
    ])


def _looks_like_image_task(task: dict) -> bool:
    text = _task_context(task)
    return any(marker in text for marker in ("图", "图片", "生图", "修图", "封面", "配图", "海报"))


def _looks_like_research_task(task: dict) -> bool:
    text = _task_context(task)
    return any(marker in text for marker in ("研究", "调研", "资料", "策略", "需求", "分析", "复盘", "导师"))


def _looks_like_short_card_task(task: dict) -> bool:
    text = _task_context(task)
    return "短卡" in text


def _looks_like_four_point_task(task: dict) -> bool:
    text = _task_context(task)
    return any(marker in text for marker in ("4 点", "四点", "4点", "4 个", "四个"))


def _allowed_artifact_roots() -> list[Path]:
    roots = []
    candidates = [Path.cwd(), paths.state_dir(), paths.state_dir().parent]
    try:
        candidates.append(paths.config_file().parent)
    except Exception:
        pass
    seen = set()
    for root in candidates:
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)
        for child in ("artifacts", "knowledge-base", "reports", "workspace"):
            roots.append(resolved / child)
    return roots


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _candidate_paths(ref: str) -> list[Path]:
    value = _clean_ref(ref)
    if not value or _URL_RE.match(value):
        return []
    path = Path(value).expanduser()
    if path.is_absolute():
        return [path]
    bases = [Path.cwd(), paths.state_dir().parent]
    try:
        bases.append(paths.config_file().parent)
    except Exception:
        pass
    return [base / path for base in bases]


def _path_usable(ref: str) -> FirstOutputCheck:
    candidates = _candidate_paths(ref)
    if not candidates:
        return FirstOutputCheck(False, "证据字段缺失", "empty path")
    allowed = _allowed_artifact_roots()
    any_allowed = False
    for path in candidates:
        allowed_path = any(_within(path, root) for root in allowed)
        any_allowed = any_allowed or allowed_path
        if allowed_path and path.exists():
            return FirstOutputCheck(True, "ok", str(path))
    if not any_allowed:
        return FirstOutputCheck(False, "路径不合法", _clean_ref(ref))
    return FirstOutputCheck(False, "证据不可用", _clean_ref(ref))


def check_url(url: str, *, timeout_s: float = 2.0) -> tuple[bool, str, str]:
    clean = _clean_ref(url)
    headers = {"User-Agent": "claudeteam-manager-watch/1.0"}
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(clean, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
                content_type = str(resp.headers.get("content-type", ""))
                if 200 <= status < 400:
                    return True, f"http {status}", content_type
                return False, f"http {status}", content_type
        except urllib.error.HTTPError as e:
            if method == "HEAD" and int(e.code or 0) in {405, 501}:
                continue
            return False, f"http {e.code}", str(e.headers.get("content-type", ""))
        except Exception as e:
            if method == "HEAD":
                continue
            return False, type(e).__name__, ""
    return False, "url check failed", ""


def _url_usable(ref: str, task: dict) -> FirstOutputCheck:
    url = _clean_ref(ref)
    ok, detail, content_type = check_url(
        url,
        timeout_s=float(tunables.tunable("manager_watch.first_output_url_timeout_s", 2.0)),
    )
    if not ok:
        return FirstOutputCheck(False, "证据不可用", f"{url} ({detail})")
    if _looks_like_image_task(task):
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in _IMAGE_EXTS and not content_type.lower().startswith("image/"):
            return FirstOutputCheck(False, "证据不符", f"not image: {url}")
    return FirstOutputCheck(True, "ok", url)


def _reference_field_values(text: str) -> list[str]:
    values = []
    for line in str(text or "").splitlines():
        match = _REFERENCE_FIELD_RE.search(line)
        if match:
            values.append(match.group("value"))
    return values


def _embedded_reference_values(text: str) -> list[str]:
    values = []
    for line in str(text or "").splitlines():
        values.extend(_line_reference_values(line))
    return values


def _line_reference_values(line: str) -> list[str]:
    values: list[str] = []
    match = _REFERENCE_FIELD_RE.search(line)
    if match:
        values.append(match.group("value"))

    lowered = line.lower()
    if not any(cue in line or cue in lowered for cue in _EMBEDDED_REFERENCE_CUES):
        return values

    for md in _MARKDOWN_URL_RE.finditer(line):
        values.append(md.group("url"))
    for url in _URL_RE.findall(line):
        values.append(url)
    for path_match in _PATH_RE.finditer(line):
        values.append(path_match.group("value"))

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_ref(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(value)
    return deduped


def check_reference(value: str, task: dict) -> FirstOutputCheck:
    clean = _clean_ref(value)
    if not clean:
        return FirstOutputCheck(False, "证据字段缺失", "empty reference")
    if _URL_RE.match(clean):
        return _url_usable(clean, task)
    return _path_usable(clean)


def _blocker_check(text: str) -> FirstOutputCheck | None:
    match = _BLOCKER_RE.search(str(text or ""))
    if not match:
        return None
    detail = _one_line(match.group("detail") or text)
    has_attempt = any(marker in detail for marker in ("已尝试", "尝试", "重试", "刷新", "检查", "跑过", "失败"))
    has_need = any(marker in detail for marker in ("需要", "请", "等待", "缺少", "授权", "登录", "扫码", "老板"))
    has_next = any(marker in detail for marker in ("分钟", "小时", "点", "今天", "明天", "下次", "后复查", "回报"))
    if len(detail) >= 24 and has_attempt and has_need and has_next and not _text_is_vague_output(detail):
        return FirstOutputCheck(True, "ok", detail)
    return FirstOutputCheck(False, "blocker 不可行动", detail or "empty blocker")


def _structured_summary_check(text: str, task: dict) -> FirstOutputCheck | None:
    if not _looks_like_research_task(task):
        return None
    full = str(text or "")
    has_conclusion = "结论" in full
    has_evidence = "证据" in full or "来源" in full
    has_next = "下一步" in full or "动作" in full
    has_risk = "风险" in full or "反例" in full
    if not (has_conclusion and has_evidence and has_next and has_risk):
        return FirstOutputCheck(False, "结构化摘要不完整", _one_line(full)[:160])
    for url in _URL_RE.findall(full):
        checked = _url_usable(url, task)
        if not checked.valid:
            return checked
    if len(_one_line(full)) < 80:
        return FirstOutputCheck(False, "结构化摘要不完整", _one_line(full))
    return FirstOutputCheck(True, "ok", "structured summary")


def _short_card_check(text: str, task: dict) -> FirstOutputCheck | None:
    if not _looks_like_short_card_task(task):
        return None
    full = str(text or "")
    required = ("直觉结论", "关键线索", "反证", "最小验证动作", "今天会应用到的真实任务")
    hits = sum(1 for marker in required if marker in full)
    if hits >= 4 and len(_one_line(full)) >= 80 and not _text_is_vague_output(full):
        return FirstOutputCheck(True, "ok", "short card")
    return FirstOutputCheck(False, "短卡不完整", _one_line(full)[:160])


def _four_point_check(text: str, task: dict) -> FirstOutputCheck | None:
    if not _looks_like_four_point_task(task):
        return None
    full = str(text or "")
    hits = sum(1 for marker in ("1)", "2)", "3)", "4)") if marker in full)
    if hits < 4:
        hits = sum(1 for marker in ("1.", "2.", "3.", "4.") if marker in full)
    if hits >= 4 and len(_one_line(full)) >= 80 and not _text_is_vague_output(full):
        return FirstOutputCheck(True, "ok", "four point report")
    return FirstOutputCheck(False, "四点现场不完整", _one_line(full)[:160])


def check(task: dict, msg: dict) -> FirstOutputCheck:
    artifact = str(msg.get("artifact") or "").strip()
    if artifact:
        return check_reference(artifact, task)
    text = str(msg.get("content") or "")
    refs = _reference_field_values(text)
    if refs:
        results = [check_reference(ref, task) for ref in refs]
        for result in results:
            if result.valid:
                return result
        return results[0]
    blocker = _blocker_check(text)
    if blocker is not None:
        return blocker
    short_card = _short_card_check(text, task)
    if short_card is not None:
        return short_card
    four_point = _four_point_check(text, task)
    if four_point is not None:
        return four_point
    summary = _structured_summary_check(text, task)
    if summary is not None:
        return summary
    embedded_refs = _embedded_reference_values(text)
    if embedded_refs:
        results = [check_reference(ref, task) for ref in embedded_refs]
        for result in results:
            if result.valid:
                return result
        return results[0]
    if _text_is_vague_output(text):
        return FirstOutputCheck(False, "空话", _one_line(text))
    return FirstOutputCheck(False, "无证据", _one_line(text)[:160])
