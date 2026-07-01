"""Local task store — coordination cards across agents.

One JSON file (`$CLAUDETEAM_STATE_DIR/tasks.json`) with shape:
    {"tasks": [...], "intents": [...], "_meta": {"last_id": N, "last_intent_id": M}}

Each task:
    {id, title, description, assignee, creator, intent_id,
     topic,
     parent_task_id,
     status, awaiting, approval_note, paused_by, paused_at,
     artifact_path, reviewed_by, reviewed_at,
     founder_stage, stage_exit_evidence, evidence_action, non_goal,
     issue_class, current_segment, next_natural_window, base_absorb_needed,
     created_at, updated_at, completed_at}

Each intent (immutable verbatim record of the boss's original words):
    {id, raw_text, source_msg, key_points, creator, created_at}

Status vocabulary: 待处理 / 进行中 / 需审批 / 待验收 / 已完成 / 已取消
The `需审批` state is a hard suspend: it can only be entered via pause()
and left via approve()/reject(), never via generic update().
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.util import flock, now_ms, read_json, write_json


VALID_STATUSES = {"待处理", "进行中", "需审批", "待验收", "已完成", "已取消"}
DEFAULT_STATUS = "待处理"
SUSPEND_STATUS = "需审批"
TERMINAL_STATUSES = {"已完成", "已取消"}
VALID_ISSUE_CLASSES = {"local-business", "cross-team", "base-common"}
VALID_CURRENT_SEGMENTS = {
    "trigger", "worker", "artifact", "receipt",
    "sync", "local_snapshot", "boss_view",
}
VALID_BASE_ABSORB_NEEDED = {"yes", "no"}
TRUTH_SURFACE_FIELDS = (
    "issue_class", "current_segment", "next_natural_window",
    "base_absorb_needed",
)

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

# Legal moves for the generic update() path. Terminals are frozen: a
# 已完成/已取消 task can never be resurrected by a stray `task update`
# — reopening is an explicit new task, not a silent status flip (a
# revived task would also re-anchor a stale intent into the assignee's
# CLAUDE.md). 待处理 → 已完成 stays legal so the everyday
# `task done <T-n>` shortcut works without a ceremonial 进行中 hop.
# 需审批 is absent on purpose: both directions are gated by
# pause()/approve()/reject() before this map is consulted.
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "待处理": {"进行中", "待验收", "已完成", "已取消"},
    "进行中": {"待处理", "待验收", "已完成", "已取消"},
    "待验收": {"进行中", "已完成", "已取消"},
    "已完成": set(),
    "已取消": set(),
}


def _file() -> Path:
    return paths.state_dir() / "tasks.json"


def _locked():
    return flock(_file().with_suffix(".lock"))


def _load() -> dict:
    return read_json(_file(), {"tasks": [], "intents": [],
                               "_meta": {"last_id": 0, "last_intent_id": 0}})


def _save(data: dict) -> None:
    write_json(_file(), data)


def _find(data: dict, task_id: str) -> dict | None:
    return _find_task(data, task_id)


def _capture_artifact_incident(task: dict, missing: list[str]) -> None:
    """L5 self-evolution: capture artifact-gate failure as an incident."""
    try:
        from claudeteam.runtime import incident_learning
        incident = incident_learning.from_artifact_gate(
            str(task.get("assignee") or ""),
            str(task.get("id") or ""),
            missing,
        )
        incident_learning.capture(incident)
    except Exception:
        pass


def _register_task_learning_context(task_id: str, *, assignee: str,
                                    title: str, description: str) -> None:
    """Best-effort hook: link relevant incident learnings to a new task."""
    try:
        from claudeteam.runtime import incident_learning
        incident_learning.register_task_context(
            task_id,
            task_title=title,
            task_description=description,
            assignee=assignee,
        )
    except Exception:
        pass


def _mark_task_learning_applied(task_id: str) -> None:
    """Best-effort hook: successful task completion counts as applied learning."""
    try:
        from claudeteam.runtime import incident_learning
        incident_learning.mark_task_applied(task_id)
    except Exception:
        pass


def _clean_topic(topic: str) -> str:
    cleaned = str(topic or "").strip()
    while cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()
    return cleaned.strip(" \t\r\n:：")


def _clean_issue_class(issue_class: str) -> str:
    raw = str(issue_class or "").strip()
    if not raw:
        return ""
    normalized = raw.casefold().replace("_", "-").replace(" ", "-")
    mapping = {
        "业务局部": "local-business",
        "local": "local-business",
        "local-business": "local-business",
        "跨队协作": "cross-team",
        "cross": "cross-team",
        "cross-team": "cross-team",
        "基座共性": "base-common",
        "base": "base-common",
        "base-common": "base-common",
    }
    cleaned = mapping.get(raw, mapping.get(normalized, normalized))
    if cleaned not in VALID_ISSUE_CLASSES:
        raise ValueError(
            f"invalid issue class: {issue_class} "
            f"(valid: {sorted(VALID_ISSUE_CLASSES)})"
        )
    return cleaned


def _clean_current_segment(segment: str) -> str:
    raw = str(segment or "").strip()
    if not raw:
        return ""
    normalized = raw.casefold().replace("-", "_").replace(" ", "_")
    mapping = {
        "触发": "trigger",
        "执行": "worker",
        "worker": "worker",
        "产物": "artifact",
        "artifact": "artifact",
        "回执": "receipt",
        "receipt": "receipt",
        "同步": "sync",
        "sync": "sync",
        "本地快照": "local_snapshot",
        "local_snapshot": "local_snapshot",
        "boss_view": "boss_view",
        "boss-view": "boss_view",
        "老板视图": "boss_view",
    }
    cleaned = mapping.get(raw, mapping.get(normalized, normalized))
    if cleaned not in VALID_CURRENT_SEGMENTS:
        raise ValueError(
            f"invalid current segment: {segment} "
            f"(valid: {sorted(VALID_CURRENT_SEGMENTS)})"
        )
    return cleaned


def _clean_yes_no(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.casefold()
    mapping = {
        "yes": "yes",
        "y": "yes",
        "true": "yes",
        "1": "yes",
        "是": "yes",
        "需要": "yes",
        "no": "no",
        "n": "no",
        "false": "no",
        "0": "no",
        "否": "no",
        "不需要": "no",
    }
    cleaned = mapping.get(raw, mapping.get(normalized, normalized))
    if cleaned not in VALID_BASE_ABSORB_NEEDED:
        raise ValueError(
            f"invalid base absorb flag: {value} "
            f"(valid: {sorted(VALID_BASE_ABSORB_NEEDED)})"
        )
    return cleaned


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


def _find_task(data: dict, task_id: str) -> dict | None:
    for task in data.get("tasks", []):
        if str(task.get("id") or "") == task_id:
            return task
    return None


def _normalize_parent_task_id(parent_task_id: str | None, *, data: dict,
                              task_id: str = "") -> str:
    parent = str(parent_task_id or "").strip()
    if not parent:
        return ""
    if task_id and parent == task_id:
        raise ValueError("parent task cannot be self")
    if _find_task(data, parent) is None:
        raise ValueError(f"parent task not found: {parent}")
    if not task_id:
        return parent
    seen = {task_id}
    current = parent
    while current:
        if current in seen:
            raise ValueError(f"parent task would create a cycle: {parent}")
        seen.add(current)
        parent_task = _find_task(data, current)
        if parent_task is None:
            break
        current = str(parent_task.get("parent_task_id") or "").strip()
    return parent


def _child_tasks(data: dict, parent_task_id: str) -> list[dict]:
    parent = str(parent_task_id or "").strip()
    if not parent:
        return []
    return [
        task for task in data.get("tasks", [])
        if str(task.get("parent_task_id") or "").strip() == parent
    ]


def _open_child_tasks(data: dict, parent_task_id: str) -> list[dict]:
    return [
        task for task in _child_tasks(data, parent_task_id)
        if str(task.get("status") or "") not in TERMINAL_STATUSES
    ]


def _set_status(task: dict, status: str) -> None:
    task["status"] = status
    task["completed_at"] = now_ms() if status in TERMINAL_STATUSES else None
    task["updated_at"] = now_ms()


def _inherit_parent_truth_surface(data: dict, parent_task_id: str, *,
                                  issue_class: str,
                                  base_absorb_needed: str) -> tuple[str, str]:
    parent = _find_task(data, parent_task_id)
    if parent is None:
        return issue_class, base_absorb_needed
    inherited_issue = str(issue_class or "").strip()
    inherited_absorb = str(base_absorb_needed or "").strip()
    if not inherited_issue:
        inherited_issue = str(parent.get("issue_class") or "").strip()
    if not inherited_absorb:
        inherited_absorb = str(parent.get("base_absorb_needed") or "").strip()
    return inherited_issue, inherited_absorb


def _task_repo_root() -> Path:
    config = paths.config_file()
    try:
        return config.resolve().parent
    except OSError:
        return config.parent


# ── public API ────────────────────────────────────────────────────


def create(assignee: str, title: str, *,
           description: str = "", creator: str = "",
           intent_id: str = "",
           topic: str = "",
           parent_task_id: str = "",
           artifact_path: str = "",
           founder_stage: str = "",
           stage_exit_evidence: str = "",
           evidence_action: str = "",
           non_goal: str = "",
           issue_class: str = "",
           current_segment: str = "",
           next_natural_window: str = "",
           base_absorb_needed: str = "") -> str:
    """Create a new task; return its task_id (T-<n>)."""
    if not title.strip():
        raise ValueError("title cannot be empty")
    with _locked():
        data = _load()
        meta = data.setdefault("_meta", {})
        meta["last_id"] = meta.get("last_id", 0) + 1
        tid = f"T-{meta['last_id']}"
        now = now_ms()
        parent = _normalize_parent_task_id(parent_task_id, data=data, task_id=tid)
        inherited_issue_class, inherited_base_absorb = _inherit_parent_truth_surface(
            data,
            parent,
            issue_class=issue_class,
            base_absorb_needed=base_absorb_needed,
        )
        data.setdefault("tasks", []).append({
            "id": tid,
            "title": title.strip(),
            "description": description,
            "assignee": assignee,
            "creator": creator,
            "intent_id": intent_id,
            "topic": _clean_topic(topic),
            "parent_task_id": parent,
            "status": DEFAULT_STATUS,
            "awaiting": "",
            "approval_note": "",
            "paused_by": "",
            "paused_at": None,
            "artifact_path": artifact_path,
            "reviewed_by": "",
            "reviewed_at": None,
            "founder_stage": founder_stage,
            "stage_exit_evidence": stage_exit_evidence,
            "evidence_action": evidence_action,
            "non_goal": non_goal,
            "issue_class": _clean_issue_class(inherited_issue_class),
            "current_segment": _clean_current_segment(current_segment),
            "next_natural_window": str(next_natural_window or "").strip(),
            "base_absorb_needed": _clean_yes_no(inherited_base_absorb),
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        })
        _save(data)
        _register_task_learning_context(
            tid,
            assignee=assignee,
            title=title.strip(),
            description=description,
        )
        return tid


def update(task_id: str, *, status: str | None = None,
           assignee: str | None = None, title: str | None = None,
           description: str | None = None,
           topic: str | None = None,
           parent_task_id: str | None = None,
           artifact_path: str | None = None,
           reviewed_by: str | None = None,
           founder_stage: str | None = None,
           stage_exit_evidence: str | None = None,
           evidence_action: str | None = None,
           non_goal: str | None = None,
           issue_class: str | None = None,
           current_segment: str | None = None,
           next_natural_window: str | None = None,
           base_absorb_needed: str | None = None,
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
            prior_status = str(task.get("status") or "")
            if status is not None:
                if SUSPEND_STATUS in (status, prior_status):
                    raise ValueError(
                        "需审批 transitions must use task pause/approve/reject, not update")
                status_changes = status != prior_status
                if status_changes:
                    allowed = LEGAL_TRANSITIONS.get(prior_status, set())
                    if status not in allowed:
                        raise ValueError(
                            f"illegal transition: {prior_status} → {status}"
                            + (f" (from {prior_status} only: "
                               f"{' / '.join(sorted(allowed))})"
                               if allowed else f" ({prior_status} is terminal — "
                                               f"reopen by creating a new task)"))
                if status_changes and status in {"待验收", "已完成"} and not _force:
                    open_children = _open_child_tasks(data, task_id)
                    if open_children:
                        child_ids = ", ".join(
                            str(child.get("id") or "?") for child in open_children[:5]
                        )
                        raise ValueError(
                            f"task {task_id}: cannot mark {status} with open child tasks: "
                            f"{child_ids}"
                        )
                if status_changes and status == "已完成" and not _force:
                    existing = str(task.get("artifact_path") or "").strip()
                    incoming = str(artifact_path or "").strip()
                    resolved = incoming or existing
                    existing_reviewer = str(task.get("reviewed_by") or "").strip()
                    incoming_reviewer = str(reviewed_by or "").strip()
                    resolved_reviewer = incoming_reviewer or existing_reviewer
                    if not resolved:
                        _capture_artifact_incident(task, ["artifact_path missing"])
                        raise ValueError(
                            f"task {task_id}: '已完成' requires artifact_path "
                            f"(evidence of completion). Set artifact_path or use "
                            f"_force=True to bypass."
                        )
                    if not _artifact_file_exists(resolved):
                        _capture_artifact_incident(task, [resolved])
                        raise ValueError(
                            f"task {task_id}: artifact does not exist at "
                            f"{resolved}; provide a valid path or use "
                            f"_force=True to bypass."
                        )
                    if not resolved_reviewer:
                        raise ValueError(
                            f"task {task_id}: '已完成' requires reviewed_by "
                            f"(who accepted the artifact). Set reviewed_by or "
                            f"use _force=True to bypass."
                        )
                if status_changes:
                    _set_status(task, status)
            if assignee is not None:
                task["assignee"] = assignee
            if title is not None:
                task["title"] = title.strip()
            if description is not None:
                task["description"] = description
            if topic is not None:
                task["topic"] = _clean_topic(topic)
            if parent_task_id is not None:
                task["parent_task_id"] = _normalize_parent_task_id(
                    parent_task_id, data=data, task_id=task_id)
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
            if issue_class is not None:
                task["issue_class"] = _clean_issue_class(issue_class)
            if current_segment is not None:
                task["current_segment"] = _clean_current_segment(current_segment)
            if next_natural_window is not None:
                task["next_natural_window"] = str(next_natural_window).strip()
            if base_absorb_needed is not None:
                task["base_absorb_needed"] = _clean_yes_no(base_absorb_needed)
            parent = _find_task(
                data, str(task.get("parent_task_id") or "").strip()
            )
            if parent is not None:
                if not str(task.get("issue_class") or "").strip():
                    task["issue_class"] = str(parent.get("issue_class") or "").strip()
                if not str(task.get("base_absorb_needed") or "").strip():
                    task["base_absorb_needed"] = str(
                        parent.get("base_absorb_needed") or ""
                    ).strip()
            task["updated_at"] = now_ms()
            _save(data)
            if status == "已完成" and prior_status != "已完成":
                _mark_task_learning_applied(task_id)
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
               topic: str | None = None,
               parent_task_id: str | None = None) -> list[dict]:
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
    if parent_task_id is not None:
        wanted_parent = str(parent_task_id).strip()
        rows = [
            t for t in rows
            if str(t.get("parent_task_id") or "").strip() == wanted_parent
        ]
    rows.sort(key=lambda t: int(t["id"].split("-")[1]) if "-" in t["id"] else 0)
    return rows


def create_intent(raw_text: str, *, source_msg: str = "",
                  key_points: str = "", creator: str = "user") -> str:
    """Persist the boss's verbatim words as an immutable intent; return I-<n>."""
    if not raw_text.strip():
        raise ValueError("intent raw_text cannot be empty")
    with _locked():
        data = _load()
        meta = data.setdefault("_meta", {})
        meta["last_intent_id"] = meta.get("last_intent_id", 0) + 1
        iid = f"I-{meta['last_intent_id']}"
        data.setdefault("intents", []).append({
            "id": iid,
            "raw_text": raw_text,
            "source_msg": source_msg,
            "key_points": key_points,
            "creator": creator,
            "created_at": now_ms(),
        })
        _save(data)
        return iid


def get_intent(intent_id: str) -> dict | None:
    for intent in _load().get("intents", []):
        if intent.get("id") == intent_id:
            return intent
    return None


def pause(task_id: str, *, awaiting: str = "user",
          approval_note: str = "", paused_by: str = "") -> bool:
    """进行中 → 需审批. Returns False unless the task is currently 进行中."""
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None or task.get("status") != "进行中":
            return False
        _set_status(task, SUSPEND_STATUS)
        task["awaiting"] = awaiting
        task["approval_note"] = approval_note
        task["paused_by"] = paused_by
        task["paused_at"] = now_ms()
        _save(data)
        return True


def approve(task_id: str, *, done: bool = False, note: str = "") -> bool:
    """需审批 → 进行中 or 已完成."""
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None or task.get("status") != SUSPEND_STATUS:
            return False
        _set_status(task, "已完成" if done else "进行中")
        task["awaiting"] = ""
        task["approval_note"] = note
        _save(data)
        return True


def reject(task_id: str, *, feedback: str = "", cancel: bool = False) -> bool:
    """需审批 → 进行中 or 已取消."""
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None or task.get("status") != SUSPEND_STATUS:
            return False
        _set_status(task, "已取消" if cancel else "进行中")
        task["awaiting"] = ""
        task["approval_note"] = feedback
        _save(data)
        return True


def void(task_id: str, *, reason: str = "", voided_by: str = "") -> bool:
    """Explicitly tombstone a task to 已取消, including a completed task."""
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None or task.get("status") == "已取消":
            return False
        _set_status(task, "已取消")
        task["awaiting"] = ""
        task["approval_note"] = reason
        task["paused_by"] = voided_by
        task["paused_at"] = now_ms()
        _save(data)
        return True


def audit_tasks(*, assignee: str | None = None,
                topic: str | None = None,
                parent_task_id: str | None = None,
                active_only: bool = True) -> dict:
    """Audit active task truth-surface quality for manager acceptance."""
    rows = list_tasks(
        assignee=assignee,
        topic=topic,
        parent_task_id=parent_task_id,
    )
    if active_only:
        rows = [
            task for task in rows
            if str(task.get("status") or "") not in TERMINAL_STATUSES
        ]
    data = _load()
    findings: list[dict] = []
    for task in rows:
        task_id = str(task.get("id") or "")
        task_title = str(task.get("title") or "").strip()
        missing = [
            field for field in TRUTH_SURFACE_FIELDS
            if not str(task.get(field) or "").strip()
        ]
        if missing:
            findings.append({
                "task_id": task_id,
                "title": task_title,
                "assignee": str(task.get("assignee") or ""),
                "finding_code": "missing_truth_surface",
                "missing_fields": missing,
                "message": (
                    "missing truth-surface fields: "
                    + ", ".join(missing)
                ),
            })
        parent = _find_task(data, str(task.get("parent_task_id") or "").strip())
        if parent is None:
            continue
        parent_id = str(parent.get("id") or "").strip()
        parent_issue = str(parent.get("issue_class") or "").strip()
        child_issue = str(task.get("issue_class") or "").strip()
        if parent_issue and child_issue and parent_issue != child_issue:
            findings.append({
                "task_id": task_id,
                "title": task_title,
                "assignee": str(task.get("assignee") or ""),
                "parent_task_id": parent_id,
                "finding_code": "parent_issue_class_mismatch",
                "field": "issue_class",
                "expected": parent_issue,
                "actual": child_issue,
                "message": (
                    f"child issue_class {child_issue} mismatches "
                    f"parent {parent_id} {parent_issue}"
                ),
            })
        parent_absorb = str(parent.get("base_absorb_needed") or "").strip()
        child_absorb = str(task.get("base_absorb_needed") or "").strip()
        if parent_absorb and child_absorb and parent_absorb != child_absorb:
            findings.append({
                "task_id": task_id,
                "title": task_title,
                "assignee": str(task.get("assignee") or ""),
                "parent_task_id": parent_id,
                "finding_code": "parent_base_absorb_needed_mismatch",
                "field": "base_absorb_needed",
                "expected": parent_absorb,
                "actual": child_absorb,
                "message": (
                    f"child base_absorb_needed {child_absorb} mismatches "
                    f"parent {parent_id} {parent_absorb}"
                ),
            })
    repo_root = _task_repo_root()
    return {
        "ok": not findings,
        "team": repo_root.name,
        "repo_root": str(repo_root),
        "active_only": active_only,
        "scanned_tasks": len(rows),
        "finding_count": len(findings),
        "filters": {
            "assignee": assignee or "",
            "topic": _clean_topic(topic or ""),
            "parent_task_id": str(parent_task_id or "").strip(),
        },
        "findings": findings,
    }
