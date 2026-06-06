"""Apply a router Decision: write inbox rows + (best-effort) inject panes.

Separated from `router.classify_event` so the routing decision stays a
pure function and the side-effecting "apply" step is the only place that
touches the store and tmux.

`apply` branches on `decision.action`:

  DROP       no-op (`DeliveryReport(skipped=True)`)
  SLASH      `_apply_slash`: dispatch via `feishu/slash.dispatch` →
             reply is `str` or `dict` (interactive cards). dict →
             `chat.send_card`, str → `chat.send_text`. Pane never
             touched, no LLM runs.
  BROADCAST  same as ROUTE but targets are all non-sender agents
  ROUTE      per-target: `_write_inbox` (always; flock-serialised) +
             `_inject_to_pane` (best-effort; skipped when `wake.is_rate_limited`
             returns True so the inbox row stays the canonical record).

Returns a `DeliveryReport` so callers can log / surface partial-success
without inspecting hand-rolled tuples. Lists in the report:
  written / injected / failed_inject / rate_limited (per agent),
  skipped (DROP), slash_reply (SLASH text-form replies only).
"""
from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass, field
from typing import Callable

from claudeteam.agents import adapter_for_agent as _default_adapter_for_agent
from claudeteam.agents import identity as _identity
from claudeteam.feishu import chat as _chat
from claudeteam.feishu import first_response as _first_response
from claudeteam.feishu import slash as _slash
from claudeteam.feishu.router import Action, Decision
from claudeteam.runtime import config, team_command, tmux, tunables, wake
from claudeteam.runtime.lifecycle import lazy_spawn_cmd
from claudeteam.store import local_facts, topics
from claudeteam.util import current_time_line


@dataclass
class DeliveryReport:
    written: list[str] = field(default_factory=list)        # inbox row landed
    injected: list[str] = field(default_factory=list)       # pane received text
    failed_inject: list[str] = field(default_factory=list)
    rate_limited: list[str] = field(default_factory=list)   # inbox kept, inject skipped
    skipped: bool = False                                    # True iff decision was DROP
    slash_reply: str = ""                                    # set when action=SLASH
    fast_ack: bool = False                                   # boss got an immediate receipt
    first_response_started: bool = False                     # real-model first-response job accepted


@dataclass(frozen=True)
class _Deps:
    adapter_for_agent: Callable
    tmux_inject: Callable
    append_message: Callable
    session: str


def _resolve_deps(adapter_lookup, tmux_inject, append_message, session) -> _Deps:
    """Fill in production defaults for any None collaborator."""
    return _Deps(
        adapter_for_agent=adapter_lookup or _default_adapter_for_agent,
        tmux_inject=tmux_inject or tmux.inject,
        append_message=append_message or local_facts.append_message,
        session=session or config.session_name(),
    )


def _is_boss_message_to_manager(decision: Decision, agent: str) -> bool:
    """Human chat message routed to manager.

    Worker cards forwarded to manager have `decision.sender` set, so they
    are intentionally excluded. This is the path the boss experiences as
    "I asked the team something"; it gets high priority and optional pane
    preemption below.
    """
    if decision.action is not Action.ROUTE:
        return False
    if agent != "manager":
        return False
    if decision.sender:
        return False
    return bool(decision.text.strip())


def _priority_for_decision(agent: str, decision: Decision) -> str:
    return "高" if _is_boss_message_to_manager(decision, agent) else "中"


def _write_inbox(agent: str, sender: str, decision: Decision,
                 deps: _Deps, report: DeliveryReport) -> str:
    """Returns the local_id on success, "" on failure (failure is
    also logged to the report). The caller threads the local_id into
    the pane-inject wrapper so the agent knows which row to mark
    `claudeteam read` after replying."""
    try:
        local_id = deps.append_message(
            agent, sender, _message_content_for_agent(decision),
            priority=_priority_for_decision(agent, decision),
        )
    except Exception as e:
        print(f"  ⚠️ inbox write failed for {agent}: {e}")
        return ""
    report.written.append(agent)
    return local_id or ""


def _message_content_for_agent(decision: Decision) -> str:
    if not decision.reply_context:
        return decision.text
    return "\n\n".join([
        decision.reply_context.strip(),
        "[老板本条新消息]",
        decision.text,
    ]).strip()


def _build_wake_args(agent: str, adapter) -> dict:
    """Kwargs for wake_fn: spawn_cmd, init_msg, on_woken.

    Wrapping the lazy-wake setup keeps `_inject_to_pane` focused on its
    actual job (deliver text) and isolates the cross-module wiring
    (lifecycle.pane_env_prefix, identity.init_prompt, status upsert).
    """
    from claudeteam.runtime import tunables
    return {
        "spawn_cmd": lazy_spawn_cmd(agent),
        "init_msg": _identity.init_prompt(agent),
        "timeout_s": float(tunables.tunable("wake.lazy_wake_timeout_s", 30.0)),
        # Flip status from "待命" to "进行中" so `claudeteam team` reflects
        # reality once the lazy pane actually wakes up.
        "on_woken": lambda: local_facts.upsert_status(
            agent, "进行中", "responding to first message"),
    }


# Heuristic: if the boss message asks for a summary / report-back / status
# coordinated through manager, workers should also send the result to
# manager (not just `say` to chat) so manager's inbox pings and they can
# follow up. manager's pane doesn't see chat messages — only its own
# inbox + dispatched messages — so without this hint the dispatch +
# summarize loop stalls (boss saw this 2026-05-05 in a Round C dry-run:
# manager dispatched, worker counted, posted to chat, manager never
# learned and never summarized).
_SUMMARY_CUE_TOKENS = (
    "汇总", "汇报", "总结", "报告",
    "summarize", "summary", "report back",
    "manager 跟进", "manager 综合",
)
_STATUS_QUERY_TOKENS = (
    "现在什么情况", "什么情况", "有真的在做", "进度", "当前状态",
    "现在在干什么", "卡住了吗", "待我拍板", "待老板拍板",
    "进展如何", "进展怎么样", "现在怎么样", "还没好吗",
    "又不行", "超时了吗",
)
_NATURAL_PROGRESS_EVIDENCE_TOKENS = (
    "截图", "图片", "浏览器", "上传", "附件", "预览", "preview",
    "URL", "url", "视觉", "UI", "ui", "验收", "外部平台",
    "页面", "小红书", "MasterGo", "mastergo",
)


def _wants_manager_summary(text: str) -> bool:
    low = text.lower()
    return any(tok.lower() in low for tok in _SUMMARY_CUE_TOKENS)


def _wants_realtime_status(text: str) -> bool:
    low = text.lower()
    return any(tok.lower() in low for tok in _STATUS_QUERY_TOKENS)


def _needs_natural_progress_first(agent: str, decision: Decision) -> bool:
    if agent != "manager":
        return False
    if decision.sender not in {"", "user"}:
        return False
    content = _message_content_for_agent(decision)
    if not _wants_realtime_status(content):
        return False
    return (any(tok in content for tok in _NATURAL_PROGRESS_EVIDENCE_TOKENS)
            or _wants_realtime_status(decision.text))


def _topic_event_for_decision(decision: Decision, agent: str) -> tuple[dict | None, str]:
    """Update/render topic context for a human boss message to manager.

    This is the enforcement layer behind the lightweight `#topic` protocol.
    The router stays pure; the delivery layer writes inbox + topic state in
    the same place so a delivered boss message has a durable conversation lane
    before the manager sees it.

    Quote-reply linking: when a boss message is a Feishu quote-reply to a
    previous message, look up which topic lane the parent was in and switch
    back to it.  This mirrors the human habit of "replying in-thread".

    Topic drift: when a message shares almost no meaningful terms with the
    current topic capsule, auto-spawn a new topic so parallel conversation
    lanes don't get mixed into one.
    """
    if not _is_boss_message_to_manager(decision, agent):
        return None, ""

    # ── quote-reply: find parent message's topic ──────────────────
    reply_to = str(decision.reply_to or "").strip()
    if reply_to and not topics.parse_topic_prefix(decision.text)[2]:
        parent_topic = topics.lookup_parent_topic(reply_to)
        if parent_topic:
            try:
                row = topics.switch(
                    parent_topic, msg_id=decision.msg_id,
                    initial_capsule=topics._initial_capsule(decision.text)
                )
                event = {
                    "kind": "switch",
                    "topic": row,
                    "body": decision.text,
                    "previous": row.get("_previous", ""),
                    "changed": bool(row.get("_changed")),
                }
                if decision.msg_id:
                    topics.record_msg_topic(decision.msg_id, str(row.get("name") or parent_topic))
                return event, topics.render_event_for_prompt(event)
            except Exception:
                pass  # fall through to normal handling

    # ── reply context without #topic marker ──────────────────────
    if decision.reply_context.strip():
        _, _, matched = topics.parse_topic_prefix(decision.text)
        if not matched:
            return None, (
                "\n[话题上下文] 本条含飞书回复上下文，回复上下文优先。"
                "不要用当前 topic 胶囊覆盖父消息，也不要因「什么意思/继续/展开」"
                "这类短追问自动延续 current topic；先围绕父消息和老板本条新消息回答。"
                "若父消息或老板本条明确带 #话题，再用 `claudeteam topic switch/note` 沉淀。\n"
            )

    # ── explicit #topic marker ───────────────────────────────────
    _, _, has_marker = topics.parse_topic_prefix(decision.text)
    if has_marker:
        try:
            event = topics.apply_message(decision.text, msg_id=decision.msg_id)
        except Exception as e:
            return None, (f"\n[话题上下文] 写入 topics.json 失败：{e}。"
                          f"本轮先按用户消息处理，之后用 `claudeteam topic` 对账。\n")
        return event, topics.render_event_for_prompt(event)

    # ── drift detection: auto-spawn topic if too different ──────
    cur = topics.current()
    if cur and not has_marker:
        capsule = str(cur.get("capsule") or "")
        if (
            bool(tunables.tunable("topics.auto_drift_enabled", False))
            and topics.topic_drift_detected(decision.text, capsule)
        ):
            auto_name = topics.auto_topic_name(decision.text)
            try:
                row = topics.switch(
                    auto_name, msg_id=decision.msg_id,
                    initial_capsule=topics._initial_capsule(decision.text)
                )
                event = {
                    "kind": "switch",
                    "topic": row,
                    "body": decision.text,
                    "previous": row.get("_previous", ""),
                    "changed": bool(row.get("_changed")),
                }
                if decision.msg_id:
                    topics.record_msg_topic(decision.msg_id, str(row.get("name") or auto_name))
                return event, (
                    "\n[话题上下文] 自动检测话题漂移：本条与当前话题胶囊几乎无重叠，"
                    f"已自动创建 `#{auto_name}`。"
                    "若判断有误，用 `claudeteam topic switch <原话题>` 切回。\n\n"
                    f"{topics.render_event_for_prompt(event)}"
                )
            except Exception:
                pass  # fall through to normal handling

    try:
        event = topics.apply_message(decision.text, msg_id=decision.msg_id)
    except Exception as e:
        return None, (f"\n[话题上下文] 写入 topics.json 失败：{e}。"
                      f"本轮先按用户消息处理，之后用 `claudeteam topic` 对账。\n")
    return event, topics.render_event_for_prompt(event)


def _message_age_s(create_time: str) -> float | None:
    raw = str(create_time or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        value = int(raw)
        sent_at = value / 1000 if value > 10_000_000_000 else value
        return time.time() - sent_at
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            sent_at = _dt.datetime.strptime(raw, fmt).timestamp()
            return time.time() - sent_at
        except ValueError:
            continue
    return None


def _should_fast_ack(decision: Decision, agent: str) -> bool:
    """True when a human boss message just entered the manager queue.

    The manager may run a high-reasoning model and take a while to answer.
    This router-level receipt is deliberately zero-LLM: it only tells the
    boss the message is queued and being handled.
    """
    from claudeteam.runtime import tunables

    if not bool(tunables.tunable("router.fast_ack.enabled", False)):
        return False
    if bool(tunables.tunable("router.first_response.enabled", False)):
        return False
    if not _is_boss_message_to_manager(decision, agent):
        return False
    max_age_s = float(tunables.tunable("router.fast_ack.max_age_s", 180.0))
    if max_age_s > 0:
        age = _message_age_s(decision.create_time)
        if age is not None and age > max_age_s:
            return False
    return True


def _should_preempt_for_boss(decision: Decision, agent: str) -> bool:
    if not bool(tunables.tunable("router.boss_preempt.enabled", True)):
        return False
    return _is_boss_message_to_manager(decision, agent)


def _preempt_busy_manager_if_needed(agent: str, decision: Decision,
                                    target: tmux.Target, adapter) -> bool:
    """Interrupt a busy manager pane before injecting a boss message.

    This is deliberately narrow: only human boss → manager messages can
    preempt. Worker progress and internal nudges continue to queue behind
    the manager's current work.
    """
    if not _should_preempt_for_boss(decision, agent):
        return False
    force_preempt = _manager_real_first_response_s(agent, decision) > 0
    if not force_preempt and wake.is_ready(target, adapter):
        return False
    keys = str(tunables.tunable(
        "router.boss_preempt.keys", "C-c")).split()
    if not keys:
        return False
    try:
        return bool(tmux.send_keys(target, *keys))
    except Exception as e:
        print(f"  ⚠️ boss preempt failed for {agent}: {e}")
        return False



def _topic_ack_line(topic_event: dict | None) -> str:
    row = topic_event.get("topic") if topic_event else None
    if not row:
        return "暂未绑定；manager 会先判断归属，必要时新建或切换话题。"
    name = str(row.get("name") or "").strip()
    if not name:
        return "暂未绑定；manager 会先判断归属，必要时新建或切换话题。"
    action = "切换到" if topic_event.get("kind") == "switch" else "延续"
    return f"{action} #{name}"


def _format_fast_ack_text(raw: str, topic_event: dict | None) -> str:
    topic_line = _topic_ack_line(topic_event)
    row = topic_event.get("topic") if topic_event else None
    topic_name = str(row.get("name") or "") if row else ""
    try:
        text = raw.format(topic=topic_name, topic_line=topic_line)
    except (KeyError, IndexError, ValueError):
        text = raw
    if "话题：" not in text and "话题:" not in text and topic_line not in text:
        text = f"{text}\n话题：{topic_line}"
    return text


def _send_fast_ack(decision: Decision, *, topic_event: dict | None,
                   chat_send: Callable | None,
                   chat_id: str | None, profile: str | None) -> bool:
    from claudeteam.runtime import tunables

    chat = chat_id if chat_id is not None else config.chat_id()
    if not chat:
        return False
    text = str(tunables.tunable(
        "router.fast_ack.text",
        "系统已收到并写入主管队列。这只是自动入队回执，不代表主管已完成处理。",
    )).strip()
    if not text:
        return False
    text = _format_fast_ack_text(text, topic_event)
    prof = profile if profile is not None else config.lark_profile()
    send_text = chat_send or _chat.send_text
    try:
        return send_text(chat, text, profile=prof, as_user=False) is not None
    except Exception as e:
        print(f"  ⚠️ fast ack failed for {decision.msg_id}: {e}")
        return False


def _start_first_response(decision: Decision, *, local_id: str,
                          topic_event: dict | None,
                          chat_send: Callable | None,
                          chat_id: str | None,
                          profile: str | None,
                          first_response_runner: Callable | None) -> bool:
    runner = first_response_runner or _first_response.start
    try:
        return bool(runner(
            decision,
            local_id=local_id,
            topic_event=topic_event,
            chat_send=chat_send,
            chat_id=chat_id,
            profile=profile,
        ))
    except Exception as e:
        local_facts.append_log(
            "manager", "first_response_failed",
            f"trace={local_id or decision.msg_id}; msg_id={decision.msg_id}; error=start failed: {e}",
            ref=local_id or decision.msg_id,
        )
        print(f"  ⚠️ first-response runner failed to start for {decision.msg_id}: {e}")
        return False


def _manager_real_first_response_s(agent: str, decision: Decision) -> float:
    """Return the opt-in manager first-response SLA in seconds.

    This is manager-only and boss-message-only. Other teams keep the
    existing evidence-first inject hint unless they explicitly set
    `router.manager_real_first_response_s`.
    """
    if agent != "manager" or decision.sender not in {"", "user"}:
        return 0.0
    if bool(tunables.tunable("router.first_response.enabled", False)):
        return 0.0
    try:
        return float(tunables.tunable("router.manager_real_first_response_s", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _compose_inject_text(agent: str, decision: Decision,
                         local_id: str = "",
                         topic_context: str = "") -> str:
    """Prepend a short routing-context header to the chat message before
    injecting it into the agent's pane.

    Without this header, claude treats raw injected text as a normal
    user prompt and replies in-pane (which the boss can't see). The
    hint primes the agent to:
      1. Reply via the correct channel (`claudeteam say` for chat-
         originated; `claudeteam send` for peer messages).
      2. Mark the inbox row `read` afterward (deliver knows the
         local_id since it just appended the row) so the inbox
         doesn't accumulate unread rows.
      3. If the message hints at manager-summary follow-up, non-
         manager agents are also told to `claudeteam send manager`
         so manager's inbox pings — manager's pane is blind to
         chat-only `say` events otherwise."""
    sender = decision.sender or "user"
    ct = team_command.safe_cli_cmd(ensure=True)
    read_hint = (f" 完成后用 `{ct} read {local_id}` 销 inbox。"
                 if local_id else "")
    task_list_hint = f"先 `{ct} task list --assignee {agent} --active` 对账当前活跃任务。"
    boss_preempt_hint = ""
    if agent == "manager" and sender in {"user", ""}:
        boss_preempt_hint = (
            "老板消息绝对抢占：先只处理当前老板这条，"
            "不要先跑 `task list --assignee manager --active`，"
            "不要先验收旧 worker 回执，也不要先批量 `task done` 清尾巴。"
        )
    boss_or_task_hint = boss_preempt_hint or task_list_hint
    context_hint = (
        f"{current_time_line()} 遇到 今天/上午/刚才/之前/还记得吗，"
        f"先查 `{ct} recall {agent}`、inbox、task、logs/artifacts 再答。"
    )
    summary_hint = ""
    if (agent != "manager"
            and _wants_manager_summary(decision.text)):
        summary_hint = (f" 这条似乎需要 manager 汇总，处理完后**额外**"
                        f"发一句 `{ct} send manager {agent} \"<结果>\"` "
                        f"让 manager inbox 知道你的进度。")
    realtime_status_hint = ""
    if (agent == "manager"
            and (decision.sender == "" or decision.sender == "user")
            and _wants_realtime_status(decision.text)):
        if bool(tunables.tunable(
                "chat.visible_quality_guard.require_visual_status_image", False)):
            realtime_status_hint = (
                " 这条是现状/进度问题，团队启用了现场速报门禁：必须先运行 "
                "`scripts/traffic-status.py --out artifacts/traffic/boss-comms/latest-status-card.md`、"
                "`scripts/traffic-field-report.py`、"
                "`scripts/traffic-gate.py status-card --file artifacts/traffic/boss-comms/latest-status-card.md` "
                "和 `scripts/traffic-gate.py field-report --file artifacts/traffic/boss-comms/field-report/latest-field-report.md`，"
                f"再 `cat artifacts/traffic/boss-comms/field-report/latest-field-report.md | {ct} say manager - --to user --image artifacts/traffic/boss-comms/field-report/latest-field-report.png`；"
                "禁止无图纯文字回复。")
        elif bool(tunables.tunable(
                "chat.visible_quality_guard.require_realtime_status_card", False)):
            realtime_status_hint = (
                " 这条是现状/进度问题，团队启用了实时状态卡门禁：必须先运行 "
                "`scripts/traffic-status.py --out artifacts/traffic/boss-comms/latest-status-card.md` "
                "和 `scripts/traffic-gate.py status-card --file artifacts/traffic/boss-comms/latest-status-card.md`，"
                f"再 `cat artifacts/traffic/boss-comms/latest-status-card.md | {ct} say manager - --to user`；"
                "禁止用 task list/recall 的历史总结替代实时看板。")
    natural_progress_hint = ""
    if _needs_natural_progress_first(agent, decision):
        natural_progress_hint = (
            " 这条是老板追问进展/卡点：如果完整结论需要截图、图片、浏览器、"
            "上传、预览 URL、UI 验收或外部平台证据，第一步先用 "
            f"`{ct} say manager - --to user` 发一条自然语言进度更新，"
            "说清谁在做、做到哪、卡在哪、下次什么时候回；进度更新不能宣称"
            "已完成/已通过/已验收，然后再继续补正式报告。")
    response_contract_hint = ""
    if (agent == "manager"
            and (decision.sender == "" or decision.sender == "user")
            and bool(tunables.tunable("router.first_response.enabled", False))):
        response_contract_hint = (
            " 本队启用了首响行动契约：router 的首响只负责 10 秒内接住老板，"
            f"正式 `{ct} say manager - --to user` 前必须兑现首响承诺的下一步；"
            "如果还没拿到资料/验证/派工结果，就写当前进展或 blocker，不要装作已完成。"
            "say 命令会做轻量契约门禁并留下 fulfillment 日志。")
    # 简短引导 — 长解释属于 identity.md 的职责，不是每次注入都重复一遍。
    # 关键指示：哪个频道回 + 怎么 mark read（如果 local_id 已知）+ 是否需
    # 要 send manager 让其汇总。具体命令格式 / --to 选择交给 identity 教。
    if sender == "user" or not sender:
        if agent == "manager":
            reply_hint = (
                f"再用 stdin 形式 `{ct} say {agent} - --to user` 回群；"
                "含引号/反引号/URL/多行不要包进 shell 引号。"
            )
            first_response_s = _manager_real_first_response_s(agent, decision)
        else:
            reply_hint = (
                f"老板点名你或你有真实交付/真实 blocker/需要老板动作时，"
                f"可用 stdin 形式 `{ct} say {agent} - --to user` 直报；"
                f"否则先用 `{ct} send manager {agent} \"...\"` 交给 manager 汇总。"
            )
            first_response_s = 0.0
        if first_response_s > 0:
            hint = (
                f"[群聊·老板] {current_time_line()} "
                f"这队启用了 manager 真实首响门禁：先在 {first_response_s:g} 秒内"
                "基于已有上下文生成第一段真实模型回应，第一段前不要运行 "
                f"`{ct} health`、`task list`、`task get`、recall、浏览器、"
                "截图、git、接口或长文件读取。第一段必须是老板可见的自然语言首响，"
                "像真实主管先接住现场：根据老板语气匹配紧急、不满、好奇或闲聊；"
                "1-3 句，总字数不超过 120 字；要包含你已理解什么、先怎么处理、"
                "下一条会补什么证据，但不要把「意图/风险/负责人/证据/下一证据」"
                f"这些字段标签发给老板。{reply_hint}"
                f"首响发出后，先用 `{ct} log manager first_response_audit "
                f"\"trace={local_id or decision.msg_id}; intent=一句话; risk=一句话; "
                "owner=职责或worker; next_evidence=下一证据\" "
                f"{local_id or decision.msg_id}` 记录内部审计；"
                f"然后再执行常规核验：{boss_or_task_hint}{context_hint}"
                "补查/派活/看日志/看产物；禁止把自动 fast_ack 当成 manager 已响应；"
                "如果超过首响时限是 Feishu/router/发送链路导致，单独记为 blocker。"
                f"{summary_hint}{natural_progress_hint}"
                f"{realtime_status_hint}{read_hint}"
            )
        else:
            hint = (f"[群聊·老板] {context_hint}{boss_or_task_hint}"
                    f"先做最小真实动作：查证/跑命令/派活/看日志/看产物，"
                    f"{reply_hint}"
                    f"禁止只说「我去核对/稍后给结论」就 `read` 销账；"
                    f"没有新事实就继续执行或明确真实 blocker。"
                    f"{summary_hint}{natural_progress_hint}"
                    f"{response_contract_hint}"
                    f"{realtime_status_hint}{read_hint}")
    else:
        hint = (f"[同事·{sender}] {context_hint}{task_list_hint}"
                f"回 `{ct} send {sender} {agent} "
                f"\"...\"`；进度回报带 `--task-id <T-id>`，完工回报再加 "
                f"`--artifact <path> --done`；对齐/待命/继续监控等内部确认不要 "
                f"`say` 刷群。只有真实交付、真实 blocker、需要老板动作或老板点名时，"
                f"才用 stdin 形式 `{ct} say {agent} - --to user`。{read_hint}")
    if topic_context and _manager_real_first_response_s(agent, decision) > 0:
        hint = (
            f"{hint}\n[话题上下文延迟核验] 这条消息有话题胶囊，"
            "但为了首响 SLA，第一段前不要展开；首响发出后再查 topic/task/inbox。"
        )
    elif topic_context:
        hint = f"{hint}\n{topic_context.strip()}"
    content = _message_content_for_agent(decision)
    return f"{hint}\n\n{content}"


def _inject_to_pane(agent: str, decision: Decision,
                    deps: _Deps, wake_fn: Callable | None,
                    local_id: str = "",
                    topic_context: str = "") -> str:
    """Deliver `decision.text` to the agent's pane (wrapped with a
    routing-context hint so the agent posts replies via `claudeteam
    say` instead of answering in pane). `local_id` is appended to the
    hint so the agent knows which inbox row to mark read.

    Returns a DeliveryReport field name: 'injected' / 'failed_inject' /
    'rate_limited'.
    """
    target = tmux.Target(deps.session, agent)
    try:
        adapter = deps.adapter_for_agent(agent)
        if wake.is_rate_limited(target, adapter):
            print(f"  ⏸  {agent} rate-limited; inbox row kept, inject skipped")
            return "rate_limited"
        _preempt_busy_manager_if_needed(agent, decision, target, adapter)
        ready_before = True
        if wake_fn is not None:
            try:
                ready_before = wake.is_ready(target, adapter)
            except Exception as e:
                print(f"  ⚠️ {agent} readiness check skipped: {e}")
        identity_changed = False
        try:
            if _identity.identity_path(agent).exists():
                _, identity_changed = _identity.write_if_changed(agent)
        except Exception as e:
            print(f"  ⚠️ identity refresh skipped for {agent}: {e}")
        if wake_fn is not None and not ready_before:
            if not wake_fn(target, adapter, **_build_wake_args(agent, adapter)):
                print(f"  ⚠️ {agent} pane not ready; inbox row kept, inject skipped")
                return "failed_inject"
        elif identity_changed:
            if deps.tmux_inject(
                    target, _identity.init_prompt(agent),
                    submit_keys=adapter.submit_keys()):
                print(f"  🔁 {agent} identity changed; re-injected init prompt")
            else:
                print(f"  ⚠️ {agent} identity changed but init prompt inject failed")
        text = _compose_inject_text(
            agent, decision, local_id=local_id,
            topic_context=topic_context,
        )
        ok = deps.tmux_inject(target, text, submit_keys=adapter.submit_keys())
    except Exception as e:
        print(f"  ⚠️ inject error for {agent}: {e}")
        return "failed_inject"
    return "injected" if ok else "failed_inject"


def apply(decision: Decision, *,
          adapter_for_agent: Callable | None = None,
          tmux_inject: Callable | None = None,
          append_message: Callable | None = None,
          wake_fn: Callable | None = None,
          session: str | None = None,
          team_agents: list[str] | None = None,
          lazy_agents: frozenset[str] | None = None,
          slash_dispatch: Callable | None = None,
          chat_send: Callable | None = None,
          chat_send_card: Callable | None = None,
          first_response_runner: Callable | None = None,
          chat_id: str | None = None,
          profile: str | None = None) -> DeliveryReport:
    """Apply `decision`. Side-effects per action:

    DROP       — no-op (skipped=True).
    SLASH      — dispatch via slash registry, post reply to chat as bot.
                 Zero pane touches.
    BROADCAST  — same as ROUTE but targets are all non-sender agents.
    ROUTE      — write inbox row + tmux inject for each target.

    All collaborators are injectable for tests; production defaults read
    from the real modules.
    """
    if decision.is_drop():
        return DeliveryReport(skipped=True)

    deps = _resolve_deps(adapter_for_agent, tmux_inject, append_message, session)

    if decision.action is Action.SLASH:
        return _apply_slash(decision, deps,
                            team_agents=team_agents,
                            lazy_agents=lazy_agents,
                            slash_dispatch=slash_dispatch,
                            chat_send=chat_send,
                            chat_send_card=chat_send_card,
                            chat_id=chat_id,
                            profile=profile)

    sender = decision.sender or "user"
    report = DeliveryReport()
    acked = False
    first_response_attempted = False
    for agent in decision.targets:
        local_id = _write_inbox(agent, sender, decision, deps, report)
        if not local_id:
            continue
        topic_event, topic_context = _topic_event_for_decision(decision, agent)
        if (not first_response_attempted
                and _first_response.should_run(decision, agent)):
            report.first_response_started = _start_first_response(
                decision,
                local_id=local_id,
                topic_event=topic_event,
                chat_send=chat_send,
                chat_id=chat_id,
                profile=profile,
                first_response_runner=first_response_runner,
            )
            first_response_attempted = True
        if not acked and _should_fast_ack(decision, agent):
            report.fast_ack = _send_fast_ack(
                decision,
                topic_event=topic_event,
                chat_send=chat_send,
                chat_id=chat_id,
                profile=profile,
            )
            acked = True
        outcome = _inject_to_pane(agent, decision, deps, wake_fn,
                                   local_id=local_id,
                                   topic_context=topic_context)
        getattr(report, outcome).append(agent)
    return report


def _apply_slash(decision: Decision, deps: _Deps, *,
                 team_agents: list[str] | None,
                 lazy_agents: frozenset[str] | None,
                 slash_dispatch: Callable | None,
                 chat_send: Callable | None,
                 chat_send_card: Callable | None,
                 chat_id: str | None,
                 profile: str | None) -> DeliveryReport:
    """Run slash command at router level (zero LLM) and post reply to chat
    as bot. Pane is never touched.

    Round-79: dispatch may now return a dict (Feishu card schema) — branch
    on type to call chat.send_card instead of chat.send_text. `reply_to`
    only applies to the text path; cards don't support thread-reply.
    """
    dispatch = slash_dispatch or _slash.dispatch
    ctx = _slash.SlashContext(
        team_agents=team_agents or config.agent_names(),
        session=deps.session,
        lazy_agents=lazy_agents if lazy_agents is not None else frozenset(),
    )
    reply = dispatch(decision.text, ctx)

    report = DeliveryReport(slash_reply=reply if isinstance(reply, str) else "")
    chat = chat_id if chat_id is not None else config.chat_id()
    if not chat:
        preview = (reply[:200] if isinstance(reply, str)
                   else str(reply)[:200])
        print(f"  ⚠️ slash reply ready but chat_id unset; reply suppressed:\n{preview}")
        return report
    prof = profile if profile is not None else config.lark_profile()
    if isinstance(reply, dict):
        send_card = chat_send_card or _chat.send_card
        result = send_card(chat, reply, profile=prof, as_user=False)
    else:
        send_text = chat_send or _chat.send_text
        result = send_text(chat, reply, profile=prof, as_user=False,
                           reply_to=decision.msg_id)
    if result is None:
        # chat.send_text/send_card already logged the underlying failure.
        # Surface a one-line warning here so router.log makes it obvious
        # the slash dispatch ran but the reply never landed in chat.
        print(f"  ⚠️ slash dispatched OK but chat reply for {decision.msg_id} failed to post")
    return report
