"""Shared artifact and UI-evidence gates for task completion."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_TEXT_EXTS = {".md", ".markdown", ".txt", ".html", ".htm", ".json"}
_URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&*+,;=%-]+")
_LOCAL_IMAGE_RE = re.compile(
    r"(?P<path>(?:/|[A-Za-z0-9_.~-])[-A-Za-z0-9_./~]*\.(?:png|jpe?g|webp|gif))",
    re.IGNORECASE,
)

_UI_EXPLICIT_MARKERS = (
    "页面还原", "界面还原", "视觉还原", "ui还原", "UI还原",
    "UI 还原", "ui 还原", "视觉验收", "按图还原",
    "页面改造", "页面验收", "视觉复核",
)


@dataclass(frozen=True)
class UiEvidence:
    required: bool
    has_screenshot: bool
    has_preview_url: bool

    @property
    def missing(self) -> list[str]:
        if not self.required:
            return []
        out: list[str] = []
        if not self.has_screenshot:
            out.append("screenshot image")
        if not self.has_preview_url:
            out.append("http(s) preview URL")
        return out

    @property
    def passed(self) -> bool:
        return not self.missing


def _clean_ref(ref: str) -> str:
    return str(ref or "").strip().strip("`'\"<>").rstrip(".,，。；;")


def _is_image_url(url: str) -> bool:
    parsed = urlparse(_clean_ref(url))
    return Path(parsed.path).suffix.lower() in _IMAGE_EXTS


def preview_urls(text: str) -> list[str]:
    """Return non-image http(s) URLs that can serve as page previews."""
    urls = []
    for url in _URL_RE.findall(str(text or "")):
        clean = _clean_ref(url)
        if clean and not _is_image_url(clean):
            urls.append(clean)
    return urls


def _candidate_paths(ref: str, base_dirs: list[Path] | tuple[Path, ...]) -> list[Path]:
    ref = _clean_ref(ref)
    if not ref:
        return []
    parsed = urlparse(ref)
    if parsed.scheme and parsed.scheme != "file":
        return []
    path = Path(parsed.path if parsed.scheme == "file" else ref).expanduser()
    if path.is_absolute():
        return [path]
    return [Path(base) / path for base in base_dirs]


def existing_artifact_reference(artifact: str,
                                *,
                                base_dirs: list[Path] | tuple[Path, ...]) -> bool:
    """True when artifact points to an existing local ref or any remote URL."""
    artifact = _clean_ref(artifact)
    if not artifact:
        return False
    if _URL_RE.search(artifact):
        return True
    parsed = urlparse(artifact)
    if parsed.scheme and parsed.scheme != "file":
        return True
    return any(path.exists() for path in _candidate_paths(artifact, base_dirs))


def looks_like_ui_work(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return any(marker in text or marker in compact for marker in _UI_EXPLICIT_MARKERS)


def _read_text(path: Path) -> str:
    if path.suffix.lower() not in _TEXT_EXTS:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _existing_image_from_text(text: str,
                              *,
                              base_dirs: list[Path] | tuple[Path, ...],
                              relative_to: Path | None = None) -> bool:
    for url in _URL_RE.findall(text):
        if _is_image_url(url):
            return True
    bases = list(base_dirs)
    if relative_to is not None:
        bases.insert(0, relative_to)
    for match in _LOCAL_IMAGE_RE.finditer(text):
        ref = match.group("path")
        if any(path.exists() for path in _candidate_paths(ref, bases)):
            return True
    return False


def _artifact_paths(artifact: str,
                    base_dirs: list[Path] | tuple[Path, ...]) -> list[Path]:
    return [path for path in _candidate_paths(artifact, base_dirs) if path.exists()]


def ui_evidence(artifact: str,
                *,
                context_text: str = "",
                base_dirs: list[Path] | tuple[Path, ...]) -> UiEvidence:
    """Inspect a completion artifact for UI screenshot + preview evidence."""
    context = f"{context_text}\n{artifact}"
    required = looks_like_ui_work(context)
    has_preview_url = bool(preview_urls(context))
    has_screenshot = _existing_image_from_text(context, base_dirs=base_dirs)

    for path in _artifact_paths(artifact, base_dirs):
        if path.is_file():
            if path.suffix.lower() in _IMAGE_EXTS:
                has_screenshot = True
            text = _read_text(path)
            if text:
                has_preview_url = has_preview_url or bool(preview_urls(text))
                has_screenshot = (
                    has_screenshot
                    or _existing_image_from_text(
                        text, base_dirs=base_dirs, relative_to=path.parent)
                )
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix.lower() in _IMAGE_EXTS:
                    has_screenshot = True
                if child.is_file() and child.suffix.lower() in _TEXT_EXTS:
                    text = _read_text(child)
                    if text:
                        has_preview_url = has_preview_url or bool(preview_urls(text))
                        has_screenshot = (
                            has_screenshot
                            or _existing_image_from_text(
                                text, base_dirs=base_dirs, relative_to=child.parent)
                        )
                if has_preview_url and has_screenshot:
                    break
        if has_preview_url and has_screenshot:
            break
    return UiEvidence(
        required=required,
        has_screenshot=has_screenshot,
        has_preview_url=has_preview_url,
    )
