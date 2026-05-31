"""Tests for shared artifact / UI evidence gates."""
from __future__ import annotations

import tempfile
from pathlib import Path

from claudeteam.runtime import artifact_gate


def test_ui_evidence_requires_real_image_and_non_image_preview_url():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        report = tmp_path / "artifacts" / "T-1" / "report.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# 页面还原交付\n\n"
            "预览：https://example.com/#/dashboard\n"
            "截图：missing.png\n",
            encoding="utf-8",
        )

        evidence = artifact_gate.ui_evidence(
            str(report),
            context_text="页面还原 /dashboard",
            base_dirs=[tmp_path],
        )

    assert evidence.required is True
    assert evidence.has_preview_url is True
    assert evidence.has_screenshot is False
    assert evidence.missing == ["screenshot image"]


def test_ui_evidence_accepts_markdown_report_with_relative_image():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        report = tmp_path / "artifacts" / "T-1" / "report.md"
        image = report.parent / "shot.png"
        report.parent.mkdir(parents=True)
        image.write_bytes(b"fake image")
        report.write_text(
            "# UI 还原交付\n\n"
            "Preview: http://localhost:5173/#/dashboard?visualPreview=1\n"
            "![screenshot](shot.png)\n",
            encoding="utf-8",
        )

        evidence = artifact_gate.ui_evidence(
            "artifacts/T-1/report.md",
            context_text="UI 还原",
            base_dirs=[tmp_path],
        )

    assert evidence.required is True
    assert evidence.passed is True


def test_ui_evidence_accepts_absolute_image_after_chinese_label():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        image = tmp_path / "shot.jpg"
        image.write_bytes(b"fake image")
        report = tmp_path / "report.md"
        report.write_text(
            f"设计截图：`{image}`\n\n"
            "Preview: https://example.com/#/dashboard\n",
            encoding="utf-8",
        )

        evidence = artifact_gate.ui_evidence(
            str(report),
            context_text="页面还原",
            base_dirs=[tmp_path],
        )

    assert evidence.passed is True


def test_non_ui_completion_does_not_require_preview_bundle():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        artifact = tmp_path / "artifacts" / "T-1" / "summary.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("接口字段梳理完成", encoding="utf-8")

        evidence = artifact_gate.ui_evidence(
            str(artifact),
            context_text="接口字段梳理",
            base_dirs=[tmp_path],
        )

    assert evidence.required is False
    assert evidence.passed is True


def test_screenshot_or_preview_word_alone_does_not_trigger_ui_gate():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        artifact = tmp_path / "artifacts" / "T-1" / "shotlist.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("截图清单和预览链接整理完成", encoding="utf-8")

        evidence = artifact_gate.ui_evidence(
            str(artifact),
            context_text="screenshot shotlist",
            base_dirs=[tmp_path],
        )

    assert evidence.required is False
    assert evidence.passed is True


def test_preview_urls_stop_before_chinese_explanatory_text():
    text = "预览：http://127.0.0.1:9225）打开目标页，首屏正文可见"

    assert artifact_gate.preview_urls(text) == ["http://127.0.0.1:9225"]
