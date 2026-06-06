"""`claudeteam cross-track <action> ...`

Stable bidirectional cross-team collaboration protocol.

Actions:
  dispatch  <team-ref> <to> <from> <message>  派发跨团队任务
  accept    <track-id> [--message <msg>]        接收并确认
  progress  <track-id> [--message <msg>]        进度更新
  deliver   <track-id> --artifact <path>        交付成果
  ack       <track-id>                          验收通过，闭环
  reject    <track-id> --reason <reason>        拒绝
  list      [--direction in|out] [--status <s>] 列出
  show      <track-id>                          详情
  status                                        统计摘要

Every action that changes state also triggers a return message to the
partner team via cross-send, carrying --cross-track-id and
--cross-track-action flags so the partner's cross-track store is
automatically updated.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from claudeteam.commands import cross_send as _cross_send
from claudeteam.runtime import config, team_registry
from claudeteam.store import cross_track as store, tasks as task_store
from claudeteam.util import error_exit, pop_flag, pop_bool_flag, usage_error

USAGE = (
    "usage: claudeteam cross-track <action> [...]\n"
    "  dispatch <team-ref> <to> <from> <message> [--topic <t>] [--task-id <T-id>] [--priority <p>]\n"
    "  accept   <track-id> [--message <msg>]\n"
    "  progress <track-id> [--message <msg>]\n"
    "  deliver  <track-id> --artifact <path> [--message <msg>]\n"
    "  ack      <track-id>\n"
    "  reject   <track-id> --reason <reason>\n"
    "  list     [--direction in|out] [--status <s>] [--team <team>]\n"
    "  show     <track-id>\n"
    "  status"
)

SUCCESS_EMOJI = "✅"
FAIL_EMOJI = "❌"


def _team_name() -> str:
    """Short team key derived from state dir name."""
    d = Path(config.state_dir())
    return d.parent.name if d.name == "state" else d.name


def _dispatch(argv: list[str]) -> int:
    rest = list(argv)
    topic = pop_flag(rest, "--topic") or ""
    local_task_id = pop_flag(rest, "--task-id") or ""
    if len(rest) < 4:
        return usage_error(USAGE)
    team_ref, to, frm, message = rest[:4]
    priority = rest[4] if len(rest) > 4 else "高"
    if local_task_id and task_store.get(local_task_id) is None:
        return error_exit(f"{FAIL_EMOJI} no such task: {local_task_id}")

    # Resolve target team to get its label
    target = _cross_send._resolve_target(
        team_ref, root=_cross_send._default_root(),
        registry_script=None, remote_state_dir=None,
    )
    partner_label = target.label if target else team_ref

    track_id = store.create(
        partner_team=team_ref,
        partner_label=partner_label,
        topic=topic,
        source_agent=frm,
        target_agent=to,
        local_task_id=local_task_id,
        initial_message=message,
    )

    # Cross-send with track markers so target can ack
    marked = (f"[cross-track: {track_id}] [action: dispatch]\n{message}")
    rc = _cross_send.main([
        team_ref, to, frm, marked, priority,
    ])

    if rc == 0:
        print(f"{SUCCESS_EMOJI} Dispatched {track_id} → {partner_label}")
        print(f"   Track: claudeteam cross-track show {track_id}")
    else:
        store.transition(track_id, "cancelled",
                         message=f"Dispatch failed (cross-send rc={rc})")
        print(f"{FAIL_EMOJI} Dispatch failed (cross-send rc={rc})")
    return rc


def _accept(argv: list[str]) -> int:
    rest = list(argv)
    message = pop_flag(rest, "--message") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    track_id = rest[0]

    existing = store.get(track_id)
    if existing is None:
        store.accept(track_id, message=message or "已接收",
                     source_agent="manager")
    else:
        store.transition(track_id, "accepted", message=message or "已接收")

    _send_ack(track_id, "accept", message or "已接收，开始处理")
    print(f"{SUCCESS_EMOJI} Accepted {track_id}")
    return 0


def _progress(argv: list[str]) -> int:
    rest = list(argv)
    message = pop_flag(rest, "--message") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    track_id = rest[0]

    if not store.transition(track_id, "in_progress", message=message):
        return error_exit(f"{FAIL_EMOJI} track not found: {track_id}")

    _send_ack(track_id, "progress", message or "处理中")
    print(f"{SUCCESS_EMOJI} Progress {track_id}")
    return 0


def _deliver(argv: list[str]) -> int:
    rest = list(argv)
    artifact = pop_flag(rest, "--artifact") or ""
    message = pop_flag(rest, "--message") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    if not artifact:
        return error_exit(f"{FAIL_EMOJI} --artifact is required for deliver")
    track_id = rest[0]

    delivery_msg = message or f"已交付，artifact: {artifact}"
    if not store.transition(track_id, "delivering", message=delivery_msg,
                            artifact=artifact):
        return error_exit(f"{FAIL_EMOJI} track not found: {track_id}")

    _send_ack(track_id, "deliver", delivery_msg)
    print(f"{SUCCESS_EMOJI} Delivered {track_id}")
    return 0


def _ack(argv: list[str]) -> int:
    rest = list(argv)
    if len(rest) < 1:
        return usage_error(USAGE)
    track_id = rest[0]

    track = store.get(track_id)
    if track is None:
        return error_exit(f"{FAIL_EMOJI} track not found: {track_id}")
    if track["status"] != "delivering":
        return error_exit(
            f"{FAIL_EMOJI} can only ack a 'delivering' track; "
            f"current: {track['status']}")

    store.transition(track_id, "completed", message="验收通过，闭环",
                     direction="out")
    _send_ack(track_id, "ack", "验收通过，跨团队协作闭环")
    print(f"{SUCCESS_EMOJI} Closed {track_id} — loop complete")
    return 0


def _reject(argv: list[str]) -> int:
    rest = list(argv)
    reason = pop_flag(rest, "--reason") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    if not reason:
        return error_exit(f"{FAIL_EMOJI} --reason is required for reject")
    track_id = rest[0]

    if not store.transition(track_id, "rejected", message=reason):
        return error_exit(f"{FAIL_EMOJI} track not found: {track_id}")

    _send_ack(track_id, "reject", f"已拒绝: {reason}")
    print(f"{FAIL_EMOJI} Rejected {track_id}")
    return 0


def _list(argv: list[str]) -> int:
    rest = list(argv)
    direction = pop_flag(rest, "--direction") or None
    status = pop_flag(rest, "--status") or None
    partner = pop_flag(rest, "--team") or None

    tracks = store.list_tracks(direction=direction, status=status,
                               partner_team=partner)
    if not tracks:
        print("(no cross-track entries)")
        return 0

    for t in tracks:
        d = "→" if t["direction"] == "outbound" else "←"
        partner = t.get("partner_label") or t.get("partner_team") or "?"
        topic = t.get("topic", "")[:60]
        print(f"  {t['track_id']}  [{t['status']:12}] {d} {partner}  {topic}")
    return 0


def _show(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    track_id = argv[0]
    t = store.get(track_id)
    if t is None:
        return error_exit(f"{FAIL_EMOJI} track not found: {track_id}")

    d = "→ 派给" if t["direction"] == "outbound" else "← 来自"
    partner = t.get("partner_label") or t.get("partner_team") or "?"
    print(f"Track:    {t['track_id']}")
    print(f"Direction: {t['direction']} ({d} {partner})")
    print(f"Status:    {t['status']}")
    print(f"Topic:     {t.get('topic', '')}")
    print(f"Agents:    {t.get('source_agent', '?')} → {t.get('target_agent', '?')}")
    if t.get("local_task_id"):
        print(f"Task:      {t['local_task_id']}")
    if t.get("partner_task_id"):
        print(f"Partner:   {t['partner_task_id']}")
    if t.get("artifact"):
        print(f"Artifact:  {t['artifact']}")
    print("Messages:")
    for msg in t.get("message_history", []):
        arrow = "→" if msg["direction"] == "out" else "←"
        print(f"  {arrow} {msg['content'][:120]}")
    return 0


def _status(argv: list[str]) -> int:
    active = store.count_active()
    out = store.count_active(direction="outbound")
    inp = store.count_active(direction="inbound")

    print(f"Cross-track: {active} active ({out} outbound, {inp} inbound)")
    if active == 0:
        return 0

    print()
    tracks = store.list_tracks()
    for t in tracks:
        if t["status"] in store.TERMINAL_STATUSES:
            continue
        d = "→" if t["direction"] == "outbound" else "←"
        partner = t.get("partner_label") or t.get("partner_team") or "?"
        print(f"  [{t['status']:12}] {d} {partner}  {t['track_id']}  {t.get('topic','')[:60]}")
    return 0


# ── return-path helpers ────────────────────────────────────────────


def _resolve_return_target(track: dict) -> tuple[str, str]:
    """Find the partner team ref so we can cross-send back.

    For inbound tracks: partner_team comes from the message context.
    For outbound tracks: partner_team is stored.
    """
    partner = track.get("partner_team") or ""
    if not partner and track.get("direction") == "inbound":
        # Try to reverse-lookup from message history
        for msg in track.get("message_history", []):
            if msg["direction"] == "out":
                import re
                m = re.search(r'from\s+(\S+)', msg.get("content", ""))
                if m:
                    partner = m.group(1)
                    break
    return partner, track.get("partner_label") or partner


def _send_ack(track_id: str, action: str, message: str) -> None:
    """Send a cross-track acknowledgment back to the partner team.

    Uses cross-send with --cross-track-id and --cross-track-action so
    the partner's cross-track store is automatically updated on receipt.
    """
    track = store.get(track_id)
    if track is None:
        return

    partner, _ = _resolve_return_target(track)
    if not partner:
        return

    frm = "manager"
    to = "manager"
    if track["direction"] == "outbound":
        frm = track.get("source_agent") or "manager"
        to = track.get("target_agent") or "manager"
    else:
        frm = track.get("source_agent") or "manager"
        to = "manager"

    marked = f"[cross-track: {track_id}] [action: {action}]\n{message}"

    try:
        _cross_send.main([
            partner, to, frm, marked, "高",
            "--cross-track-id", track_id,
            "--cross-track-action", action,
        ])
    except Exception as e:
        print(f"  ⚠️ ack send failed ({action}): {e}", file=sys.stderr)


# ── main ───────────────────────────────────────────────────────────


def _apply_remote_action(track_id: str, action: str, message: str,
                         source_team: str = "", source_label: str = "") -> None:
    """Apply a cross-track action from a remote ack. Called both in-process
    (by cross_send._cross_track_update_local) and via SSH exec on remote hosts."""
    import re
    from claudeteam.store import cross_track as ct
    clean = re.sub(r'\[cross-track:[^\]]+\]\s*\[action:[^\]]+\]\s*', '', message).strip()
    status_map = {
        "accept":   "accepted",
        "progress": "in_progress",
        "deliver":  "delivering",
        "ack":      "completed",
        "reject":   "rejected",
    }
    new_status = status_map.get(action)
    if new_status is None:
        return
    existing = ct.get(track_id)
    if existing is None:
        ct.accept(
            track_id, message=clean, source_agent="manager",
            partner_team=source_team, partner_label=source_label,
        )
        if new_status != "accepted":
            ct.transition(track_id, new_status, message=clean)
    else:
        direction = "in" if existing.get("direction") == "outbound" else "out"
        ct.transition(track_id, new_status, message=clean, direction=direction)


def main(argv: list[str], *, run: Callable = subprocess.run) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)

    action = argv[0]
    rest = argv[1:]

    handlers = {
        "dispatch": _dispatch,
        "accept":   _accept,
        "progress": _progress,
        "deliver":  _deliver,
        "ack":      _ack,
        "reject":   _reject,
        "list":     _list,
        "show":     _show,
        "status":   _status,
    }

    handler = handlers.get(action)
    if handler is None:
        return usage_error(USAGE)

    return handler(rest)
