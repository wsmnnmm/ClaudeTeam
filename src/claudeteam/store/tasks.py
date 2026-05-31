"""Local task store — coordination cards across agents.

One JSON file (`$CLAUDETEAM_STATE_DIR/tasks.json`) with shape:
    {"tasks": [...], "_meta": {"last_id": N}}

Each task:
    {id, title, description, assignee, creator,
     topic,
     status, artifact_path, reviewed_by, reviewed_at,
     founder_stage, stage_exit_evidence, evidence_action, non_goal,
     created_at, updated_at, completed_at}

Pure file-locked CRUD; lifecycle (assignment, completion, etc.) is whatever
the agents agree on — the store is opinion-free.

Status vocabulary: 待处理 / 进行中 / 待验收 / 已完成 / 已取消
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.util import flock, now_ms, read_json, write_json


VALID_STATUSES = {"待处理", "进行中", "待验收", "已完成", "已取消"}
DEFAULT_STATUS = "待处理"
TERMINAL_STATUSES = {"已完成", "已取消"}

# Common invalid status values seen in the wild and their safe mappings.
# "历史候选" was found on 28 tasks in work-assistant — it means
# "candidate that was never picked up", closest to 已取消.
_STATUS_REPAIR_MAP: dict[str, str] = {
    "历史候选": "已取消",
    "历史": "已取消",
    "候选": "待处理",
    "待确认": "待处理",
    "暂停": "已取消",
    "搁置": "已取消",
    "已关闭": "已取消",
    "close": "已取消",
    "closed": "已取消",
    "done": "已完成",
    "complete": "已完成",
    "in_progress": "进行中",
    "pending": "待处理",
    "blocked": "进行中",
}


def _repair_status(status: str, task_id: str = "") -> str:
    """Map an invalid status to the nearest valid one, logging a warning."""
    if status in VALID_STATUSES:
        return status
    repaired = _STATUS_REPAIR_MAP.get(status, DEFAULT_STATUS)
    import sys
    print(
        f"  ⚠️ tasks: {task_id or '?'} status {status!r} → {repaired!r} "
        f"(not in {sorted(VALID_STATUSES)}); run `claudeteam task update "
        f"{task_id} --status {repaired}` to silence this",
        file=sys.stderr,
    )
    return repaired


def repair_invalid_statuses(*, dry_run: bool = False) -> list[dict]:
    """Scan all tasks and repair invalid statuses. Returns list of changed tasks."""
    changed: list[dict] = []
    with _locked():
        data = _load()
        for task in data.get("tasks", []):
            status = str(task.get("status") or "")
            if status in VALID_STATUSES:
                continue
            task["status"] = _repair_status(status, task.get("id", "?"))
            task["updated_at"] = now_ms()
            changed.append(dict(task))
        if changed and not dry_run:
            _save(data)
    return changed


def _file() -> Path:
    return paths.state_dir() / "tasks.json"


def _locked():
    return flock(_file().with_suffix(".lock"))


def _load() -> dict:
    return read_json(_file(), {"tasks": [], "_meta": {"last_id": 0}})


def _save(data: dict) -> None:
    write_json(_file(), data)


def _clean_topic(topic: str) -> str:
    cleaned = str(topic or "").strip()
    while cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()
    return cleaned.strip(" \t\r\n:：")


def _resolve_artifact_path(artifact: str) -> Path:
    """Resolve a relative artifact path against the team directory."""
    p = Path(artifact)
    if p.is_absolute():
        return p
    return (paths.config_file().parent / p).resolve()


def _artifact_file_exists(artifact: str) -> bool:
    """True when artifact is a URL or points to an existing local file."""
    if not artifact.strip():
        return False
    from urllib.parse import urlparse
    parsed = urlparse(artifact)
    if parsed.scheme and parsed.scheme != "file":
        return True
    path = _resolve_artifact_path(artifact)
    return path.exists()


# ── public API ────────────────────────────────────────────────────


def create(assignee: str, title: str, *,
           description: str = "", creator: str = "",
           topic: str = "",
           artifact_path: str = "",
           founder_stage: str = "",
           stage_exit_evidence: str = "",
           evidence_action: str = "",
           non_goal: str = "") -> str:
    """Create a new task; return its task_id (T-<n>)."""
    if not title.strip():
        raise ValueError("title cannot be empty")
    with _locked():
        data = _load()
        data["_meta"]["last_id"] = data["_meta"].get("last_id", 0) + 1
        tid = f"T-{data['_meta']['last_id']}"
        now = now_ms()
        data.setdefault("tasks", []).append({
            "id": tid,
            "title": title.strip(),
            "description": description,
            "assignee": assignee,
            "creator": creator,
            "topic": _clean_topic(topic),
            "status": DEFAULT_STATUS,
            "artifact_path": artifact_path,
            "reviewed_by": "",
            "reviewed_at": None,
            "founder_stage": founder_stage,
            "stage_exit_evidence": stage_exit_evidence,
            "evidence_action": evidence_action,
            "non_goal": non_goal,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        })
        _save(data)
        return tid


def update(task_id: str, *, status: str | None = None,
           assignee: str | None = None, title: str | None = None,
           description: str | None = None,
           topic: str | None = None,
           artifact_path: str | None = None,
           reviewed_by: str | None = None,
           founder_stage: str | None = None,
           stage_exit_evidence: str | None = None,
           evidence_action: str | None = None,
           non_goal: str | None = None,
           _force: bool = False) -> bool:
    """Apply non-None fields. Returns False if task_id not found.

    Evidence gate: transitioning to '已完成' without an artifact_path (either
    already on the task or provided in this update) raises ValueError unless
    `_force=True`. This closes the 71%-cancellation-without-evidence gap
    found in the 2026-05-31 audit.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status} (valid: {sorted(VALID_STATUSES)})")
    with _locked():
        data = _load()
        for task in data.get("tasks", []):
            if task["id"] != task_id:
                continue
            if status is not None:
                if status == "已完成" and not _force:
                    existing = str(task.get("artifact_path") or "").strip()
                    incoming = str(artifact_path or "").strip()
                    resolved = incoming or existing
                    if not resolved:
                        raise ValueError(
                            f"task {task_id}: '已完成' requires artifact_path "
                            f"(evidence of completion). Set artifact_path or use "
                            f"_force=True to bypass."
                        )
                    if not _artifact_file_exists(resolved):
                        raise ValueError(
                            f"task {task_id}: artifact does not exist at "
                            f"{resolved}; provide a valid path or use "
                            f"_force=True to bypass."
                        )
                task["status"] = status
                if status in TERMINAL_STATUSES:
                    task["completed_at"] = now_ms()
                else:
                    task["completed_at"] = None
            if assignee is not None:
                task["assignee"] = assignee
            if title is not None:
                task["title"] = title.strip()
            if description is not None:
                task["description"] = description
            if topic is not None:
                task["topic"] = _clean_topic(topic)
            if artifact_path is not None:
                task["artifact_path"] = artifact_path
            if reviewed_by is not None:
                task["reviewed_by"] = reviewed_by
                task["reviewed_at"] = now_ms() if reviewed_by else None
            if founder_stage is not None:
                task["founder_stage"] = founder_stage
            if stage_exit_evidence is not None:
                task["stage_exit_evidence"] = stage_exit_evidence
            if evidence_action is not None:
                task["evidence_action"] = evidence_action
            if non_goal is not None:
                task["non_goal"] = non_goal
            task["updated_at"] = now_ms()
            _save(data)
            return True
    return False


def get(task_id: str) -> dict | None:
    for task in _load().get("tasks", []):
        if task["id"] == task_id:
            status = str(task.get("status") or "")
            if status not in VALID_STATUSES:
                task["status"] = _repair_status(status, task_id)
                with _locked():
                    _save(_load())  # persist the repair
            return task
    return None


def list_tasks(*, status: str | None = None,
               assignee: str | None = None,
               topic: str | None = None) -> list[dict]:
    """Return tasks filtered by status / assignee / topic, sorted by id."""
    rows = list(_load().get("tasks", []))
    repaired = False
    for task in rows:
        current = str(task.get("status") or "")
        if current not in VALID_STATUSES:
            task["status"] = _repair_status(current, task.get("id", "?"))
            repaired = True
    if repaired:
        with _locked():
            _save(_load())  # persist repairs
    if status is not None:
        rows = [t for t in rows if t.get("status") == status]
    if assignee is not None:
        rows = [t for t in rows if t.get("assignee") == assignee]
    if topic is not None:
        wanted = _clean_topic(topic).casefold()
        rows = [t for t in rows
                if str(t.get("topic") or "").casefold() == wanted]
    rows.sort(key=lambda t: int(t["id"].split("-")[1]) if "-" in t["id"] else 0)
    return rows
