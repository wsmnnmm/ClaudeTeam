"""Guard manager action after reading a boss message.

`manager_watch.sweep_boss_inbox` catches boss messages that were never read.
This module covers the next failure mode: manager marks a boss message read,
then disappears into long execution without replying, delegating, or reporting a
real blocker.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claudeteam.runtime import config, paths, team_command, tunables
from claudeteam.store import local_facts
from claudeteam.util import flock, fmt_time_ms, now_ms, read_json, write_json


@dataclass(frozen=True)
class ActionNotice:
    local_id: str
    content: str
    age_ms: int
    route_hint: str
    expected_owner: str
    body: str
    public_title: str = ""
    public_body: str = ""


_VISUAL_MARKERS = (
    "图", "图片", "生图", "修图", "封面", "视觉", "配图", "海报",
    "截图标注", "重做", "更正式", "gpt-image",
    "gpt-image-2", "GPT-image",
)
_DESIGN_MARKERS = (
    "设计稿", "MasterGo", "mastergo", "MCP", "mcp", "像素",
    "视觉差异", "UI 还原", "ui 还原", "页面还原", "标注图",
)
_BROWSER_MARKERS = (
    "浏览器", "小红书", "抖音", "视频号", "后台", "草稿", "发布",
    "登录", "截图", "URL", "url", "保存", "平台", "网页",
)
_CODE_MARKERS = (
    "代码", "页面", "UI", "ui", "前端", "接口", "API", "api",
    "联调", "bug", "修复", "实现", "还原", "部署", "测试",
)
_RESEARCH_MARKERS = (
    "研究", "调研", "分析", "复盘", "整理", "对比", "策略",
    "导师", "刘小排", "亦仁", "资料", "课程",
)


def _state_file() -> Path:
    return paths.state_file("manager-action-guard.json")


def _locked():
    return flock(_state_file().with_suffix(".lock"))


def _load_state() -> dict:
    return read_json(_state_file(), {"records": []})


def _save_state(data: dict) -> None:
    write_json(_state_file(), data)


def enabled() -> bool:
    return bool(tunables.tunable("manager_action_guard.enabled", True))


def _list_tunable(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = tunables.tunable(f"manager_action_guard.{name}", list(default))
    if isinstance(raw, list):
        return tuple(str(x) for x in raw if str(x).strip())
    if isinstance(raw, str):
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    return default


def _known_agent(agent: str) -> bool:
    if not agent:
        return False
    try:
        return agent in set(config.agent_names())
    except Exception:
        return False


def _owner(name: str, default: str) -> str:
    raw = str(tunables.tunable(f"manager_action_guard.{name}", default) or "").strip()
    return raw if _known_agent(raw) else ""


def classify_content(content: str) -> tuple[str, str]:
    text = str(content or "")
    buckets = (
        ("visual", "visual_owner", "worker_visual",
         _list_tunable("visual_markers", _VISUAL_MARKERS)),
        ("design", "design_owner", "worker_design",
         _list_tunable("design_markers", _DESIGN_MARKERS)),
        ("browser", "browser_owner", "worker_ops",
         _list_tunable("browser_markers", _BROWSER_MARKERS)),
        ("code", "code_owner", "worker_frontend",
         _list_tunable("code_markers", _CODE_MARKERS)),
        ("research", "research_owner", "worker_research",
         _list_tunable("research_markers", _RESEARCH_MARKERS)),
    )
    scored = []
    for idx, (route, owner_key, default_owner, markers) in enumerate(buckets):
        hits = sum(1 for marker in markers if marker and marker in text)
        if hits:
            scored.append((hits, -idx, route, owner_key, default_owner))
    if scored:
        _, _, route, owner_key, default_owner = sorted(scored)[-1]
        return route, _owner(owner_key, default_owner)
    return "general", _owner("default_owner", "")


def _ms(row: dict, key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _clip(text: str, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else compact[:limit - 1].rstrip() + "…"


def _format_age(ms: int) -> str:
    minutes = max(1, round(ms / 60000))
    return f"{minutes} 分钟"


def _open_records(data: dict) -> list[dict]:
    return [r for r in data.get("records", []) if not r.get("closed_at")]


def _record_sort_key(row: dict) -> tuple[int, int]:
    return (_ms(row, "read_at"), _ms(row, "created_at"))


def _pick_open_record(rows: list[dict], *, expected_owner: str = "",
                      task_id: str = "", local_id: str = "") -> dict | None:
    if not rows:
        return None
    if local_id:
        exact = [row for row in rows if str(row.get("local_id") or "") == local_id]
        if exact:
            return sorted(exact, key=_record_sort_key)[-1]
    if task_id:
        matching_task = [row for row in rows if str(row.get("task_id") or "") == task_id]
        if matching_task:
            return sorted(matching_task, key=_record_sort_key)[-1]
    if expected_owner:
        matching_owner = [
            row for row in rows
            if str(row.get("expected_owner") or "") == expected_owner
        ]
        if matching_owner:
            return sorted(matching_owner, key=_record_sort_key)[-1]
    return sorted(rows, key=_record_sort_key)[-1]


def _iter_known_messages() -> list[dict]:
    names = {"manager", "user"}
    try:
        names.update(config.agent_names())
    except Exception:
        pass
    rows: list[dict] = []
    for name in names:
        rows.extend(local_facts.list_messages(name, unread_only=False))
    return rows


def _compensating_action(record: dict) -> tuple[str, str, str] | None:
    """Find a real action that happened before this guard record existed.

    Normal `say` / `send` paths close the latest open record. A common race is:
    manager replies or delegates first, then runs `claudeteam read`; the guard
    record is created after the real action, so the close hook had nothing to
    close. Treat that already-written evidence as a compensated closure instead
    of alerting.
    """
    since_ms = _ms(record, "created_at")
    task_id = str(record.get("task_id") or "")
    expected_owner = str(record.get("expected_owner") or "")
    for msg in _iter_known_messages():
        if msg.get("from") != "manager":
            continue
        created_at = _ms(msg, "created_at")
        if created_at <= since_ms:
            continue
        to_agent = str(msg.get("to") or "")
        msg_task_id = str(msg.get("task_id") or "")
        if to_agent.startswith("worker") or (expected_owner and to_agent == expected_owner):
            detail = f"compensated delegate to {to_agent}"
            if msg_task_id:
                detail += f" ({msg_task_id})"
            return "delegate_compensated", detail, str(msg.get("local_id") or "")
        if task_id and msg_task_id == task_id:
            return "delegate_compensated", "compensated manager task action", str(msg.get("local_id") or "")

    for row in local_facts.list_logs("manager", limit=300):
        created_at = _ms(row, "created_at")
        if created_at <= since_ms:
            continue
        kind = str(row.get("type") or "")
        if kind == "say":
            return "boss_say_compensated", "compensated manager boss-visible say", str(row.get("local_id") or "")
    return None


def record_boss_read(row: dict, *, now_ms_fn: Callable[[], int] = now_ms) -> dict | None:
    """Track a boss→manager row after manager read it."""
    if not enabled():
        return None
    if row.get("to") != "manager" or row.get("from") != "user":
        return None
    local_id = str(row.get("local_id") or "")
    content = str(row.get("content") or "").strip()
    if not local_id or not content:
        return None
    route, owner = classify_content(content)
    now = now_ms_fn()
    created_at = _ms(row, "created_at") or now
    read_at = _ms(row, "read_at") or now
    task_id = str(row.get("task_id") or "")
    record = {
        "local_id": local_id,
        "content": content,
        "created_at": created_at,
        "read_at": read_at,
        "task_id": task_id,
        "route_hint": route,
        "expected_owner": owner,
        "closed_at": None,
        "closed_by": "",
        "closure_kind": "",
        "detail": "",
        "closure_ref": "",
        "last_alert_at": 0,
        "alert_count": 0,
    }
    with _locked():
        data = _load_state()
        rows = data.setdefault("records", [])
        for existing in rows:
            if existing.get("local_id") != local_id:
                continue
            if existing.get("closed_at"):
                return dict(existing)
            existing.update({
                "content": content,
                "created_at": created_at,
                "read_at": read_at,
                "task_id": task_id,
                "route_hint": route,
                "expected_owner": owner,
            })
            _save_state(data)
            return dict(existing)
        rows.append(record)
        _save_state(data)
    return record


def close_latest(kind: str, detail: str, *, closed_by: str = "manager",
                 ref: str = "", task_id: str = "",
                 expected_owner: str = "", local_id: str = "",
                 now_ms_fn: Callable[[], int] = now_ms) -> dict | None:
    if not enabled():
        return None
    now = now_ms_fn()
    with _locked():
        data = _load_state()
        rows = _open_records(data)
        if not rows:
            return None
        target = _pick_open_record(
            rows,
            expected_owner=expected_owner,
            task_id=task_id,
            local_id=local_id,
        )
        if target is None:
            return None
        target.update({
            "closed_at": now,
            "closed_by": closed_by,
            "closure_kind": kind,
            "detail": str(detail or ""),
            "closure_ref": str(ref or ""),
        })
        _save_state(data)
        return dict(target)


def mark_delegate(to_agent: str, message: str, *, task_id: str = "",
                  ref: str = "", now_ms_fn: Callable[[], int] = now_ms) -> dict | None:
    detail = f"delegated to {to_agent}"
    if task_id:
        detail += f" ({task_id})"
    if message:
        detail += f": {_clip(message, 120)}"
    return close_latest(
        "delegate", detail, closed_by=f"manager->{to_agent}",
        ref=ref, task_id=task_id, expected_owner=to_agent, now_ms_fn=now_ms_fn)


def mark_boss_say(message: str, *, image: str = "", ref: str = "",
                  now_ms_fn: Callable[[], int] = now_ms) -> dict | None:
    detail = _clip(message or "", 140)
    if image:
        detail = f"{detail} [image={image}]" if detail else f"[image={image}]"
    return close_latest(
        "boss_say", detail, closed_by="manager->user",
        ref=ref, now_ms_fn=now_ms_fn)


def observe_public_manager_reply(message: str, *, ref: str = "",
                                 now_ms_fn: Callable[[], int] = now_ms) -> dict | None:
    """Best-effort closure when router sees manager's public bot card.

    Some boss-visible manager replies are successfully posted to Feishu but do
    not flow through `claudeteam say`, so `mark_boss_say()` never runs and the
    manager-action guard keeps alerting "已读未闭环". When the Feishu router sees
    a manager-authored card come back from the group, treat it as observed
    public closure for the latest open boss message.

    This is intentionally conservative:
    - only used for manager's own public card path;
    - does nothing if there is no open record;
    - reuses `boss_say` closure kind so downstream tooling keeps the same
      semantics as an explicit `claudeteam say`.
    """
    return mark_boss_say(message, ref=ref, now_ms_fn=now_ms_fn)


def _route_text(route_hint: str, expected_owner: str) -> str:
    labels = {
        "visual": "视觉/图片类，优先派视觉专岗",
        "design": "设计稿/UI 还原类，优先派设计或前端专岗",
        "browser": "浏览器/平台操作类，优先派运营或浏览器专岗",
        "code": "代码/UI/联调类，优先派工程专岗",
        "research": "研究/导师/资料类，优先派研究或学习专岗",
        "general": "通用任务，按团队职责选择专岗或直接短答",
    }
    label = labels.get(route_hint, "通用任务，按团队职责选择专岗或直接短答")
    if expected_owner:
        return f"{label}：建议 owner={expected_owner}"
    return f"{label}：当前团队未配置明确 owner，主管必须自己选一个真实 owner"


def _build_notice(record: dict, now: int, public_overdue_ms: int) -> ActionNotice:
    ct = team_command.safe_cli_cmd(ensure=True)
    local_id = str(record.get("local_id") or "?")
    content = str(record.get("content") or "")
    route_hint = str(record.get("route_hint") or "general")
    expected_owner = str(record.get("expected_owner") or "")
    read_at = _ms(record, "read_at")
    age_ms = max(0, now - read_at)
    route_line = _route_text(route_hint, expected_owner)
    owner_hint = expected_owner or "<按团队职责选择 worker>"
    snippet = _clip(content, 180)
    body = (
        f"⏱ manager_action_guard 发现老板消息已读但未闭环：{local_id}\n"
        f"已读时间：{fmt_time_ms(read_at)}；已过去：{_format_age(age_ms)}\n"
        f"老板原话：{snippet}\n"
        f"系统判断：{route_line}\n\n"
        "当前缺口：没有记录到 manager 回群、派工或真实 blocker。\n"
        "manager 现在必须三选一：\n"
        f"1. 预计超过 1 分钟的执行，立即派专岗：`{ct} send {owner_hint} manager \"目标/已知事实/边界/本轮 artifact/3 分钟内回产物或 blocker\" 高`。\n"
        f"2. 能短答则用 stdin 安全模式回群：`{ct} say manager - --to user`。\n"
        "3. 不能推进则回真实 blocker：缺什么权限/登录/素材/API/模型额度，下一步谁能解除。\n\n"
        "禁止继续由 manager 自己长时间做图、跑浏览器、改代码、写长报告后再沉默。"
    )
    public_title = ""
    public_body = ""
    if public_overdue_ms <= 0 or age_ms >= public_overdue_ms:
        public_title = "需要主管确认：老板消息已读后未闭环"
        public_body = (
            f"系统发现你的一条消息被主管接手后，超过 {_format_age(age_ms)} "
            "还没有记录到派工、回群或真实卡点。\n"
            f"消息摘要：{snippet}\n"
            "已自动催 manager：要么派给专岗，要么回真实进展/卡点。"
            "你不用重复解释；如果需要授权、登录、付款或方向取舍，主管必须单独说清楚。"
        )
    return ActionNotice(
        local_id=local_id,
        content=content,
        age_ms=age_ms,
        route_hint=route_hint,
        expected_owner=expected_owner,
        body=body,
        public_title=public_title,
        public_body=public_body,
    )


def _public_ready(*, age_ms: int, public_overdue_ms: int,
                  now: int, first_alert_at: int, grace_ms: int) -> bool:
    if public_overdue_ms > 0 and age_ms < public_overdue_ms:
        return False
    if grace_ms > 0:
        if not first_alert_at:
            return False
        if now - first_alert_at < grace_ms:
            return False
    return True


def sweep(*, now_ms_fn: Callable[[], int] = now_ms,
          overdue_s: int | None = None,
          repeat_s: int | None = None,
          public_overdue_s: int | None = None,
          max_age_s: int | None = None) -> list[ActionNotice]:
    if not enabled():
        return []
    now = now_ms_fn()
    overdue_ms = int((
        overdue_s if overdue_s is not None
        else tunables.tunable("manager_action_guard.overdue_s", 180)
    ) * 1000)
    repeat_ms = int((
        repeat_s if repeat_s is not None
        else tunables.tunable("manager_action_guard.repeat_s", 300)
    ) * 1000)
    public_overdue_ms = int((
        public_overdue_s if public_overdue_s is not None
        else tunables.tunable("manager_action_guard.public_overdue_s", 300)
    ) * 1000)
    public_after_alert_ms = int((
        tunables.tunable("manager_action_guard.public_after_manager_alert_s", 0)
    ) * 1000)
    max_age_ms = int((
        max_age_s if max_age_s is not None
        else tunables.tunable("manager_action_guard.max_age_s", 21600)
    ) * 1000)
    notices: list[ActionNotice] = []
    with _locked():
        data = _load_state()
        for record in _open_records(data):
            read_at = _ms(record, "read_at")
            created_at = _ms(record, "created_at")
            compensated = _compensating_action(record)
            if compensated is not None:
                kind, detail, ref = compensated
                record.update({
                    "closed_at": now,
                    "closed_by": "manager_action_guard",
                    "closure_kind": kind,
                    "detail": detail,
                    "closure_ref": ref,
                })
                continue
            if now - read_at < overdue_ms:
                continue
            if (max_age_ms > 0
                    and now - created_at > max_age_ms
                    and int(record.get("alert_count") or 0) == 0):
                continue
            last_alert_at = int(record.get("last_alert_at") or 0)
            if last_alert_at and now - last_alert_at < repeat_ms:
                continue
            first_alert_at = int(record.get("first_alert_at") or 0)
            seeded_first_alert_at = first_alert_at or now
            age_ms = max(0, now - read_at)
            gated_public_overdue_ms = public_overdue_ms if _public_ready(
                age_ms=age_ms,
                public_overdue_ms=public_overdue_ms,
                now=now,
                first_alert_at=seeded_first_alert_at,
                grace_ms=public_after_alert_ms,
            ) else 10**18
            notice = _build_notice(record, now, gated_public_overdue_ms)
            record["last_alert_at"] = now
            record["first_alert_at"] = seeded_first_alert_at
            record["alert_count"] = int(record.get("alert_count") or 0) + 1
            notices.append(notice)
        _save_state(data)
    return notices


def list_records(*, include_closed: bool = True) -> list[dict]:
    rows = list(_load_state().get("records", []))
    if not include_closed:
        rows = [r for r in rows if not r.get("closed_at")]
    return rows
