"""Download Feishu message media into project-local artifacts."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from claudeteam.feishu import lark
from claudeteam.runtime import config, paths


_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_part(value: str, *, fallback: str) -> str:
    cleaned = _SAFE_PART_RE.sub("_", str(value or "")).strip("._")
    return cleaned or fallback


def _artifact_dir(message_id: str) -> Path:
    return paths.state_dir() / "artifacts" / "feishu" / _safe_part(
        message_id, fallback="message")


def _latest_matching(path: Path, stem: str) -> Path | None:
    candidates = [
        p for p in path.glob(f"{stem}*")
        if p.is_file() and p.name == _safe_part(p.name, fallback=p.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime_ns)


def download_message_resource(
    message_id: str,
    resource_key: str,
    resource_type: str,
    *,
    profile: str | None = None,
    as_user: bool = False,
    run: Callable = subprocess.run,
) -> Path | None:
    """Download one message image/file resource and return the local path.

    lark-cli's resource command only accepts a safe relative output name,
    so we run it with cwd set to the artifact directory and pass a filename
    stem. The CLI infers a file extension from Feishu's response when it can.
    """
    message_id = str(message_id or "").strip()
    resource_key = str(resource_key or "").strip()
    resource_type = str(resource_type or "").strip().lower()
    if not message_id or not resource_key or resource_type not in {"image", "file"}:
        return None

    out_dir = _artifact_dir(message_id)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"  ⚠️ Feishu media artifact dir failed: {exc}")
        return None

    stem = f"{resource_type}-{_safe_part(resource_key, fallback='resource')}"
    argv = [
        *lark.resolve_cli_prefix(),
        *(["--profile", profile] if profile else (
            ["--profile", config.lark_profile()] if config.lark_profile() else [])),
        "im", "+messages-resources-download",
        "--message-id", message_id,
        "--file-key", resource_key,
        "--type", resource_type,
        "--output", stem,
        "--as", "user" if as_user else "bot",
    ]
    try:
        result = run(
            argv,
            capture_output=True,
            text=True,
            timeout=lark._resolve_timeout(None),
            env=lark.subprocess_env(),
            cwd=str(out_dir),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  ⚠️ Feishu media download failed: {exc}")
        return None
    if result.returncode != 0:
        detail = next(
            (line for line in ((result.stderr or "") + "\n" + (result.stdout or "")).splitlines() if line.strip()),
            f"rc={result.returncode}",
        )
        print(f"  ⚠️ Feishu media download failed: {detail}"[:200])
        return None

    downloaded = _latest_matching(out_dir, stem)
    if downloaded is None:
        # Some lark-cli versions write exactly the output stem with no extension.
        exact = out_dir / stem
        downloaded = exact if exact.is_file() else None
    if downloaded is None:
        return None
    try:
        os.chmod(downloaded, 0o600)
    except OSError:
        pass
    return downloaded.resolve()
