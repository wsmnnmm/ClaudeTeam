"""`claudeteam mentor-request` — send an AI mentor evidence pack to TODO002.

This is the team-agnostic front door for group-chat instructions such as
"去问一下刘小排" or "问一下亦仁".  The source manager keeps ownership of
translation/execution; TODO002 owns the DeepSea mentor workstation.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claudeteam.runtime import config, paths
from claudeteam.util import error_exit, usage_error


USAGE = (
    "usage: claudeteam mentor-request [--mentor auto|liu|yiren|both] "
    "--topic <topic> [--kind product|bug|growth|strategy|ops|review] "
    "[--file <evidence.md>] [--image <path> --image-caption <text>] "
    "[--target cloud|local|dry-run] [message|-]"
)

MENTOR_ALIASES = {
    "liu": "liu",
    "liuxiaopai": "liu",
    "xiaopai": "liu",
    "刘小排": "liu",
    "yiren": "yiren",
    "yi": "yiren",
    "亦仁": "yiren",
    "both": "liu,yiren",
    "all": "liu,yiren",
    "两位": "liu,yiren",
    "双导师": "liu,yiren",
}

TARGET_DEFAULTS = {
    "cloud_host": "tencent-claudeteam",
    "cloud_dir": "/srv/ai/projects/todo002-study-coach",
    "cloud_state_dir": "/srv/ai/runtime/todo002-study-coach-cloud/state",
    "cloud_claudeteam_bin": "/srv/ai/ClaudeTeam/.venv/bin/claudeteam",
    "local_dir": "/Users/wsm/Project/todo002-study-coach",
    "local_claudeteam_bin": "/Users/wsm/Project/ClaudeTeam/.venv/bin/claudeteam",
}

SOURCE_TEAM_DIRS = {
    "website-chuhai": "/Users/wsm/Project/website-chuhai-team",
    "websitechuhai": "/Users/wsm/Project/website-chuhai-team",
    "product-lab": "/Users/wsm/Project/product-lab",
    "productlab": "/Users/wsm/Project/product-lab",
    "work-assistant": "/Users/wsm/Project/work-assistant-team",
    "workassistant": "/Users/wsm/Project/work-assistant-team",
    "work-assistant-team": "/Users/wsm/Project/work-assistant-team",
    "traffic-ops": "/Users/wsm/Project/traffic-ops-team",
    "trafficops": "/Users/wsm/Project/traffic-ops-team",
    "traffic-ops-team": "/Users/wsm/Project/traffic-ops-team",
}


@dataclass(frozen=True)
class _Args:
    mentor: str
    topic: str
    kind: str
    owner: str
    message: str
    evidence_file: str
    images: tuple[str, ...]
    image_captions: tuple[str, ...]
    target: str
    out_dir: str
    source_team: str
    source_manager: str


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fa5]+", "-", str(text or "").lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:72] or "mentor-request"


def _stamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def _pop_value(rest: list[str], flag: str) -> str | None:
    if flag not in rest:
        return None
    idx = rest.index(flag)
    if idx == len(rest) - 1:
        raise ValueError(f"{flag} requires a value")
    value = rest[idx + 1]
    del rest[idx:idx + 2]
    return value


def _pop_all(rest: list[str], flag: str) -> list[str]:
    values: list[str] = []
    while flag in rest:
        idx = rest.index(flag)
        if idx == len(rest) - 1:
            raise ValueError(f"{flag} requires a value")
        values.append(rest[idx + 1])
        del rest[idx:idx + 2]
    return values


def _project_root() -> Path:
    cfg = paths.config_file()
    if cfg.exists():
        return cfg.parent
    state = paths.state_dir()
    if state.name == "state":
        return state.parent
    return Path.cwd()


def _default_source_team() -> str:
    session = config.session_name()
    if session and session != "ClaudeTeam":
        return session
    return _project_root().name


def _default_source_dir(source_team: str) -> str:
    key = str(source_team or "").strip().lower()
    if key in SOURCE_TEAM_DIRS:
        return SOURCE_TEAM_DIRS[key]
    return str(_project_root())


def _read_message(raw: str) -> str:
    return sys.stdin.read().strip() if raw == "-" else raw.strip()


def _parse(argv: list[str]) -> _Args | None:
    rest = list(argv)
    try:
        mentor = _pop_value(rest, "--mentor") or "auto"
        topic = _pop_value(rest, "--topic") or ""
        kind = _pop_value(rest, "--kind") or "product"
        owner = _pop_value(rest, "--owner") or ""
        evidence_file = _pop_value(rest, "--file") or ""
        images = tuple(_pop_all(rest, "--image"))
        captions = tuple(_pop_all(rest, "--image-caption") + _pop_all(rest, "--image-note"))
        target = (
            _pop_value(rest, "--target")
            or os.environ.get("CLAUDETEAM_MENTOR_TARGET")
            or "cloud"
        )
        out_dir = _pop_value(rest, "--out-dir") or ""
        source_team = _pop_value(rest, "--source-team") or _default_source_team()
        source_manager = _pop_value(rest, "--source-manager") or "manager"
        dry_run = False
        if "--dry-run" in rest:
            rest.remove("--dry-run")
            dry_run = True
    except ValueError:
        return None
    if dry_run:
        target = "dry-run"
    if any(flag.startswith("--") for flag in rest):
        return None
    message = _read_message(" ".join(rest))
    if not topic:
        topic = _infer_topic(message, evidence_file)
    if not topic:
        return None
    if images and len(captions) < len(images):
        raise ValueError("every --image must have a matching --image-caption")
    if target not in {"cloud", "local", "dry-run"}:
        raise ValueError("--target must be cloud, local, or dry-run")
    mentor_keys = _resolve_mentor(mentor, kind, " ".join([topic, message]))
    return _Args(
        mentor=mentor_keys,
        topic=topic,
        kind=kind,
        owner=owner,
        message=message,
        evidence_file=evidence_file,
        images=images,
        image_captions=captions,
        target=target,
        out_dir=out_dir,
        source_team=source_team,
        source_manager=source_manager,
    )


def _infer_topic(message: str, evidence_file: str) -> str:
    for line in str(message or "").splitlines():
        line = line.strip(" #：:，,。")
        if line:
            return line[:60]
    if evidence_file:
        return Path(evidence_file).stem
    return ""


def _resolve_mentor(raw: str, kind: str, text: str) -> str:
    key = MENTOR_ALIASES.get(str(raw or "").strip().lower()) or MENTOR_ALIASES.get(str(raw or "").strip())
    if key:
        return key
    if str(raw or "").strip().lower() not in {"", "auto"}:
        raise ValueError("--mentor must be auto, liu, yiren, or both")
    compact = re.sub(r"\s+", "", text)
    has_liu = "刘小排" in compact or "liuxiaopai" in compact.lower()
    has_yiren = "亦仁" in compact or "yiren" in compact.lower()
    if has_liu and has_yiren:
        return "liu,yiren"
    if has_yiren:
        return "yiren"
    if has_liu:
        return "liu"
    if kind in {"strategy", "business", "biz"}:
        return "yiren"
    return "liu"


def _read_evidence(file_path: str) -> str:
    if not file_path:
        return ""
    return Path(file_path).expanduser().resolve().read_text(encoding="utf-8").strip()


def _copy_images(args: _Args, package_dir: Path) -> list[dict]:
    copied: list[dict] = []
    image_dir = package_dir / "images"
    if args.images:
        image_dir.mkdir(parents=True, exist_ok=True)
    for idx, image in enumerate(args.images):
        src = Path(image).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"image not found: {src}")
        dst = image_dir / f"{idx + 1:02d}-{src.name}"
        shutil.copy2(src, dst)
        copied.append({
            "source": str(src),
            "path": str(dst),
            "relativePath": f"images/{dst.name}",
            "name": dst.name,
            "caption": args.image_captions[idx],
        })
    return copied


def _mentor_label(mentor_keys: str) -> str:
    labels = []
    for key in mentor_keys.split(","):
        labels.append("AI 亦仁" if key == "yiren" else "AI 刘小排")
    return " / ".join(labels)


def _render_brief(args: _Args, evidence: str, images: list[dict]) -> str:
    owner = args.owner or f"{args.source_team}/{args.source_manager}"
    source_dir = _default_source_dir(args.source_team)
    lines = [
        f"# {args.source_team} -> TODO002 AI导师请求：{args.topic}",
        "",
        f"- Source team: {args.source_team}",
        f"- Source dir: {source_dir}",
        f"- Source manager: {args.source_manager}",
        f"- Target mentors: {_mentor_label(args.mentor)} ({args.mentor})",
        f"- Kind: {args.kind}",
        f"- Owner: {owner}",
        "",
        "## 老板/manager 原始上下文",
        "",
        args.message or "- 未提供额外上下文；请基于主题和证据判断。",
        "",
        "## 证据正文",
        "",
        evidence or "- 暂无额外证据文件。",
        "",
        "## 图片证据",
        "",
    ]
    if images:
        for image in images:
            lines.append(f"- {image['name']}")
            lines.append(f"  - 预期画面/证据含义：{image['caption']}")
            lines.append(f"  - 包内相对路径：{image['relativePath']}")
            lines.append("  - 传给导师时必须作为图片附件上传，不要只把路径写进问题。")
    else:
        lines.append("- 无图片；请只基于内联正文提问。")
    lines.extend([
        "",
        "## TODO002 执行要求",
        "",
        "- TODO002 是导师网关，负责 DeepSea 浏览器、mentor-loop、顾问卡和 loop-state。",
        "- 如果本包选择单导师，只生成该导师的问题文件，不要在导师可见问题里提另一位导师。",
        "- 如果图片说明与图片内容不一致、图片看不清、或疑似旧图，先打 blocker，不要继续上传。",
        "- 本地路径只作内部索引；导师可见内容必须来自内联正文或实际上传图片。",
        "- 完成导师 loop 后，必须运行 `scripts/mentor-loop-return.cjs --run-dir <loop-run-dir> "
        f"--source-team {args.source_team} --source-dir {source_dir}`，直到拿到源团队 inbox local_id、源团队群消息 id 或源 manager 明确确认。",
        "- 导师卡返回后，源团队 manager 负责翻译成任务卡、门禁、SOP 补丁或老板决策卡。",
        "",
    ])
    return "\n".join(lines)


def _image_delivery_lines(meta: dict, package_root: str) -> list[str]:
    images = list(meta.get("images") or [])
    if not images:
        return []
    lines = [
        "",
        "图片附件：",
        "这些图必须用 mentor-loop-run 的 --image 实际上传给导师，不能只在问题里写“截图如下”。",
    ]
    for image in images:
        image_path = f"{package_root}/images/{image['name']}"
        lines.append(f"- {image_path}")
        lines.append(f"  图意：{image['caption']}")
    return lines


def _write_package(args: _Args) -> tuple[Path, dict]:
    root = Path(args.out_dir).expanduser().resolve() if args.out_dir else (
        _project_root() / "artifacts" / "cross-team" / "mentor-requests")
    package_dir = root / f"{_stamp()}-{_safe_slug(args.topic)}"
    package_dir.mkdir(parents=True, exist_ok=True)
    images = _copy_images(args, package_dir)
    evidence = _read_evidence(args.evidence_file)
    brief_path = package_dir / "brief.md"
    meta_path = package_dir / "request.json"
    brief_path.write_text(_render_brief(args, evidence, images), encoding="utf-8")
    meta = {
        "sourceTeam": args.source_team,
        "sourceDir": _default_source_dir(args.source_team),
        "sourceManager": args.source_manager,
        "topic": args.topic,
        "kind": args.kind,
        "owner": args.owner or f"{args.source_team}/{args.source_manager}",
        "mentors": args.mentor,
        "target": args.target,
        "brief": str(brief_path),
        "images": images,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return package_dir, meta


def _todo_env(target: str) -> dict[str, str]:
    if target == "cloud":
        return {
            "CLAUDETEAM_CONFIG_FILE": f"{TARGET_DEFAULTS['cloud_dir']}/claudeteam.cloud.toml",
            "CLAUDETEAM_STATE_DIR": TARGET_DEFAULTS["cloud_state_dir"],
            "CLAUDETEAM_TEAM_FILE": f"{TARGET_DEFAULTS['cloud_dir']}/team.json",
            "CLAUDETEAM_RUNTIME_CONFIG": f"{TARGET_DEFAULTS['cloud_dir']}/runtime_config.cloud.json",
            "LARK_CLI_PROFILE": "todo002-study-coach-cloud",
        }
    return {
        "CLAUDETEAM_CONFIG_FILE": f"{TARGET_DEFAULTS['local_dir']}/claudeteam.toml",
        "CLAUDETEAM_STATE_DIR": f"{TARGET_DEFAULTS['local_dir']}/state",
        "CLAUDETEAM_TEAM_FILE": f"{TARGET_DEFAULTS['local_dir']}/team.json",
        "CLAUDETEAM_RUNTIME_CONFIG": f"{TARGET_DEFAULTS['local_dir']}/runtime_config.json",
        "LARK_CLI_PROFILE": "todo002-study-coach",
    }


def _send_message(args: _Args, meta: dict, package_dir: Path, *,
                  run: Callable = subprocess.run) -> dict:
    source_dir = str(meta.get("sourceDir") or _default_source_dir(args.source_team))
    image_lines = _image_delivery_lines(meta, str(package_dir))
    message = "\n".join([
        f"[{args.source_team}→TODO002][AI导师请求] {args.topic}",
        "",
        f"导师：{_mentor_label(args.mentor)}",
        f"问题类型：{args.kind}",
        f"请求包：{meta['brief']}",
        f"源队目录：{source_dir}",
        *image_lines,
        "",
        "请 TODO002 manager：",
        "1. 检查 evidence pack 和图片说明是否匹配。",
        "2. 用 mentor-loop-run 走独立导师入口；有图片时必须带 --image 和 --image-caption，保存顾问卡和 loop-state。",
        "3. 完成后必须运行："
        f" scripts/mentor-loop-return.cjs --run-dir <loop-run-dir> --source-team {args.source_team}"
        f" --source-dir {source_dir}",
        "4. 拿到源团队 inbox local_id、源团队群消息 id 或源 manager 明确确认后，才算回传完成。",
    ])
    if args.target == "dry-run":
        return {"attempted": False, "target": "dry-run", "message": message}

    if args.target == "cloud":
        host = os.environ.get("TODO002_CLOUD_HOST", TARGET_DEFAULTS["cloud_host"])
        remote_root = (
            f"{TARGET_DEFAULTS['cloud_dir']}/knowledge-base/cross-team/"
            f"{_safe_slug(args.source_team)}-inbox"
        )
        remote_dir = f"{remote_root}/{package_dir.name}"
        run(["ssh", host, f"mkdir -p {shlex.quote(remote_root)}"],
            capture_output=True, text=True, timeout=60, check=False)
        scp = run(["scp", "-q", "-r", str(package_dir), f"{host}:{remote_dir}"],
                  capture_output=True, text=True, timeout=120, check=False)
        if scp.returncode != 0:
            return {
                "attempted": True,
                "target": "cloud",
                "delivered": False,
                "error": scp.stderr or scp.stdout or f"scp exit {scp.returncode}",
            }
        remote_brief = f"{remote_dir}/brief.md"
        message = message.replace(str(meta["brief"]), remote_brief)
        message = message.replace(str(package_dir), remote_dir)
        env_prefix = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in _todo_env("cloud").items()
        )
        remote_cmd = (
            f"{env_prefix} {shlex.quote(TARGET_DEFAULTS['cloud_claudeteam_bin'])} "
            "send manager "
            f"{shlex.quote(_safe_slug(args.source_team) + '_manager')} "
            f"{shlex.quote(message)} 高 --no-task"
        )
        proc = run(["ssh", host, remote_cmd], capture_output=True, text=True,
                   timeout=120, check=False)
        return {
            "attempted": True,
            "target": "cloud",
            "remoteDir": remote_dir,
            "remoteBrief": remote_brief,
            "delivered": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    env = {**os.environ, **_todo_env("local")}
    proc = run(
        [TARGET_DEFAULTS["local_claudeteam_bin"], "send", "manager",
         f"{_safe_slug(args.source_team)}_manager", message, "高", "--no-task"],
        cwd=TARGET_DEFAULTS["local_dir"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return {
        "attempted": True,
        "target": "local",
        "delivered": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def main(argv: list[str]) -> int:
    if not argv or "--help" in argv or "-h" in argv:
        return usage_error(USAGE)
    try:
        args = _parse(argv)
    except (OSError, ValueError) as e:
        return error_exit(f"❌ {e}\n\n{USAGE}")
    if args is None:
        return usage_error(USAGE)
    package_dir, meta = _write_package(args)
    delivery = _send_message(args, meta, package_dir)
    ok = args.target == "dry-run" or bool(delivery.get("delivered"))
    print(json.dumps({
        "ok": ok,
        "packageDir": str(package_dir),
        "brief": meta["brief"],
        "mentors": args.mentor,
        "target": args.target,
        "delivery": delivery,
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1
