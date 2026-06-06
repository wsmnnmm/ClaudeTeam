"""Cross-team collaboration track store.

One JSON file per team: `$CLAUDETEAM_STATE_DIR/cross-track.json`

Each track is a bidirectional handshake between two teams. Track IDs are
globally unique (XT-<timestamp>-<random>) so both sides reference the same
identifier — no ID mapping needed.

Status machine:
  pending     → 已派发，等待对方接收
  accepted    → 对方已接收并确认
  in_progress → 对方开始处理
  delivering  → 对方已交付，等待我方验收
  completed   → 我方验收通过，闭环
  rejected    → 对方拒绝
  cancelled   → 我方取消

Rejected and cancelled are terminal states reachable from any non-terminal.
"""
from __future__ import annotations

import random
import string
from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.util import flock, now_ms, read_json, write_json

VALID_STATUSES = {
    "pending", "accepted", "in_progress",
    "delivering", "completed", "rejected", "cancelled",
}
TERMINAL_STATUSES = {"completed", "rejected", "cancelled"}
ACTIVE_STATUSES = {"pending", "accepted", "in_progress", "delivering"}
UNKNOWN_PARTNER_TEAM = "unknown_source_team"
UNKNOWN_PARTNER_LABEL = "Unknown Source Team"
UNBOUND_TOPIC = "[unbound]"

# Valid transitions
_ALLOWED = {
    "pending":     {"accepted", "rejected", "cancelled"},
    "accepted":    {"in_progress", "delivering", "rejected", "cancelled"},
    "in_progress": {"delivering", "rejected", "cancelled"},
    "delivering":  {"completed", "rejected"},
    "completed":   set(),
    "rejected":    set(),
    "cancelled":   set(),
}


def _file() -> Path:
    return paths.state_dir() / "cross-track.json"


def _locked():
    return flock(Path(str(_file()) + ".lock"))


def _load() -> dict:
    return read_json(_file(), {"tracks": [], "_meta": {"last_id": 0}})


def _save(data: dict) -> None:
    write_json(_file(), data)


def _new_track_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"XT-{now_ms()}-{suffix}"


def _validate_transition(current: str, target: str) -> None:
    if target not in _ALLOWED.get(current, set()):
        raise ValueError(
            f"Invalid transition: {current} → {target}. "
            f"Allowed from {current}: {sorted(_ALLOWED.get(current, set()))}"
        )


def _status_order(status: str) -> int:
    return {
        "pending": 0, "accepted": 1, "in_progress": 2,
        "delivering": 3, "completed": 4, "rejected": 5, "cancelled": 5,
    }.get(status, 9)


def _append_message(track: dict, direction: str, content: str) -> None:
    track.setdefault("message_history", []).append({
        "direction": direction,
        "content": str(content or ""),
        "at": now_ms(),
    })


# ── public API ──────────────────────────────────────────────────────


def create(*, partner_team: str, partner_label: str = "",
           topic: str = "", source_agent: str = "",
           target_agent: str = "", local_task_id: str = "",
           initial_message: str = "") -> str:
    """Create a new outbound track entry. Returns track_id."""
    if not partner_team.strip():
        raise ValueError("partner_team is required")
    track_id = _new_track_id()
    with _locked():
        data = _load()
        data["_meta"]["last_id"] = data["_meta"].get("last_id", 0) + 1
        now = now_ms()
        track = {
            "track_id": track_id,
            "direction": "outbound",
            "partner_team": partner_team.strip(),
            "partner_label": partner_label,
            "topic": str(topic or ""),
            "status": "pending",
            "source_agent": source_agent,
            "target_agent": target_agent,
            "local_task_id": local_task_id,
            "partner_task_id": "",
            "partner_track_id": "",
            "artifact": "",
            "message_history": [],
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        if initial_message:
            _append_message(track, "out", initial_message)
        data.setdefault("tracks", []).append(track)
        _save(data)
        return track_id


def accept(track_id: str, *, message: str = "",
           partner_track_id: str = "", partner_task_id: str = "",
           source_agent: str = "",
           partner_team: str = "",
           partner_label: str = "",
           topic: str = "") -> str | None:
    """Accept an inbound cross-track request. Creates an inbound entry if
    it doesn't exist, or transitions existing outbound to 'accepted'.

    Returns the track_id on success, None if not found.
    """
    if not track_id:
        raise ValueError("track_id is required")
    with _locked():
        data = _load()
        for track in data.get("tracks", []):
            if track["track_id"] != track_id:
                continue
            _validate_transition(track["status"], "accepted")
            track["status"] = "accepted"
            track["updated_at"] = now_ms()
            if partner_track_id:
                track["partner_track_id"] = partner_track_id
            if partner_task_id:
                track["partner_task_id"] = partner_task_id
            if partner_team and not str(track.get("partner_team") or "").strip():
                track["partner_team"] = partner_team.strip()
            if partner_label and not str(track.get("partner_label") or "").strip():
                track["partner_label"] = partner_label.strip()
            if topic and not str(track.get("topic") or "").strip():
                track["topic"] = str(topic)
            if message:
                _append_message(track, "in", message)
            _save(data)
            return track_id

        # No existing entry → create inbound
        now = now_ms()
        resolved_partner_team = partner_team.strip() or UNKNOWN_PARTNER_TEAM
        resolved_partner_label = partner_label.strip() or UNKNOWN_PARTNER_LABEL
        resolved_topic = str(topic or "").strip() or UNBOUND_TOPIC
        track = {
            "track_id": track_id,
            "direction": "inbound",
            "partner_team": resolved_partner_team,
            "partner_label": resolved_partner_label,
            "topic": resolved_topic,
            "status": "accepted",
            "source_agent": source_agent,
            "target_agent": "",
            "local_task_id": partner_task_id,
            "partner_task_id": "",
            "partner_track_id": partner_track_id,
            "artifact": "",
            "message_history": [],
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        if message:
            _append_message(track, "in", message)
        data.setdefault("tracks", []).append(track)
        data["_meta"]["last_id"] = data["_meta"].get("last_id", 0) + 1
        _save(data)
        return track_id


def transition(track_id: str, to_status: str, *,
               message: str = "", direction: str = "",
               artifact: str | None = None,
               partner_task_id: str | None = None) -> bool:
    """Transition a track to a new status. Returns False if not found."""
    if to_status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {to_status}")
    with _locked():
        data = _load()
        for track in data.get("tracks", []):
            if track["track_id"] != track_id:
                continue
            _validate_transition(track["status"], to_status)
            track["status"] = to_status
            track["updated_at"] = now_ms()
            if to_status in TERMINAL_STATUSES:
                track["completed_at"] = now_ms()
            if message:
                _append_message(track, direction or _dir_for(track), message)
            if artifact is not None:
                track["artifact"] = artifact
            if partner_task_id is not None:
                track["partner_task_id"] = partner_task_id
            _save(data)
            return True
    return False


def _dir_for(track: dict) -> str:
    return "out" if track.get("direction") == "outbound" else "in"


def add_message(track_id: str, direction: str, content: str) -> bool:
    """Append a message to the track's history without changing status."""
    with _locked():
        data = _load()
        for track in data.get("tracks", []):
            if track["track_id"] != track_id:
                continue
            _append_message(track, direction, content)
            track["updated_at"] = now_ms()
            _save(data)
            return True
    return False


def get(track_id: str) -> dict | None:
    """Return one track by id, or None."""
    for track in _load().get("tracks", []):
        if track["track_id"] == track_id:
            return dict(track)
    return None


def list_tracks(*, direction: str | None = None,
                status: str | None = None,
                partner_team: str | None = None) -> list[dict]:
    """List tracks filtered by direction / status / partner_team.

    Sorted by status severity then created_at desc.
    """
    rows = list(_load().get("tracks", []))
    if direction is not None:
        rows = [t for t in rows if t.get("direction") == direction]
    if status is not None:
        rows = [t for t in rows if t.get("status") == status]
    if partner_team is not None:
        rows = [t for t in rows
                if str(t.get("partner_team") or "").casefold() == partner_team.casefold()]
    rows.sort(key=lambda t: (_status_order(t.get("status", "")),
                             -(t.get("created_at", 0))))
    return rows


def count_active(direction: str | None = None) -> int:
    """Count tracks that are NOT in a terminal state."""
    tracks = list_tracks(direction=direction)
    return sum(1 for t in tracks if t.get("status") not in TERMINAL_STATUSES)


def partner_label_for(track_id: str) -> str:
    """Resolve a display label for the partner team from the track entry."""
    t = get(track_id)
    return (t.get("partner_label") or t.get("partner_team") or "") if t else ""
