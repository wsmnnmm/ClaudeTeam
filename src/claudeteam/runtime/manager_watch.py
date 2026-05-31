"""Overdue-task monitor for manager dispatches.

The manager identity says "派工后要巡视", but identity text is still an
LLM instruction. This module gives the watchdog a small runtime backstop:
if a manager-created worker task has no recorded worker signal for the
configured window, notify manager's inbox/pane and optionally the chat.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claudeteam.agents import adapter_for_agent
from claudeteam.feishu import pane_state as live_pane_state
from claudeteam.runtime import (
    config, first_output_gate, manager_action_guard, paths, team_command, tmux,
    tunables, wake,
)
from claudeteam.store import local_facts, tasks
from claudeteam.util import flock, fmt_time_ms, now_ms, read_json, write_json


@dataclass(frozen=True)
class OverdueNotice:
    task_id: str
    assignee: str
    title: str
    body: str
    public_title: str = ""
    public_body: str = ""


def _state_file() -> Path:
    return paths.state_file("manager-watch.json")


def _locked():
    return flock(_state_file().with_suffix(".lock"))


def _load_state() -> dict:
    return read_json(_state_file(), {"alerts": {}})


def _save_state(data: dict) -> None:
    write_json(_state_file(), data)


_STANDBY_MARKERS = ("保持待命", "先待命", "进入待命", "待命即可")
_NO_OUTPUT_MARKERS = (
    "不需要再补产物", "不需要补产物", "无需再补产物", "无需补产物",
    "不需要再补", "无需回执", "不需要回执", "不需要再回执",
    "不用再补", "不用补产物", "不追加扩写", "不继续扩写",
    "已由我收口", "验收通过",
)


def _standby_or_no_output_task(task: dict) -> bool:
    text = " ".join(str(task.get(k) or "") for k in ("title", "description"))
    if not text.strip():
        return False
    return (any(marker in text for marker in _STANDBY_MARKERS)
            and any(marker in text for marker in _NO_OUTPUT_MARKERS))


def _managed_worker_task(task: dict) -> bool:
    assignee = str(task.get("assignee") or "")
    if not assignee or assignee == "manager":
        return False
    try:
        if assignee not in set(config.agent_names()):
            return False
    except Exception:
        return False
    if task.get("status") not in {tasks.DEFAULT_STATUS, "进行中"}:
        return False
    if _standby_or_no_output_task(task):
        return False
    return str(task.get("creator") or "") == "manager"


def _task_ms(task: dict, key: str) -> int:
    try:
        return int(task.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _latest_worker_signal_ms(task: dict) -> int:
    """Best-effort latest signal that the worker acknowledged/progressed.

    A "signal" is intentionally about work movement: a task-card update,
    a worker reading the task inbox row, a worker→manager inbox message for
    this task, or a worker say/log. Heartbeats are deliberately excluded.
    Commands such as `inbox` and health checks touch heartbeats while merely
    inspecting the system; using them here would hide stalled work behind
    operator/manager pokes.
    """
    assignee = str(task.get("assignee") or "")
    task_id = str(task.get("id") or "")
    created = _task_ms(task, "created_at")
    latest = max(created, _task_ms(task, "updated_at"))

    for msg in local_facts.list_messages(assignee, unread_only=False):
        if str(msg.get("task_id") or "") != task_id:
            continue
        if _task_ms(msg, "created_at") <= created:
            continue
        read_at = _task_ms(msg, "read_at")
        if read_at > created:
            latest = max(latest, read_at)

    for msg in local_facts.list_messages("manager", unread_only=False):
        if msg.get("from") != assignee:
            continue
        if _task_ms(msg, "created_at") <= created:
            continue
        msg_task_id = str(msg.get("task_id") or "")
        if msg_task_id and msg_task_id != task_id:
            continue
        latest = max(latest, _task_ms(msg, "created_at"))

    for row in local_facts.list_logs(assignee, limit=200):
        if _task_ms(row, "created_at") > created:
            latest = max(latest, _task_ms(row, "created_at"))

    return latest


def _latest_worker_output(task: dict) -> tuple[int, str]:
    """Latest valid worker output timestamp plus latest invalid reason."""
    assignee = str(task.get("assignee") or "")
    task_id = str(task.get("id") or "")
    created = _task_ms(task, "created_at")
    latest = 0
    latest_invalid_at = 0
    latest_reason = "无证据"

    artifact_path = str(task.get("artifact_path") or "").strip()
    if artifact_path:
        artifact_check = first_output_gate.check_reference(artifact_path, task)
        if artifact_check.valid:
            latest = max(latest, _task_ms(task, "updated_at"), created, 1)
        else:
            latest_reason = artifact_check.reason

    status = str(task.get("status") or "")
    if status in {"待验收", "已完成"} and latest:
        latest = max(latest, _task_ms(task, "updated_at"), created, 1)

    for msg in local_facts.list_messages("manager", unread_only=False):
        if msg.get("from") != assignee:
            continue
        created_at = _task_ms(msg, "created_at")
        if created_at <= created:
            continue
        msg_task_id = str(msg.get("task_id") or "")
        if msg_task_id and msg_task_id != task_id:
            continue
        check = first_output_gate.check(task, msg)
        if check.valid:
            latest = max(latest, created_at)
        elif created_at >= latest_invalid_at:
            latest_invalid_at = created_at
            latest_reason = check.reason

    for row in local_facts.list_logs(assignee, limit=200):
        created_at = _task_ms(row, "created_at")
        if created_at <= created:
            continue
        check = first_output_gate.check(task, {
            "content": row.get("content") or "",
            "artifact": "",
        })
        if check.valid:
            latest = max(latest, created_at)
        elif created_at >= latest_invalid_at:
            latest_invalid_at = created_at
            latest_reason = check.reason

    return latest, latest_reason


def _latest_worker_output_ms(task: dict) -> int:
    return _latest_worker_output(task)[0]


def _unread_task_message_count(task: dict) -> int:
    assignee = str(task.get("assignee") or "")
    task_id = str(task.get("id") or "")
    count = 0
    for msg in local_facts.list_messages(assignee, unread_only=True):
        if str(msg.get("task_id") or "") == task_id:
            count += 1
    return count


def _pane_state(agent: str) -> str:
    try:
        target = tmux.Target(config.session_name(), agent)
        if not tmux.has_window(target):
            return "pane missing"
        adapter = adapter_for_agent(agent)
        if wake.is_rate_limited(target, adapter):
            return "rate-limited"
        text = tmux.capture_pane(target, lines=120)
        emoji, brief = live_pane_state.parse(text)
        if emoji == "⚠️":
            return brief
        if emoji == "🔄":
            return "thinking"
        if wake.is_ready(target, adapter):
            return "ready"
        if brief == "idle":
            return "not at ready prompt"
        return brief
    except Exception as e:
        return f"pane check failed: {e}"


def _fingerprint(task: dict, signal_ms: int) -> str:
    return "|".join([
        str(task.get("id") or ""),
        str(task.get("status") or ""),
        str(task.get("artifact_path") or ""),
        str(_task_ms(task, "updated_at")),
        str(signal_ms),
    ])


def _format_age(ms: int) -> str:
    minutes = max(1, round(ms / 60000))
    return f"{minutes} 分钟"


def _public_ready(*, age_ms: int, public_overdue_ms: int,
                  now: int, first_alert_at: int, grace_ms: int) -> bool:
    """Gate boss-visible escalation behind task age plus manager grace.

    Teams that do not configure the grace tunable keep the legacy behavior:
    public escalation depends only on task age.
    """
    if public_overdue_ms > 0 and age_ms < public_overdue_ms:
        return False
    if grace_ms > 0:
        if not first_alert_at:
            return False
        if now - first_alert_at < grace_ms:
            return False
    return True


def _clip(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _msg_ms(msg: dict, key: str) -> int:
    try:
        return int(msg.get(key) or 0)
    except (TypeError, ValueError):
        return 0


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


def _after_window(created_at: int, *, since_ms: int, now: int) -> bool:
    return since_ms < created_at <= now


def _manager_task_progress_after(task_id: str, since_ms: int, now: int) -> bool:
    if not task_id:
        return False
    for msg in _iter_known_messages():
        if msg.get("from") != "manager":
            continue
        if not _after_window(_msg_ms(msg, "created_at"), since_ms=since_ms, now=now):
            continue
        if str(msg.get("task_id") or "") == task_id:
            return True
        if task_id in str(msg.get("content") or ""):
            return True
    for row in local_facts.list_logs("manager", limit=300):
        if not _after_window(_msg_ms(row, "created_at"), since_ms=since_ms, now=now):
            continue
        if str(row.get("type") or "") == "first_output_feedback":
            continue
        if str(row.get("ref") or "") == task_id:
            return True
        if task_id in str(row.get("content") or ""):
            return True
    return False


def _first_output_feedback_after(task_id: str, since_ms: int, now: int) -> bool:
    if not task_id:
        return False
    for row in local_facts.list_logs("manager", limit=300):
        if str(row.get("type") or "") != "first_output_feedback":
            continue
        if not _after_window(_msg_ms(row, "created_at"), since_ms=since_ms, now=now):
            continue
        if str(row.get("ref") or "") == task_id:
            return True
        if task_id in str(row.get("content") or ""):
            return True
    return False


def _worker_evidence_after(task: dict, since_ms: int, pane_state: str, now: int) -> bool:
    if pane_state == "thinking":
        return True
    task_id = str(task.get("id") or "")
    assignee = str(task.get("assignee") or "")
    if _latest_worker_output_ms(task) > since_ms:
        return True
    for msg in local_facts.list_messages("manager", unread_only=False):
        if msg.get("from") != assignee:
            continue
        if not _after_window(_msg_ms(msg, "created_at"), since_ms=since_ms, now=now):
            continue
        msg_task_id = str(msg.get("task_id") or "")
        if not msg_task_id or msg_task_id == task_id:
            return True
    for row in local_facts.list_logs(assignee, limit=300):
        if not _after_window(_msg_ms(row, "created_at"), since_ms=since_ms, now=now):
            continue
        if str(row.get("ref") or "") == task_id:
            return True
        if task_id and task_id in str(row.get("content") or ""):
            return True
    return False


def _boss_visible_task_alert_allowed(task: dict, *, since_ms: int,
                                     pane_state: str, now: int) -> bool:
    """Escalate to the boss only when the private loop is truly silent.

    The manager inbox/pane notice stays strict. The public Feishu card is
    intentionally quieter: if manager has progressed, worker has produced any
    fresh task evidence, or manager has explicitly logged a misfire/feedback,
    keep the pressure internal.
    """
    task_id = str(task.get("id") or "")
    if _manager_task_progress_after(task_id, since_ms, now):
        return False
    if _worker_evidence_after(task, since_ms, pane_state, now):
        return False
    if _first_output_feedback_after(task_id, since_ms, now):
        return False
    return True


def _manager_progress_after(since_ms: int, pane_state: str, now: int) -> bool:
    if pane_state == "thinking":
        return True
    for msg in _iter_known_messages():
        if msg.get("from") != "manager":
            continue
        if _after_window(_msg_ms(msg, "created_at"), since_ms=since_ms, now=now):
            return True
    for row in local_facts.list_logs("manager", limit=300):
        if str(row.get("type") or "") == "first_output_feedback":
            continue
        if _after_window(_msg_ms(row, "created_at"), since_ms=since_ms, now=now):
            return True
    return False


def _agent_public_label(agent: str) -> str:
    try:
        role = str(config.agent_config(agent).get("role") or "").strip()
    except Exception:
        role = ""
    return role or "执行同学"


def _should_public_alert(task: dict, age_ms: int, pane_state: str,
                         public_overdue_ms: int) -> bool:
    """True when an overdue task deserves a boss-visible chat card.

    Manager inbox nudges are cheap and private. Group cards are expensive
    attention, so do not publish ordinary review backlog or tasks that
    already have an artifact and only need manager收口.
    """
    if public_overdue_ms > 0 and age_ms < public_overdue_ms:
        return False
    has_artifact = bool(str(task.get("artifact_path") or "").strip())
    status = str(task.get("status") or "")
    if status == "待验收" and has_artifact:
        return False
    if has_artifact and pane_state == "ready":
        return False
    return True


def _build_public_body(task: dict, age_ms: int, pane_state: str,
                       *, escalated_after_manager_prompt: bool = False) -> str:
    task_id = str(task.get("id") or "?")
    title = _clip(str(task.get("title") or "").strip() or "未命名任务", 140)
    assignee = str(task.get("assignee") or "")
    status = str(task.get("status") or "进行中")
    owner = _agent_public_label(assignee)

    if pane_state != "ready":
        current = "执行侧可能没有正常接住任务，主管需要先恢复现场或改派。"
    elif str(task.get("artifact_path") or ""):
        current = "已有交付线索，但任务账本还没有完成主管验收和收口。"
    else:
        current = "还没有形成可对外验收的证据，主管需要追齐证据、打回重做或调整方案。"

    return (
        f"系统发现一项团队任务超过 {_format_age(age_ms)} 没有形成可验收收口。\n"
        f"任务编号：{task_id}\n"
        f"任务：{title}\n"
        f"负责人：{owner}\n"
        f"当前判断：{status}；{current}\n\n"
        + ("当前升级原因：主管在首次提醒后仍未完成动作收口。\n"
           if escalated_after_manager_prompt else "")
        + "系统已先给主管固定三选一动作：自动重试 / 转派他人 / 标记阻塞。\n"
        "老板动作：先不用处理内部命令。需要你出手时，主管必须单独说清楚是授权、登录、预算还是方向取舍。\n"
        "主管下一步：立即核验现场，并给出“已完成 / 卡住原因 / 改派方案 / 下次回报时间”的人话结论。"
    )


def _build_notice(task: dict, now: int, signal_ms: int,
                  pane_state_fn: Callable[[str], str],
                  public_enabled: bool,
                  public_grace_applied: bool,
                  public_since_ms: int = 0) -> OverdueNotice:
    ct = team_command.safe_cli_cmd(ensure=True)
    task_id = str(task.get("id") or "?")
    assignee = str(task.get("assignee") or "?")
    title = str(task.get("title") or "").strip() or "untitled task"
    age_ms = max(0, now - signal_ms)
    unread_count = _unread_task_message_count(task)
    pane_state = pane_state_fn(assignee)

    reasons = [f"超过 {_format_age(age_ms)} 没有记录到 {assignee} 的新信号"]
    if unread_count:
        reasons.append(f"{assignee} 还有 {unread_count} 条该任务 inbox 未读")
    if pane_state != "ready":
        reasons.append(f"{assignee} pane 状态：{pane_state}")
    if not str(task.get("artifact_path") or ""):
        reasons.append("任务卡尚无 artifact")

    body = (
        f"⏱ manager_watch 发现派工超时：{task_id} → {assignee}\n"
        f"任务：{title}\n"
        f"状态：{task.get('status') or '?'}；创建：{fmt_time_ms(_task_ms(task, 'created_at'))}；"
        f"最近信号：{fmt_time_ms(signal_ms)}\n"
        f"原因：{'；'.join(reasons)}\n\n"
        "manager 固定三选一动作：\n"
        f"1. 自动重试：先 `{ct} peek {assignee} 100` 看现场，再把任务重置/重派给原负责人。\n"
        f"2. 转派他人：如果原负责人没接住，立即改派，并用 `{ct} send <新负责人> manager \"接手 {task_id}：目标/边界/3 分钟内回首产物或 blocker\" 高 --task-id {task_id}`。\n"
        "3. 标记阻塞：如果卡在授权/登录/预算/方向取舍，立刻把任务转 blocked，并给老板回“卡住原因 / 已尝试 / 需要谁 / 下次回报时间”。"
    )
    public_body = ""
    if (public_enabled
            and _should_public_alert(task, age_ms, pane_state, 0)
            and _boss_visible_task_alert_allowed(
                task, since_ms=public_since_ms, pane_state=pane_state, now=now)):
        public_body = _build_public_body(
            task, age_ms, pane_state,
            escalated_after_manager_prompt=public_grace_applied,
        )
    return OverdueNotice(
        task_id=task_id,
        assignee=assignee,
        title=f"⏱ {task_id} 派工超时：{assignee}",
        body=body,
        public_title=(f"需要主管确认：{task_id} 长时间未收口"
                      if public_body else ""),
        public_body=public_body,
    )


def _build_first_output_notice(task: dict, now: int,
                               pane_state_fn: Callable[[str], str],
                               public_enabled: bool,
                               public_grace_applied: bool,
                               failure_reason: str = "无证据",
                               public_since_ms: int = 0) -> OverdueNotice:
    ct = team_command.safe_cli_cmd(ensure=True)
    task_id = str(task.get("id") or "?")
    assignee = str(task.get("assignee") or "?")
    title = str(task.get("title") or "").strip() or "untitled task"
    created = _task_ms(task, "created_at")
    age_ms = max(0, now - created)
    unread_count = _unread_task_message_count(task)
    pane_state = pane_state_fn(assignee)

    reasons = [
        f"派工后超过 {_format_age(age_ms)} 没有记录到 {assignee} 的可验证首产物/真实 blocker",
        f"当前判定：{failure_reason}",
    ]
    if unread_count:
        reasons.append(f"{assignee} 还有 {unread_count} 条该任务 inbox 未读")
    if pane_state != "ready":
        reasons.append(f"{assignee} pane 状态：{pane_state}")

    body = (
        f"⏱ first_output_watch 发现派工后无首产物：{task_id} → {assignee}\n"
        f"任务：{title}\n"
        f"状态：{task.get('status') or '?'}；创建：{fmt_time_ms(created)}\n"
        f"原因：{'；'.join(reasons)}\n\n"
        "manager 固定三选一动作：\n"
        f"1. 自动重试：`{ct} peek {assignee} 100` 看现场，再催原负责人 3 分钟内回首产物或 blocker。\n"
        f"2. 转派他人：如果原负责人没接住或方向不对，立即改派，并把 {task_id} 的目标/证据门禁同步给新负责人。\n"
        "3. 标记阻塞：如果需要老板动作，回群说清授权、登录、预算或方向取舍，并把任务转 blocked。\n"
        f"4. 反馈通道：误报/漏报/证据不符可记日志：`{ct} log manager first_output_feedback \"task_id={task_id} kind=误报|漏报|证据不符 note=<原因>\" {task_id}`。"
    )

    public_title = ""
    public_body = ""
    if (public_enabled
            and _boss_visible_task_alert_allowed(
                task, since_ms=public_since_ms, pane_state=pane_state, now=now)):
        owner = _agent_public_label(assignee)
        public_title = f"需要主管确认：{task_id} 派工后无首产物"
        public_body = (
            f"系统发现一项任务派给执行侧超过 {_format_age(age_ms)}，还没有形成可验证首产物或真实 blocker。\n"
            f"任务编号：{task_id}\n"
            f"任务：{_clip(title, 140)}\n"
            f"负责人：{owner}\n"
            f"当前判定：{failure_reason}。\n"
            + ("当前升级原因：主管在首次提醒后仍未完成动作收口。\n" if public_grace_applied else "")
            + "当前动作：已先给主管固定三选一动作：自动重试 / 转派他人 / 标记阻塞，并要求执行侧给出可用 artifact、链接、文件、截图、摘要证据，或具体卡点、已尝试动作、需要谁和下次回报时间。\n\n"
            "老板动作：先不用重复追问；如果需要你授权、登录、预算或方向取舍，主管必须单独说清楚。"
        )

    return OverdueNotice(
        task_id=task_id,
        assignee=assignee,
        title=f"⏱ {task_id} 派工后无首产物：{assignee}",
        body=body,
        public_title=public_title,
        public_body=public_body,
    )


def _inject_manager(body: str) -> None:
    try:
        target = tmux.Target(config.session_name(), "manager")
        if not tmux.has_window(target):
            return
        adapter = adapter_for_agent("manager")
        tmux.inject(target, body, submit_keys=adapter.submit_keys())
    except Exception:
        pass


def _notify_manager(notice: OverdueNotice,
                    inject_manager_fn: Callable[[str], None] | None) -> None:
    local_id = local_facts.append_message(
        "manager", "manager_watch", notice.body,
        priority="高", task_id=notice.task_id,
    )
    kind = "first_output_watch" if "first_output_watch" in notice.body else "overdue_task"
    local_facts.append_log("manager_watch", kind, notice.body, ref=local_id)
    (inject_manager_fn or _inject_manager)(notice.body)


def _is_unread_boss_message(msg: dict) -> bool:
    """True for a live boss→manager inbox row.

    Router fast-ack makes these messages visible to the boss immediately,
    but a pane-submit failure or manager distraction can leave the real
    inbox row unread. This filter intentionally ignores manager_watch's own
    private nudges so the fallback does not chase itself.
    """
    if msg.get("read"):
        return False
    if msg.get("to") != "manager":
        return False
    if msg.get("from") != "user":
        return False
    return bool(str(msg.get("content") or "").strip())


def _build_boss_inbox_notice(msg: dict, now: int,
                             public_overdue_ms: int,
                             pane_state: str,
                             public_since_ms: int = 0) -> OverdueNotice:
    ct = team_command.safe_cli_cmd(ensure=True)
    local_id = str(msg.get("local_id") or "?")
    age_ms = max(0, now - _msg_ms(msg, "created_at"))
    snippet = _clip(str(msg.get("content") or ""), 180)
    pane_line = f"当前 pane 状态：{pane_state}\n" if pane_state and pane_state != "ready" else ""

    body = (
        f"⏱ boss_inbox_watch 发现老板消息未收口：{local_id}\n"
        f"已经过去：{_format_age(age_ms)}\n"
        f"老板原话：{snippet}\n\n"
        f"{pane_line}"
        "manager 现在要做：\n"
        "1. 先处理这条老板消息，不要被旧 worker 回执或内部任务抢占。\n"
        "2. 如果输入区已经有这条消息但没有开始执行，先按 Enter 或重新注入后立刻执行。\n"
        "3. 先做一个最小真实动作：查证、跑命令、看产物、派给明确 owner，或给出真实 blocker。\n"
        "4. 回群用 stdin 安全模式，避免反引号/引号/URL 被 shell 改写：\n"
        f"cat <<'EOF' | {ct} say manager - --to user\n"
        "<给老板的回复>\n"
        "EOF\n"
        f"然后再执行 `{ct} read {local_id}`。\n"
        "5. 禁止只说“收到/稍后汇总”就销账。"
    )
    public_title = ""
    public_body = ""
    public_ready = public_overdue_ms <= 0 or age_ms >= public_overdue_ms
    if public_ready and not _manager_progress_after(public_since_ms, pane_state, now):
        if pane_state == "thinking":
            current = "主管当前仍在思考/跑命令，但还没有形成老板可读结论。"
        elif pane_state == "ready":
            current = "主管看起来空闲，但还没有把结论回到群里。"
        else:
            current = f"主管现场状态异常：{pane_state}。"
        public_title = "需要主管确认：老板消息长时间未收口"
        public_body = (
            f"系统发现你的一条消息超过 {_format_age(age_ms)} 还没有形成主管后续。\n"
            f"消息摘要：{snippet}\n"
            f"当前动作：{current} 已自动重投给 manager，并要求先给真实进展、卡点或下一步。\n"
            "你不用重复解释；如果需要你授权、登录、付款或方向取舍，主管必须单独说清楚。"
        )
    return OverdueNotice(
        task_id=local_id,
        assignee="manager",
        title=f"⏱ 老板消息未收口：{local_id}",
        body=body,
        public_title=public_title,
        public_body=public_body,
    )


def _build_c4_escalation_notice(rows: list[dict], now: int,
                                 pane_state: str) -> OverdueNotice:
    """Build a single escalation notice when 3+ unread boss messages pile up.

    C4 wake-loop deadlock: the operator keeps sending wake messages but manager
    is stuck/crashed/overwhelmed. Another inbox nudge won't help — escalate
    directly to the boss with recovery instructions instead of injecting the
    manager pane yet again.
    """
    ct = team_command.safe_cli_cmd(ensure=True)
    count = len(rows)
    oldest = min((_msg_ms(m, "created_at") for m in rows), default=now)
    oldest_age_ms = max(0, now - oldest)
    snippet = _clip(str(rows[0].get("content") or ""), 180)
    pane_line = f"当前 pane 状态：{pane_state}\n" if pane_state and pane_state != "ready" else ""

    body = (
        f"⏱ C4 唤醒升级：manager 已积累 {count} 条未读老板消息\n"
        f"最早一条已过去：{_format_age(oldest_age_ms)}\n"
        f"最新老板消息：{snippet}\n\n"
        f"{pane_line}"
        "⚠️ manager 可能已宕机/卡死/被限流，继续注入 pane 无意义。\n\n"
        "建议恢复动作（按顺序尝试）：\n"
        f"1. `{ct} health` 查看 manager pane 状态\n"
        f"2. 如果 pane 丢失或卡死：`{ct} restart manager`\n"
        f"3. 如果有 rate-limit 标记：等待冷却后 `{ct} recycle manager`\n"
        f"4. 如果以上都无效：`{ct} down && {ct} up` 全队重启\n"
        "5. 恢复后先处理最早的老板消息，不要被 worker 回执抢占"
    )
    return OverdueNotice(
        task_id=f"C4-{count}",
        assignee="manager",
        title=f"⏱ C4 唤醒升级：manager {count} 条老板消息未读",
        body=body,
        public_title=f"⚠️ manager 可能已宕机：{count} 条老板消息未收口",
        public_body=(
            f"系统发现 manager 已积累 {count} 条未读老板消息（最早 "
            f"{_format_age(oldest_age_ms)}），pane 状态：{pane_state or '未知'}。\n"
            "已停止重复注入，等待操作员手动恢复。\n\n"
            f"建议：`claudeteam health` 检查 manager 状态，必要时 "
            f"`claudeteam restart manager` 或 `claudeteam down && claudeteam up`。"
        ),
    )


def sweep_boss_inbox(*, now_ms_fn: Callable[[], int] = now_ms,
                     pane_state_fn: Callable[[str], str] = _pane_state,
                     inject_manager_fn: Callable[[str], None] | None = None,
                     alert_fn: Callable[[OverdueNotice], None] | None = None,
                     overdue_s: int | None = None,
                     repeat_s: int | None = None,
                     public_overdue_s: int | None = None,
                     max_age_s: int | None = None) -> list[OverdueNotice]:
    """Backstop unread boss→manager rows after router fast-ack.

    The ordinary `sweep()` function catches manager→worker silence. This
    one catches the higher-stakes case the boss experiences as "I got an
    ack and then nothing": the original boss inbox row remains unread for
    too long.
    """
    now = now_ms_fn()
    overdue_ms = int((
        overdue_s if overdue_s is not None
        else tunables.tunable("manager_watch.boss_inbox_overdue_s", 300)
    ) * 1000)
    repeat_ms = int((
        repeat_s if repeat_s is not None
        else tunables.tunable("manager_watch.boss_inbox_repeat_s", 300)
    ) * 1000)
    public_overdue_ms = int((
        public_overdue_s if public_overdue_s is not None
        else tunables.tunable("manager_watch.boss_inbox_public_overdue_s", 600)
    ) * 1000)
    max_age_ms = int((
        max_age_s if max_age_s is not None
        else tunables.tunable("manager_watch.boss_inbox_max_age_s", 21600)
    ) * 1000)

    rows = [m for m in local_facts.list_messages("manager", unread_only=True)
            if _is_unread_boss_message(m)]
    open_ids = {str(m.get("local_id") or "") for m in rows}
    notices: list[OverdueNotice] = []

    # C4 wake-loop guard: when 3+ unread boss messages pile up, manager is
    # clearly unresponsive and another inbox nudge won't help. Escalate
    # directly to a public alert instead of injecting the pane again.
    c4_threshold = int(tunables.tunable("manager_watch.c4_wake_threshold", 3))
    c4_escalation = len(rows) >= c4_threshold

    with _locked():
        state = _load_state()
        alerts = state.setdefault("boss_inbox_alerts", {})

        if c4_escalation:
            # One escalation notice for the whole pile, not one per message.
            # Check repeat suppression using the count as fingerprint.
            prev_c4 = alerts.get("__c4__", {})
            last_c4_at = int(prev_c4.get("last_alert_at") or 0)
            if now - last_c4_at >= repeat_ms:
                notice = _build_c4_escalation_notice(rows, now, pane_state_fn("manager"))
                alerts["__c4__"] = {"last_alert_at": now, "count": len(rows)}
                notices.append(notice)
        else:
            for msg in rows:
                local_id = str(msg.get("local_id") or "")
                created = _msg_ms(msg, "created_at")
                if now - created < overdue_ms:
                    continue
                if max_age_ms > 0 and now - created > max_age_ms:
                    alerts.pop(local_id, None)
                    continue
                fp = "|".join([
                    local_id,
                    str(msg.get("read") or False),
                    str(created),
                    _clip(str(msg.get("content") or ""), 240),
                ])
                prev = alerts.get(local_id) or {}
                last_alert_at = int(prev.get("last_alert_at") or 0)
                if prev.get("fingerprint") == fp and now - last_alert_at < repeat_ms:
                    continue
                notice = _build_boss_inbox_notice(
                    msg, now, public_overdue_ms, pane_state_fn("manager"),
                    public_since_ms=last_alert_at or created)
                alerts[local_id] = {
                    "fingerprint": fp,
                    "last_alert_at": now,
                    "count": int(prev.get("count") or 0) + 1,
                }
                notices.append(notice)

        # Clean up stale entries (but keep __c4__ if still in escalation)
        if not c4_escalation:
            alerts.pop("__c4__", None)
        for local_id in list(alerts):
            if local_id == "__c4__":
                continue
            if local_id not in open_ids:
                alerts.pop(local_id, None)
        _save_state(state)

    for notice in notices:
        if c4_escalation:
            if alert_fn is not None:
                alert_fn(notice)
        else:
            _notify_manager(notice, inject_manager_fn)
            if alert_fn is not None:
                alert_fn(notice)
    return notices


def sweep_manager_actions(*,
                          now_ms_fn: Callable[[], int] = now_ms,
                          inject_manager_fn: Callable[[str], None] | None = None,
                          alert_fn: Callable[[OverdueNotice], None] | None = None,
                          overdue_s: int | None = None,
                          repeat_s: int | None = None,
                          public_overdue_s: int | None = None,
                          max_age_s: int | None = None) -> list[OverdueNotice]:
    """Backstop read boss messages that did not lead to an action.

    `sweep_boss_inbox` handles unread boss rows. This catches the subtler
    failure where manager already ran `read`, then got stuck doing the work
    personally without a boss reply, worker dispatch, or blocker.
    """
    action_notices = manager_action_guard.sweep(
        now_ms_fn=now_ms_fn,
        overdue_s=overdue_s,
        repeat_s=repeat_s,
        public_overdue_s=public_overdue_s,
        max_age_s=max_age_s,
    )
    notices = [
        OverdueNotice(
            task_id=notice.local_id,
            assignee="manager",
            title=f"⏱ 老板消息已读未闭环：{notice.local_id}",
            body=notice.body,
            public_title=notice.public_title,
            public_body=notice.public_body,
        )
        for notice in action_notices
    ]
    for notice in notices:
        _notify_manager(notice, inject_manager_fn)
        if alert_fn is not None:
            alert_fn(notice)
    return notices


def sweep(*, now_ms_fn: Callable[[], int] = now_ms,
          pane_state_fn: Callable[[str], str] = _pane_state,
          inject_manager_fn: Callable[[str], None] | None = None,
          alert_fn: Callable[[OverdueNotice], None] | None = None,
          overdue_s: int | None = None,
          repeat_s: int | None = None,
          max_task_age_s: int | None = None,
          public_overdue_s: int | None = None) -> list[OverdueNotice]:
    """Send manager overdue notices for stale manager→worker tasks.

    Returns the notices sent in this sweep. Duplicate notices for the same
    unchanged task are suppressed until `repeat_s` elapses.
    """
    now = now_ms_fn()
    overdue_ms = int((overdue_s if overdue_s is not None
                      else tunables.tunable("manager_watch.overdue_s", 600)) * 1000)
    repeat_ms = int((repeat_s if repeat_s is not None
                     else tunables.tunable("manager_watch.repeat_s", 900)) * 1000)
    public_overdue_ms = int((
        public_overdue_s if public_overdue_s is not None
        else tunables.tunable("manager_watch.public_overdue_s", 1800)
    ) * 1000)
    public_after_alert_ms = int((
        tunables.tunable("manager_watch.public_after_manager_alert_s", 0)
    ) * 1000)
    max_task_age_ms = int((
        max_task_age_s if max_task_age_s is not None
        else tunables.tunable("manager_watch.max_task_age_s", 21600)
    ) * 1000)

    rows = [t for t in tasks.list_tasks() if _managed_worker_task(t)]
    open_ids = {str(t.get("id") or "") for t in rows}
    notices: list[OverdueNotice] = []

    with _locked():
        state = _load_state()
        alerts = state.setdefault("alerts", {})

        for task in rows:
            signal_ms = _latest_worker_signal_ms(task)
            if now - signal_ms < overdue_ms:
                continue
            task_id = str(task.get("id") or "")
            if (max_task_age_ms > 0
                    and now - _task_ms(task, "created_at") > max_task_age_ms
                    and task_id not in alerts):
                continue
            fp = _fingerprint(task, signal_ms)
            prev = alerts.get(task_id) or {}
            first_alert_at = int(prev.get("first_alert_at") or 0)
            last_alert_at = int(prev.get("last_alert_at") or 0)
            if (prev.get("fingerprint") == fp
                    and first_alert_at
                    and _first_output_feedback_after(task_id, first_alert_at, now)):
                continue
            if prev.get("fingerprint") == fp and now - last_alert_at < repeat_ms:
                continue
            seeded_first_alert_at = first_alert_at or now
            public_enabled = _public_ready(
                age_ms=max(0, now - signal_ms),
                public_overdue_ms=public_overdue_ms,
                now=now,
                first_alert_at=seeded_first_alert_at,
                grace_ms=public_after_alert_ms,
            )
            notice = _build_notice(
                task, now, signal_ms, pane_state_fn,
                public_enabled,
                public_grace_applied=public_after_alert_ms > 0,
                public_since_ms=first_alert_at or seeded_first_alert_at,
            )
            count = int(prev.get("count") or 0) + 1
            alerts[task_id] = {
                "fingerprint": fp,
                "last_alert_at": now,
                "first_alert_at": seeded_first_alert_at,
                "count": count,
            }
            notices.append(notice)

        for task_id in list(alerts):
            if task_id not in open_ids:
                alerts.pop(task_id, None)
        _save_state(state)

    for notice in notices:
        _notify_manager(notice, inject_manager_fn)
        if alert_fn is not None:
            alert_fn(notice)
    return notices


def sweep_first_output(*,
                       now_ms_fn: Callable[[], int] = now_ms,
                       pane_state_fn: Callable[[str], str] = _pane_state,
                       inject_manager_fn: Callable[[str], None] | None = None,
                       alert_fn: Callable[[OverdueNotice], None] | None = None,
                       overdue_s: int | None = None,
                       repeat_s: int | None = None,
                       public_overdue_s: int | None = None,
                       max_task_age_s: int | None = None) -> list[OverdueNotice]:
    """Catch manager→worker dispatches that produced no first output."""
    if not bool(tunables.tunable("manager_watch.first_output_enabled", True)):
        return []
    now = now_ms_fn()
    overdue_ms = int((
        overdue_s if overdue_s is not None
        else tunables.tunable("manager_watch.first_output_overdue_s", 300)
    ) * 1000)
    repeat_ms = int((
        repeat_s if repeat_s is not None
        else tunables.tunable("manager_watch.first_output_repeat_s", 300)
    ) * 1000)
    public_overdue_ms = int((
        public_overdue_s if public_overdue_s is not None
        else tunables.tunable("manager_watch.first_output_public_overdue_s", 300)
    ) * 1000)
    public_after_alert_ms = int((
        tunables.tunable("manager_watch.first_output_public_after_manager_alert_s", 0)
    ) * 1000)
    max_task_age_ms = int((
        max_task_age_s if max_task_age_s is not None
        else tunables.tunable("manager_watch.first_output_max_task_age_s", 21600)
    ) * 1000)

    rows: list[tuple[dict, str]] = []
    for task in tasks.list_tasks():
        if not _managed_worker_task(task):
            continue
        output_ms, failure_reason = _latest_worker_output(task)
        if output_ms <= 0:
            rows.append((task, failure_reason))
    open_ids = {str(t.get("id") or "") for t, _ in rows}
    notices: list[OverdueNotice] = []

    with _locked():
        state = _load_state()
        alerts = state.setdefault("first_output_alerts", {})

        for task, failure_reason in rows:
            created = _task_ms(task, "created_at")
            if now - created < overdue_ms:
                continue
            task_id = str(task.get("id") or "")
            if (max_task_age_ms > 0
                    and now - created > max_task_age_ms
                    and task_id not in alerts):
                continue
            fp = "|".join([
                task_id,
                str(task.get("assignee") or ""),
                str(created),
                str(_task_ms(task, "updated_at")),
                failure_reason,
            ])
            prev = alerts.get(task_id) or {}
            first_alert_at = int(prev.get("first_alert_at") or 0)
            last_alert_at = int(prev.get("last_alert_at") or 0)
            if (prev.get("fingerprint") == fp
                    and first_alert_at
                    and _first_output_feedback_after(task_id, first_alert_at, now)):
                continue
            if prev.get("fingerprint") == fp and now - last_alert_at < repeat_ms:
                continue
            seeded_first_alert_at = first_alert_at or now
            public_enabled = _public_ready(
                age_ms=max(0, now - created),
                public_overdue_ms=public_overdue_ms,
                now=now,
                first_alert_at=seeded_first_alert_at,
                grace_ms=public_after_alert_ms,
            )
            notice = _build_first_output_notice(
                task, now, pane_state_fn,
                public_enabled,
                public_grace_applied=public_after_alert_ms > 0,
                failure_reason=failure_reason,
                public_since_ms=first_alert_at or seeded_first_alert_at)
            alerts[task_id] = {
                "fingerprint": fp,
                "last_alert_at": now,
                "first_alert_at": seeded_first_alert_at,
                "count": int(prev.get("count") or 0) + 1,
            }
            notices.append(notice)
            # L5 self-evolution: capture first_output failure as an incident
            try:
                from claudeteam.runtime import incident_learning
                incident_learning.capture(
                    incident_learning.from_first_output_gate(
                        str(task.get("assignee") or ""), task_id,
                        failure_reason,
                        detail=f"manager_watch sweep_first_output: {failure_reason}",
                    ))
            except Exception:
                pass

        for task_id in list(alerts):
            if task_id not in open_ids:
                alerts.pop(task_id, None)
        _save_state(state)

    for notice in notices:
        _notify_manager(notice, inject_manager_fn)
        if alert_fn is not None:
            alert_fn(notice)
    return notices
