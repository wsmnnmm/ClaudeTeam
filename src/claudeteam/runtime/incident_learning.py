"""Incident → Learning bridge — the L5 self-evolution engine.

Every gate (first_output, artifact, quality_guard, topic_drift, api_cost)
already produces structured failure data. This module closes the loop:
detect incident → classify root cause → persist learning → inject into
future agent prompts → track whether it prevented recurrence.

The bridge is intentionally small. The heavy lifting (memory store, cross-team
pool, prompt injection) already exists in store/memory.py, store/cross_learnings.py,
and agents/identity.py. This module just connects the dots.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from claudeteam.runtime import paths, tunables


# ── data types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Incident:
    """A gate firing — the raw event before it becomes a learning."""
    incident_type: str  # first_output_failure, artifact_missing, quality_guard, ...
    agent: str
    pattern: str        # machine-readable: "空话", "无证据", "missing screenshot", ...
    detail: str         # human-readable description
    task_id: str = ""
    severity: str = "warn"   # info, warn, critical

    def fingerprint(self) -> str:
        """Stable ID for dedup within a time window."""
        base = f"{self.incident_type}|{self.agent}|{self.pattern}"
        return hashlib.sha256(base.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {
            "incident_type": self.incident_type,
            "agent": self.agent,
            "pattern": self.pattern,
            "detail": self.detail,
            "task_id": self.task_id,
            "severity": self.severity,
        }


@dataclass
class LearningRecord:
    """A persisted learning with effectiveness tracking."""
    learning_id: str
    lesson: str
    category: str
    source_incidents: list[dict] = field(default_factory=list)
    created_at: int = 0
    last_seen_at: int = 0
    seen_count: int = 0
    prevented_count: int = 0   # times this learning was applied and incident DID NOT recur
    failed_count: int = 0      # times incident recurred despite this learning


# ── root cause classifiers ─────────────────────────────────────────────


_WORKER_CLASSIFIERS: dict[str, str] = {
    "空话": "worker replied with vague acknowledgement instead of evidence",
    "无证据": "worker output contained no verifiable artifact or reference",
    "证据不可用": "worker referenced a broken or inaccessible file/URL",
    "证据字段缺失": "worker did not include an artifact reference at all",
    "blocker 不可行动": "worker reported a blocker without actionable detail",
    "结构化摘要不完整": "worker research summary missing required sections",
    "短卡不完整": "worker short card missing required fields",
    "四点现场不完整": "worker four-point report incomplete",
}

_ARTIFACT_CLASSIFIERS: dict[str, str] = {
    "screenshot image": "task marked done without UI screenshot evidence",
    "http(s) preview URL": "task marked done without clickable preview URL",
    "screenshot image, http(s) preview URL": "task marked done without any visual evidence",
}

_QUALITY_CLASSIFIERS: dict[str, str] = {
    "internal_token_leak": "boss-visible message leaked internal jargon",
    "empty_list_item": "boss-visible message had empty markdown list items",
    "path_only_delivery": "boss-visible message only gave local path, no summary",
    "cli_flag_only": "boss-visible message contained only CLI flags",
    "completion_without_evidence": "completion claim without verification evidence",
    "double_escaped_tokens": "message contained double-escaped layout tokens",
}


def classify_root_cause(incident: Incident) -> str:
    """Produce a human-readable root cause explanation."""
    classifiers = {}
    if incident.incident_type == "first_output_failure":
        classifiers = _WORKER_CLASSIFIERS
    elif incident.incident_type == "artifact_missing":
        classifiers = _ARTIFACT_CLASSIFIERS
    elif incident.incident_type == "quality_guard":
        classifiers = _QUALITY_CLASSIFIERS
    explanation = classifiers.get(incident.pattern, f"unknown pattern: {incident.pattern}")
    if incident.task_id:
        explanation += f" (task: {incident.task_id})"
    return explanation


# ── state persistence ──────────────────────────────────────────────────


def _state_file() -> Path:
    return paths.state_file("incident-learnings.json")


def _load_state() -> dict:
    path = _state_file()
    default = {"incidents": {}, "learnings": [], "task_links": {}}
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(default)
        out = dict(default)
        out.update(data)
        if not isinstance(out.get("incidents"), dict):
            out["incidents"] = {}
        if not isinstance(out.get("learnings"), list):
            out["learnings"] = []
        if not isinstance(out.get("task_links"), dict):
            out["task_links"] = {}
        return out
    except (OSError, json.JSONDecodeError):
        return dict(default)


def _save_state(data: dict) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── core capture logic ─────────────────────────────────────────────────


def _debounce_window_s() -> int:
    return int(tunables.tunable("incident_learning.debounce_s", 600))


def _learning_threshold() -> int:
    return int(tunables.tunable("incident_learning.threshold", 3))


def _is_duplicate_incident(fp: str, now: int) -> bool:
    state = _load_state()
    incidents = state.get("incidents", {})
    last_ts = incidents.get(fp, 0)
    return (now - last_ts) < _debounce_window_s()


def _record_incident(fp: str, now: int) -> None:
    state = _load_state()
    state.setdefault("incidents", {})[fp] = now
    _save_state(state)


def _find_similar_learning(incident: Incident) -> LearningRecord | None:
    """Check if a learning for this pattern already exists."""
    state = _load_state()
    for entry in state.get("learnings", []):
        if entry.get("category") == incident.incident_type and entry.get("pattern") == incident.pattern:
            lr = LearningRecord(
                learning_id=entry.get("learning_id", ""),
                lesson=entry.get("lesson", ""),
                category=entry.get("category", ""),
                source_incidents=entry.get("source_incidents", []),
                created_at=entry.get("created_at", 0),
                last_seen_at=entry.get("last_seen_at", 0),
                seen_count=entry.get("seen_count", 0),
                prevented_count=entry.get("prevented_count", 0),
                failed_count=entry.get("failed_count", 0),
            )
            lr.source_incidents = entry.get("source_incidents", [])
            return lr
    return None


def _learning_entry_by_id(state: dict, learning_id: str) -> dict | None:
    for entry in state.get("learnings", []):
        if entry.get("learning_id") == learning_id:
            return entry
    return None


def get_learning(learning_id: str) -> dict | None:
    """Fetch one persisted learning by id."""
    state = _load_state()
    entry = _learning_entry_by_id(state, learning_id)
    return dict(entry) if isinstance(entry, dict) else None


def register_task_context(task_id: str, *,
                          task_title: str = "",
                          task_description: str = "",
                          assignee: str = "",
                          limit: int = 3) -> list[dict]:
    """Attach currently relevant learnings to a task's runtime context.

    This is the "learning gets used in real work" bridge: once a task is
    created, we snapshot the most relevant learnings so later success can
    count as `applied` and same-task relapse can count as `recurred`.
    """
    if not task_id:
        return []
    rows = find_relevant(
        task_title=task_title,
        task_description=task_description,
        assignee=assignee,
        limit=limit,
    )
    if not rows:
        return []
    learning_ids = [
        str(row.get("learning_id") or "").strip()
        for row in rows
        if str(row.get("learning_id") or "").strip()
    ]
    if not learning_ids:
        return []
    state = _load_state()
    links = state.setdefault("task_links", {})
    existing = dict(links.get(task_id) or {})
    merged_ids: list[str] = []
    for lid in list(existing.get("learning_ids", [])) + learning_ids:
        if lid and lid not in merged_ids:
            merged_ids.append(lid)
    links[task_id] = {
        "learning_ids": merged_ids,
        "applied_ids": list(existing.get("applied_ids", [])),
        "recurred_ids": list(existing.get("recurred_ids", [])),
        "task_title": task_title,
        "assignee": assignee,
    }
    _save_state(state)
    return rows


def _mark_linked_task_recurrence(incident: Incident) -> list[str]:
    if not incident.task_id:
        return []
    state = _load_state()
    links = state.get("task_links", {})
    link = dict(links.get(incident.task_id) or {})
    learning_ids = list(link.get("learning_ids", []))
    if not learning_ids:
        return []
    recurred_ids = list(link.get("recurred_ids", []))
    matched: list[str] = []
    for learning_id in learning_ids:
        if learning_id in recurred_ids:
            continue
        entry = _learning_entry_by_id(state, learning_id)
        if not entry:
            continue
        if entry.get("category") != incident.incident_type:
            continue
        entry["failed_count"] = int(entry.get("failed_count", 0)) + 1
        recurred_ids.append(learning_id)
        matched.append(learning_id)
    if not matched:
        return []
    link["recurred_ids"] = recurred_ids
    links[incident.task_id] = link
    _save_state(state)
    return matched


def _incident_count_for_pattern(incident_type: str, pattern: str) -> int:
    state = _load_state()
    count = 0
    for lr in state.get("learnings", []):
        for si in lr.get("source_incidents", []):
            if si.get("incident_type") == incident_type and si.get("pattern") == pattern:
                count += 1
    return count


def _render_lesson(incident: Incident) -> str:
    """Distill an incident into a concise, actionable lesson."""
    root_cause = classify_root_cause(incident)
    agent_label = incident.agent or "unknown agent"
    if incident.incident_type == "first_output_failure":
        return (
            f"[{agent_label}] 首产物不合格: {incident.pattern}。"
            f"根因: {root_cause}。"
            f"下次派工时应明确要求证据格式（artifact/URL/截图/阻塞报告），"
            f"并在首响窗口内检查是否符合 first_output_gate 标准。"
        )
    if incident.incident_type == "artifact_missing":
        return (
            f"[{agent_label}] 任务完成缺少证据: {incident.pattern}。"
            f"根因: {root_cause}。"
            f"涉及 UI/视觉的任务完成前必须附带截图和预览链接，"
            f"否则应标记为阻塞而非已完成。"
        )
    if incident.incident_type == "quality_guard":
        return (
            f"[{agent_label}] boss可见消息被门禁拦截: {incident.pattern}。"
            f"根因: {root_cause}。"
            f"发 boss 消息前检查：人话摘要、证据、下一步、不要内部术语。"
        )
    if incident.incident_type == "api_cost_warning":
        return (
            f"[{agent_label}] API 预算预警: 已使用超过 80%。"
            f"后续调用应优先选择最便宜的可用模型，避免视频/图片生成类高消费 API。"
        )
    if incident.incident_type == "api_cost_block":
        return (
            f"[{agent_label}] API 预算耗尽: 付费调用已被拦截。"
            f"如需继续执行，运行 `claudeteam api-budget reset --limit N`。"
        )
    return f"[{agent_label}] {incident.incident_type}: {incident.detail}"


def _persist_learning_to_memory(incident: Incident, lesson: str) -> None:
    """Write the learning to the agent's durable memory (auto-mirrors cross-team)."""
    try:
        from claudeteam.store import memory
        ref = incident.task_id or incident.fingerprint()
        memory.append(incident.agent, "learning", lesson, ref=ref)
    except Exception:
        pass


def _upsert_learning(incident: Incident, lesson: str, now: int) -> LearningRecord:
    """Create or update a learning record in the incident-learnings state."""
    state = _load_state()
    learning_id = hashlib.sha256(
        f"{incident.incident_type}|{incident.pattern}".encode()
    ).hexdigest()[:10]

    for entry in state.get("learnings", []):
        if entry.get("learning_id") == learning_id:
            entry["source_incidents"].append(incident.to_dict())
            entry["last_seen_at"] = now
            entry["seen_count"] = entry.get("seen_count", 0) + 1
            _save_state(state)
            lr = LearningRecord(
                learning_id=learning_id, lesson=lesson,
                category=incident.incident_type,
                source_incidents=entry["source_incidents"],
                created_at=entry.get("created_at", now),
                last_seen_at=now,
                seen_count=entry["seen_count"],
                prevented_count=entry.get("prevented_count", 0),
                failed_count=entry.get("failed_count", 0),
            )
            return lr

    entry = {
        "learning_id": learning_id,
        "lesson": lesson,
        "category": incident.incident_type,
        "pattern": incident.pattern,
        "source_incidents": [incident.to_dict()],
        "created_at": now,
        "last_seen_at": now,
        "seen_count": 1,
        "prevented_count": 0,
        "failed_count": 0,
    }
    state.setdefault("learnings", []).append(entry)
    _save_state(state)
    lr = LearningRecord(
        learning_id=learning_id, lesson=lesson,
        category=incident.incident_type,
        source_incidents=entry["source_incidents"],
        created_at=now, last_seen_at=now, seen_count=1,
    )
    return lr


def capture(incident: Incident, *,
            now_ms_fn: Callable[[], int] | None = None) -> LearningRecord | None:
    """Record an incident and auto-create a learning when threshold is met.

    Debounces duplicate incidents within the configured window. When
    the same pattern has been seen `threshold` times, creates a
    persistent learning in the agent's memory (which auto-mirrors
    to the cross-team pool via store/memory.py).

    Returns None when debounced, or the LearningRecord when a learning
    is created/updated.
    """
    if not _enabled():
        return None
    now = now_ms_fn() if now_ms_fn else int(time.time() * 1000)
    fp = incident.fingerprint()
    if _is_duplicate_incident(fp, now):
        return None
    _record_incident(fp, now)
    _mark_linked_task_recurrence(incident)

    total = _incident_count_for_pattern(incident.incident_type, incident.pattern) + 1
    lesson = _render_lesson(incident)
    threshold = _learning_threshold()

    if total >= threshold:
        _persist_learning_to_memory(incident, lesson)
        return _upsert_learning(incident, lesson, now)

    if total == 1:
        return _upsert_learning(incident, lesson, now)
    return _upsert_learning(incident, lesson, now)


def mark_applied(learning_id: str, *, task_id: str = "",
                 now_ms_fn: Callable[[], int] | None = None) -> bool:
    """Mark a learning as having been applied (used by an agent to prevent recurrence)."""
    now = now_ms_fn() if now_ms_fn else int(time.time() * 1000)
    state = _load_state()
    for entry in state.get("learnings", []):
        if entry.get("learning_id") == learning_id:
            entry["prevented_count"] = entry.get("prevented_count", 0) + 1
            _save_state(state)
            return True
    return False


def mark_recurred(learning_id: str, *,
                  now_ms_fn: Callable[[], int] | None = None) -> bool:
    """Mark that an incident recurred despite a learning — signals ineffective learning."""
    now = now_ms_fn() if now_ms_fn else int(time.time() * 1000)
    state = _load_state()
    for entry in state.get("learnings", []):
        if entry.get("learning_id") == learning_id:
            entry["failed_count"] = entry.get("failed_count", 0) + 1
            _save_state(state)
            return True
    return False


def mark_task_applied(task_id: str, *,
                      now_ms_fn: Callable[[], int] | None = None) -> list[str]:
    """Mark all learnings linked to a task as successfully applied once."""
    now = now_ms_fn() if now_ms_fn else int(time.time() * 1000)
    state = _load_state()
    links = state.get("task_links", {})
    link = dict(links.get(task_id) or {})
    learning_ids = list(link.get("learning_ids", []))
    if not learning_ids:
        return []
    applied_ids = list(link.get("applied_ids", []))
    newly_applied: list[str] = []
    for learning_id in learning_ids:
        if learning_id in applied_ids:
            continue
        entry = _learning_entry_by_id(state, learning_id)
        if not entry:
            continue
        entry["prevented_count"] = int(entry.get("prevented_count", 0)) + 1
        applied_ids.append(learning_id)
        newly_applied.append(learning_id)
    if not newly_applied:
        return []
    link["applied_ids"] = applied_ids
    link["last_applied_at"] = now
    links[task_id] = link
    _save_state(state)
    return newly_applied


# ── relevance matching ─────────────────────────────────────────────────


def _text_terms(text: str) -> set[str]:
    """Extract meaningful terms for relevance matching."""
    terms: set[str] = set()
    # Chinese: take 2-char+ sequences
    for m in re.findall(r"[一-鿿]{2,}", text):
        terms.add(m)
    # English: take 3-char+ words
    for m in re.findall(r"[a-zA-Z]{3,}", text.lower()):
        terms.add(m)
    return terms


def find_relevant(task_title: str = "", task_description: str = "",
                  assignee: str = "", *, limit: int = 5) -> list[dict]:
    """Find past learnings relevant to a new task or message.

    Matches by term overlap, agent-specific history, and recency.
    Returns learnings sorted by relevance (most relevant first).
    """
    query_terms = _text_terms(f"{task_title} {task_description}")
    if assignee:
        query_terms.update(_text_terms(assignee))
        query_terms.add(assignee)

    state = _load_state()
    scored: list[tuple[int, dict]] = []
    now = int(time.time() * 1000)

    for entry in state.get("learnings", []):
        lesson_terms = _text_terms(entry.get("lesson", ""))
        if not query_terms or not lesson_terms:
            continue

        overlap = len(query_terms & lesson_terms)
        if overlap == 0:
            continue

        # Score: term overlap + recency bonus + severity bonus
        age_hours = max(0, (now - entry.get("created_at", now)) / 3_600_000)
        recency_bonus = max(0, 5 - int(age_hours / 24))  # 5→0 over 5 days

        severity_bonus = 0
        for si in entry.get("source_incidents", []):
            if si.get("severity") == "critical":
                severity_bonus = 3
                break
            if si.get("severity") == "warn":
                severity_bonus = max(severity_bonus, 1)

        score = overlap * 10 + recency_bonus + severity_bonus
        scored.append((score, dict(entry)))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:limit]]


# ── stats and reporting ────────────────────────────────────────────────


def stats() -> dict:
    """Return effectiveness statistics for the learning system."""
    state = _load_state()
    learnings = state.get("learnings", [])
    now = int(time.time() * 1000)

    total = len(learnings)
    applied = sum(1 for l in learnings if l.get("prevented_count", 0) > 0)
    ineffective = sum(1 for l in learnings if l.get("failed_count", 0) > l.get("prevented_count", 0))

    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for l in learnings:
        cat = l.get("category", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1
        for si in l.get("source_incidents", []):
            sev = si.get("severity", "info")
            by_severity[sev] = by_severity.get(sev, 0) + 1

    recent_7d = sum(
        1 for l in learnings
        if (now - l.get("created_at", 0)) < 7 * 24 * 3_600_000
    )

    return {
        "total_learnings": total,
        "learnings_applied": applied,
        "learnings_ineffective": ineffective,
        "recent_7d": recent_7d,
        "by_category": by_category,
        "by_severity": by_severity,
        "application_rate": round(applied / max(total, 1) * 100, 1),
    }


def list_learnings(*, category: str = "", limit: int = 50) -> list[dict]:
    """List all learnings, optionally filtered by category."""
    state = _load_state()
    rows = state.get("learnings", [])
    if category:
        rows = [r for r in rows if r.get("category") == category]
    rows.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return rows[:limit]


def _enabled() -> bool:
    return bool(tunables.tunable("incident_learning.enabled", True))


# ── incident factory helpers ────────────────────────────────────────────


def from_first_output_gate(agent: str, task_id: str,
                           reason: str, detail: str = "") -> Incident:
    """Create an Incident from a first_output_gate failure."""
    return Incident(
        incident_type="first_output_failure",
        agent=agent,
        pattern=reason,
        detail=detail or reason,
        task_id=task_id,
        severity="warn",
    )


def from_artifact_gate(agent: str, task_id: str,
                       missing: list[str]) -> Incident:
    """Create an Incident from an artifact_gate failure."""
    pattern = ", ".join(missing) if missing else "unknown"
    return Incident(
        incident_type="artifact_missing",
        agent=agent,
        pattern=pattern,
        detail=f"task completed without: {pattern}",
        task_id=task_id,
        severity="warn",
    )


def from_quality_guard(agent: str, reason: str) -> Incident:
    """Create an Incident from a quality guard block."""
    pattern = "unknown"
    for key in _QUALITY_CLASSIFIERS:
        if key in reason or key.replace("_", " ") in reason.lower():
            pattern = key
            break
    if pattern == "unknown":
        if "internal" in reason.lower() or "内部" in reason:
            pattern = "internal_token_leak"
        elif "empty" in reason.lower() or "空" in reason:
            pattern = "empty_list_item"
        elif "evidence" in reason.lower() or "证据" in reason:
            pattern = "completion_without_evidence"
        else:
            pattern = reason[:50]

    severity = "critical" if any(
        m in reason for m in ("BUDGET EXCEEDED", "block", "API key", "token")
    ) else "warn"
    return Incident(
        incident_type="quality_guard",
        agent=agent,
        pattern=pattern,
        detail=reason,
        severity=severity,
    )


def from_api_cost_guard(agent: str, action: str, provider: str,
                        estimated_cost: float) -> Incident:
    """Create an Incident from an API cost guard action."""
    incident_type = "api_cost_block" if action == "block" else "api_cost_warning"
    return Incident(
        incident_type=incident_type,
        agent=agent,
        pattern=provider,
        detail=f"action={action}, provider={provider}, "
               f"estimated_cost_usd={estimated_cost:.4f}",
        severity="critical" if action == "block" else "warn",
    )


# ── render for prompt (inject into init) ────────────────────────────────


def render_for_prompt(*, limit: int = 5, agent: str = "") -> str:
    """Markdown snippet of recent high-severity learnings for init prompt injection.

    Unlike cross_learnings.render_for_prompt() which shows cross-team pool,
    this renders the structured incident-learnings state with effectiveness
    data, giving agents concrete evidence of what went wrong and was fixed.
    """
    rows = list_learnings(limit=limit * 2)
    if agent:
        rows = [
            r for r in rows
            if any(si.get("agent") == agent for si in r.get("source_incidents", []))
        ][:limit]
    else:
        rows = rows[:limit]

    if not rows:
        return ""

    lines = [
        "## 自进化学习记录 (Incident Learnings)",
        "",
        "以下是从历史事件中自动沉淀的经验教训，执行任务时主动检查避免重复踩坑：",
        "",
    ]
    for i, row in enumerate(rows, 1):
        lesson = row.get("lesson", "")
        cat = row.get("category", "?")
        seen = row.get("seen_count", 0)
        prevented = row.get("prevented_count", 0)
        failed = row.get("failed_count", 0)
        eff = f"出现{seen}次, 预防{prevented}次, 复发{failed}次"
        lines.append(f"{i}. [{cat}] {lesson}  ({eff})")
    lines.append("")
    return "\n".join(lines)


def render_task_context(task_id: str, *, limit: int = 3) -> str:
    """Short task-scoped reminder of relevant historical learnings."""
    if not task_id:
        return ""
    state = _load_state()
    link = dict(state.get("task_links", {}).get(task_id) or {})
    learning_ids = list(link.get("learning_ids", []))
    if not learning_ids:
        return ""
    rows: list[dict] = []
    for learning_id in learning_ids:
        entry = _learning_entry_by_id(state, learning_id)
        if entry:
            rows.append(entry)
    if not rows:
        return ""
    lines = ["历史相关教训（执行前先过一遍）："]
    for i, row in enumerate(rows[:limit], 1):
        lines.append(
            f"{i}. [{row.get('category', '?')}] {row.get('lesson', '')}"
        )
    return "\n".join(lines)
