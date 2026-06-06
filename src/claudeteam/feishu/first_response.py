"""Fast real-model first response for boss -> manager messages.

This path is deliberately separate from the manager's tmux pane.  The pane
still receives the full message for verification, dispatch, and closure; this
module only sends the boss a short real-model first response so a busy pane
does not break the 10s front-desk SLA.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from claudeteam.feishu import chat as _chat
from claudeteam.feishu.router import Action, Decision
from claudeteam.runtime import config, providers, tunables
from claudeteam.store import local_facts
from claudeteam.util import current_time_line


@dataclass(frozen=True)
class FirstResponseResult:
    ok: bool
    text: str = ""
    error: str = ""
    elapsed_ms: int = 0
    provider: str = ""
    model: str = ""
    send_message_id: str = ""
    contract: dict[str, str] = field(default_factory=dict)


VALID_CONTRACT_TYPES = (
    "quick_answer",
    "research",
    "verification",
    "dispatch",
    "clarification",
    "blocker",
)

_DEFAULT_NEXT_STEP = {
    "quick_answer": "直接给出判断和下一步建议",
    "research": "补齐资料来源、理论依据和可执行结论",
    "verification": "核对真实链路、数据或日志后给出结论",
    "dispatch": "明确负责人、分工边界和下一次回报口径",
    "clarification": "先确认缺失条件，再给可执行方案",
    "blocker": "定位阻塞点、影响范围和需要谁处理",
}
_STATUS_PROBE_MARKERS = (
    "还活着", "还在吗", "团队成员", "是否在线", "在线吗", "可响应",
    "能不能继续接活", "能不能接活", "最近回执", "活跃状态",
)
_STATUS_PROBE_ACTION_MARKERS = (
    "已查", "刚查", "查了", "确认", "看到", "对账", "自检",
    "实测", "复核", "已核对", "已看", "刚看", "核对了",
)
_STATUS_PROBE_EVIDENCE_MARKERS = (
    "health", "router", "watchdog", "heartbeat", "heartbeats",
    "task", "active", "inbox", "回执", "日志", "截图", "端口",
    "curl", "可连", "不可连", "二维码",
)


HttpJson = Callable[[str, dict, dict, float], dict]


def _message_age_s(create_time: str) -> float | None:
    raw = str(create_time or "").strip()
    if not raw or not raw.isdigit():
        return None
    value = int(raw)
    sent_at = value / 1000 if value > 10_000_000_000 else value
    return time.time() - sent_at


def should_run(decision: Decision, agent: str) -> bool:
    if not bool(tunables.tunable("router.first_response.enabled", False)):
        return False
    if decision.action is not Action.ROUTE:
        return False
    if agent != "manager":
        return False
    if decision.sender not in {"", "user"}:
        return False
    if not decision.text.strip():
        return False
    max_age_s = float(tunables.tunable("router.first_response.max_age_s", 180.0))
    if max_age_s > 0:
        age = _message_age_s(decision.create_time)
        if age is not None and age > max_age_s:
            return False
    return True


def start(decision: Decision, *, local_id: str, topic_event: dict | None = None,
          chat_send: Callable | None = None, chat_id: str | None = None,
          profile: str | None = None) -> bool:
    """Start the first-response job in a daemon thread.

    Returns True when the job was accepted.  The actual send result is logged
    by `run_once`; callers should still inject the original message into the
    manager pane regardless of this return value.
    """
    if not should_run(decision, "manager"):
        return False
    thread = threading.Thread(
        target=run_once,
        kwargs={
            "decision": decision,
            "local_id": local_id,
            "topic_event": topic_event,
            "chat_send": chat_send,
            "chat_id": chat_id,
            "profile": profile,
        },
        name=f"claudeteam-first-response-{(decision.msg_id or local_id)[:16]}",
        daemon=True,
    )
    thread.start()
    return True


def run_once(decision: Decision, *, local_id: str, topic_event: dict | None = None,
             chat_send: Callable | None = None, chat_id: str | None = None,
             profile: str | None = None,
             generate_fn: Callable[..., FirstResponseResult] | None = None,
             mark_first_response: Callable | None = None,
             http_json: HttpJson | None = None,
             provider_env: dict[str, str] | None = None) -> FirstResponseResult:
    started = time.monotonic()
    generate = generate_fn or generate_text
    result = generate(
        decision,
        topic_event=topic_event,
        http_json=http_json,
        provider_env=provider_env,
    )
    elapsed_ms = result.elapsed_ms or int((time.monotonic() - started) * 1000)
    if not result.ok:
        _send_failure_fallback(
            decision,
            local_id=local_id,
            topic_event=topic_event,
            chat_send=chat_send,
            chat_id=chat_id,
            profile=profile,
        )
        _log_failure(decision, local_id, result.error, elapsed_ms, result)
        return result

    chat = chat_id if chat_id is not None else config.chat_id()
    if not chat:
        return _failure_result(decision, local_id, result, "chat_id unset", elapsed_ms)

    prof = profile if profile is not None else config.lark_profile()
    send = chat_send or _chat.send_text
    as_user = bool(tunables.tunable("router.first_response.send_as_user", False))
    reply_to = ""
    if bool(tunables.tunable("router.first_response.reply_to_original", False)):
        reply_to = decision.msg_id
    try:
        sent = send(chat, result.text, profile=prof, as_user=as_user,
                    reply_to=reply_to)
    except Exception as e:
        return _failure_result(
            decision, local_id, result, f"chat send failed: {e}", elapsed_ms)
    if sent is None:
        return _failure_result(
            decision, local_id, result, "chat send returned None", elapsed_ms)

    sent_msg_id = str(sent.get("message_id") or sent.get("messageId") or "")
    marker = mark_first_response or local_facts.mark_first_response
    try:
        marker(
            local_id,
            response_message_id=sent_msg_id,
            elapsed_ms=elapsed_ms,
            response_contract=result.contract,
        )
    except Exception as e:
        local_facts.append_log(
            "manager", "first_response_mark_failed",
            f"trace={local_id or decision.msg_id}; error={e}",
            ref=local_id or decision.msg_id,
        )
    local_facts.append_log(
        "manager", "first_response_sent",
        (f"trace={local_id or decision.msg_id}; msg_id={decision.msg_id}; "
         f"elapsed_ms={elapsed_ms}; provider={result.provider}; "
         f"model={result.model}; response_message_id={sent_msg_id}; "
         f"contract={json.dumps(result.contract, ensure_ascii=False)}; "
         f"text={result.text}"),
        ref=local_id or decision.msg_id,
    )
    return FirstResponseResult(
        ok=True,
        text=result.text,
        elapsed_ms=elapsed_ms,
        provider=result.provider,
        model=result.model,
        send_message_id=sent_msg_id,
        contract=result.contract,
    )


def _failure_result(decision: Decision, local_id: str, result: FirstResponseResult,
                    error: str, elapsed_ms: int) -> FirstResponseResult:
    failed = FirstResponseResult(
        ok=False,
        text=result.text,
        error=error,
        elapsed_ms=elapsed_ms,
        provider=result.provider,
        model=result.model,
        contract=result.contract,
    )
    _log_failure(decision, local_id, failed.error, elapsed_ms, failed)
    return failed


def generate_text(decision: Decision, *, topic_event: dict | None = None,
                  http_json: HttpJson | None = None,
                  provider_env: dict[str, str] | None = None) -> FirstResponseResult:
    started = time.monotonic()
    provider = str(tunables.tunable(
        "router.first_response.provider", "anthropic")).strip().lower()
    timeout_s = float(tunables.tunable("router.first_response.timeout_s", 6.0))
    try:
        if provider not in {"anthropic", "claude"}:
            raise RuntimeError(f"unsupported provider: {provider}")
        raw_text, model = _generate_anthropic(
            decision, topic_event=topic_event,
            http_json=http_json, timeout_s=timeout_s,
            provider_env=provider_env,
        )
        text, raw_contract = _parse_model_payload(raw_text, decision)
    except Exception as e:
        return FirstResponseResult(
            ok=False,
            error=str(e),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            provider=provider,
        )
    text = _clean_model_text(text)
    contract = _normalize_contract(raw_contract, text, decision)
    if not text:
        return FirstResponseResult(
            ok=False,
            error="empty model response",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            provider=provider,
            model=model,
            contract=contract,
        )
    if quality_error := _first_response_quality_error(decision, text):
        return FirstResponseResult(
            ok=False,
            error=quality_error,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            provider=provider,
            model=model,
            contract=contract,
        )
    return FirstResponseResult(
        ok=True,
        text=text,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        provider=provider,
        model=model,
        contract=contract,
    )


def _generate_anthropic(decision: Decision, *, topic_event: dict | None,
                        http_json: HttpJson | None, timeout_s: float,
                        provider_env: dict[str, str] | None) -> tuple[str, str]:
    env = _provider_env_for_first_response(provider_env)
    base_url = env.get("ANTHROPIC_BASE_URL", "").strip()
    token = env.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    model = _resolve_model(env)
    if not base_url:
        raise RuntimeError("ANTHROPIC_BASE_URL unset")
    if not token:
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN unset")
    if not model:
        raise RuntimeError("first response model unset")
    endpoint = str(tunables.tunable(
        "router.first_response.endpoint", "messages")).strip().lower()
    endpoints = _endpoint_sequence(endpoint)
    errors = []
    for name in endpoints:
        try:
            text = _generate_with_endpoint(
                name, base_url, token, model, decision, topic_event,
                http_json=http_json, timeout_s=timeout_s,
            )
            return text, model
        except RuntimeError as e:
            errors.append(f"{name}: {e}")
            if not _should_try_next_endpoint(str(e)):
                break
    raise RuntimeError("; ".join(errors) or f"unsupported endpoint: {endpoint}")


def _provider_env_for_first_response(
        explicit_env: dict[str, str] | None) -> dict[str, str]:
    if explicit_env is not None:
        return explicit_env
    env = providers.provider_env_for_agent("manager")
    preset_name = str(tunables.tunable(
        "router.first_response.provider_preset", "")).strip()
    if preset_name:
        preset = providers.load_presets().get(preset_name)
        if preset:
            env = {**env, **preset}
    return env


def _endpoint_sequence(endpoint: str) -> list[str]:
    aliases = {
        "anthropic": "messages",
        "claude": "messages",
        "message": "messages",
        "openai_responses": "responses",
        "openai-response": "responses",
        "response": "responses",
        "chat": "chat_completions",
        "chat_completions": "chat_completions",
        "chat-completions": "chat_completions",
        "chat/completions": "chat_completions",
    }
    normalized = aliases.get(endpoint, endpoint)
    if normalized == "auto":
        return ["responses", "messages", "chat_completions"]
    if normalized in {"messages", "responses", "chat_completions"}:
        fallbacks = {
            "messages": ["messages", "responses", "chat_completions"],
            "responses": ["responses", "chat_completions", "messages"],
            "chat_completions": ["chat_completions", "responses", "messages"],
        }
        return fallbacks[normalized]
    return [normalized]


def _should_try_next_endpoint(error: str) -> bool:
    low = str(error or "").lower()
    markers = (
        "not found", "404", "not allowed", "不允许访问",
        "allowed endpoints", "允许的端点", "unsupported endpoint",
        "unknown endpoint", "invalid url", "method not allowed",
    )
    return any(marker in low for marker in markers)


def _generate_with_endpoint(name: str, base_url: str, token: str, model: str,
                            decision: Decision, topic_event: dict | None,
                            *, http_json: HttpJson | None,
                            timeout_s: float) -> str:
    if name == "messages":
        return _generate_anthropic_messages(
            base_url, token, model, decision, topic_event,
            http_json=http_json, timeout_s=timeout_s,
        )
    if name == "responses":
        return _generate_openai_responses(
            base_url, token, model, decision, topic_event,
            http_json=http_json, timeout_s=timeout_s,
        )
    if name == "chat_completions":
        return _generate_openai_chat(
            base_url, token, model, decision, topic_event,
            http_json=http_json, timeout_s=timeout_s,
        )
    raise RuntimeError(f"unsupported endpoint: {name}")


def _base_payload_values() -> tuple[int, float]:
    return (
        int(tunables.tunable("router.first_response.max_tokens", 180)),
        float(tunables.tunable("router.first_response.temperature", 0.2)),
    )


def _auth_headers(token: str, *, anthropic: bool = False) -> dict:
    headers = {
        "content-type": "application/json",
        "x-api-key": token,
        "authorization": f"Bearer {token}",
    }
    if anthropic:
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _generate_anthropic_messages(base_url: str, token: str, model: str,
                                 decision: Decision,
                                 topic_event: dict | None, *,
                                 http_json: HttpJson | None,
                                 timeout_s: float) -> str:
    max_tokens, temperature = _base_payload_values()
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": _system_prompt(),
        "messages": [{
            "role": "user",
            "content": _user_prompt(decision, topic_event),
        }],
    }
    data = (http_json or _post_json)(
        _join_api_path(base_url, "messages"),
        payload,
        _auth_headers(token, anthropic=True),
        timeout_s,
    )
    return _parse_anthropic_text(data)


def _generate_openai_responses(base_url: str, token: str, model: str,
                               decision: Decision,
                               topic_event: dict | None, *,
                               http_json: HttpJson | None,
                               timeout_s: float) -> str:
    max_tokens, temperature = _base_payload_values()
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(decision, topic_event)},
        ],
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    data = (http_json or _post_json)(
        _join_api_path(base_url, "responses"),
        payload,
        _auth_headers(token),
        timeout_s,
    )
    return _parse_openai_responses_text(data)


def _generate_openai_chat(base_url: str, token: str, model: str,
                          decision: Decision, topic_event: dict | None, *,
                          http_json: HttpJson | None,
                          timeout_s: float) -> str:
    max_tokens, temperature = _base_payload_values()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(decision, topic_event)},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = (http_json or _post_json)(
        _join_api_path(base_url, "chat/completions"),
        payload,
        _auth_headers(token),
        timeout_s,
    )
    return _parse_openai_chat_text(data)


def _resolve_model(env: dict[str, str]) -> str:
    requested = str(tunables.tunable(
        "router.first_response.model", "haiku")).strip()
    alias_key = providers.ALIAS_ENV_KEY.get(requested.lower())
    if alias_key and env.get(alias_key):
        return env[alias_key]
    if requested and requested.lower() not in {"default", "auto"}:
        return requested
    return env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or env.get("ANTHROPIC_MODEL", "")


def _system_prompt() -> str:
    return (
        "你是工作分身团队的主管前台。目标是在10秒内给老板一条真实模型首响，"
        "先接住语气和意图，但不要假装已经完成核验。不要引入老板没提到的项目、"
        "SPEC、代码、页面或验收对象。遇到 B、OK、继续、提交、合并、dev 分支、"
        "就这样等省略句，必须先用回复上下文和近期上下文补全指代；上下文已经能"
        "判断时不要反问老板仓库、选项或目标对象。只输出一个 JSON 对象，字段为 "
        "text 和 response_contract。text 是老板可见正文；response_contract "
        "只给系统看。如果老板是在问“还活着吗/是否在线/能不能接活”这类状态探针，"
        "首响里必须至少带一条刚核对过的证据；做不到就宁可失败，不要用空话冒充进展。"
    )


def _topic_line(topic_event: dict | None) -> str:
    row = topic_event.get("topic") if topic_event else None
    if row and row.get("name") and topic_event.get("kind") == "switch":
        return f"切换到 #{row['name']}"
    return "首响暂不展开历史话题；只按老板本条原话判断"


def _format_failure_fallback_text(topic_event: dict | None) -> str:
    raw = str(tunables.tunable(
        "router.first_response.failure_fallback_text",
        str(tunables.tunable(
            "router.fast_ack.text",
            "系统已收到并写入主管队列。这只是自动入队回执，不代表主管已完成处理。",
        ) or ""),
    ) or "").strip()
    if not raw:
        return ""
    topic_line = _topic_line(topic_event)
    topic_name = ""
    row = topic_event.get("topic") if topic_event else None
    if row:
        topic_name = str(row.get("name") or "")
    try:
        text = raw.format(topic=topic_name, topic_line=topic_line)
    except (KeyError, IndexError, ValueError):
        text = raw
    if "话题：" not in text and "话题:" not in text and topic_line not in text:
        text = f"{text}\n话题：{topic_line}"
    return _normalize_failure_fallback_text(text)


def _normalize_failure_fallback_text(text: str) -> str:
    compact = str(text or "").strip()
    if not compact:
        return ""
    if ("系统自动回执" not in compact
            and "自动入队回执" not in compact
            and "自动兜底回执" not in compact):
        compact = f"系统自动回执：{compact}"
    disclaimer = "这只是自动入队回执，不代表已完成处理或已有事实结论。"
    low = compact.casefold()
    if ("不代表已完成处理" not in compact
            and "不代表主管已完成处理" not in compact
            and "不是最终结论" not in compact
            and "不是事实结论" not in compact):
        if "\n" in compact:
            first, rest = compact.split("\n", 1)
            compact = f"{first} {disclaimer}\n{rest}"
        else:
            compact = f"{compact} {disclaimer}"
    return compact


def _send_failure_fallback(decision: Decision, *, local_id: str,
                           topic_event: dict | None,
                           chat_send: Callable | None,
                           chat_id: str | None,
                           profile: str | None) -> bool:
    if not bool(tunables.tunable("router.fast_ack.enabled", False)):
        return False
    chat = chat_id if chat_id is not None else config.chat_id()
    if not chat:
        return False
    text = _format_failure_fallback_text(topic_event)
    if not text:
        return False
    prof = profile if profile is not None else config.lark_profile()
    send = chat_send or _chat.send_text
    try:
        sent = send(chat, text, profile=prof, as_user=False)
    except Exception as e:
        local_facts.append_log(
            "manager", "first_response_fallback_failed",
            f"trace={local_id or decision.msg_id}; msg_id={decision.msg_id}; error={e}",
            ref=local_id or decision.msg_id,
        )
        return False
    if sent is None:
        local_facts.append_log(
            "manager", "first_response_fallback_failed",
            (f"trace={local_id or decision.msg_id}; msg_id={decision.msg_id}; "
             "error=chat send returned None"),
            ref=local_id or decision.msg_id,
        )
        return False
    sent_msg_id = str(sent.get("message_id") or sent.get("messageId") or "")
    local_facts.append_log(
        "manager", "first_response_fallback_sent",
        (f"trace={local_id or decision.msg_id}; msg_id={decision.msg_id}; "
         f"fallback_message_id={sent_msg_id}; text={text}"),
        ref=local_id or decision.msg_id,
    )
    return True


def _user_prompt(decision: Decision, topic_event: dict | None) -> str:
    parts = [
        current_time_line(),
        f"话题：{_topic_line(topic_event)}",
    ]
    if decision.reply_context.strip():
        parts.append(f"回复上下文：\n{decision.reply_context.strip()}")
    recent_context = _recent_context_for_prompt(decision)
    if recent_context:
        parts.append(f"近期上下文（用于补全省略句；若与老板本条冲突，以老板本条为准）：\n{recent_context}")
    parts.append(f"老板原话：\n{decision.text.strip()}")
    parts.append(
        "请返回严格 JSON，不要 Markdown，不要代码块。格式："
        '{"text":"1-3句、120字内中文自然首响",'
        '"response_contract":{"type":"quick_answer|research|verification|dispatch|clarification|blocker",'
        '"next_step":"下一条正式回复必须兑现的动作"}}。'
        "text 要讲清你理解了什么、先怎么处理、下一条会补什么证据；不要用标题、"
        "项目符号、字段标签；不要说收到、排队中、马上处理这种空话；不要承诺已完成/"
        "已验收；不得扩写原话里没有出现的具体任务名或对象。"
        "contract 只选一个 type；next_step 不超过 40 字，必须具体到下一步动作。"
    )
    return "\n\n".join(parts)


def _recent_context_for_prompt(decision: Decision) -> str:
    """Return a compact local conversation tail for first responses.

    Feishu `reply_context` only exists when the boss used an explicit reply.
    Real chats often use follow-ups like "B", "继续", or "对比一下 dev 分支";
    without a short local tail the fast model treats them as isolated text and
    asks for information the team already has. Keep this intentionally small:
    recent boss messages plus recent manager public conclusions are enough to
    resolve most pronouns/options/branch references without turning the
    10-second path into a long context load.
    """
    max_chars = int(tunables.tunable(
        "router.first_response.recent_context_chars", 1200))
    if max_chars <= 0:
        return ""
    snippets: list[str] = []
    snippets.extend(_recent_boss_message_snippets(decision))
    snippets.extend(_recent_manager_say_snippets())
    if not snippets:
        return ""
    text = "\n".join(snippets)
    if len(text) <= max_chars:
        return text
    keep = max_chars - 16
    return "（前文截断）\n" + text[-keep:].lstrip()


def _recent_boss_message_snippets(decision: Decision) -> list[str]:
    try:
        rows = local_facts.list_messages("manager")
    except Exception:
        return []
    rows = sorted(rows, key=lambda r: int(r.get("created_at") or 0))
    out: list[str] = []
    skipped_current = False
    current = _compact_line(decision.text)
    for row in reversed(rows):
        if row.get("from") != "user":
            continue
        text = _compact_line(row.get("content"))
        if not text:
            continue
        if (not skipped_current
                and current
                and text == current):
            skipped_current = True
            continue
        out.append(f"- 老板上一条：{_clip_prompt_text(text, 220)}")
        if len(out) >= 3:
            break
    out.reverse()
    return out


def _recent_manager_say_snippets() -> list[str]:
    try:
        rows = local_facts.list_logs("manager", limit=30)
    except Exception:
        return []
    out: list[str] = []
    for row in reversed(rows):
        if row.get("type") != "say":
            continue
        text = _compact_line(row.get("content"))
        if not text:
            continue
        out.append(f"- manager 刚才结论：{_clip_prompt_text(text, 320)}")
        if len(out) >= 3:
            break
    out.reverse()
    return out


def _compact_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _clip_prompt_text(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def _parse_model_payload(raw: str, decision: Decision) -> tuple[str, dict]:
    text = str(raw or "").strip()
    data = _parse_json_object(text)
    if not isinstance(data, dict):
        return text, _fallback_contract(text, decision)
    visible = (
        data.get("text")
        or data.get("message")
        or data.get("reply")
        or data.get("content")
        or ""
    )
    contract = data.get("response_contract") or data.get("contract") or {}
    return str(visible or text), contract if isinstance(contract, dict) else {}


def _parse_json_object(raw: str) -> dict | None:
    text = _strip_code_fence(raw)
    candidates = [text]
    start = text.find("{")
    if start > 0:
        candidates.append(text[start:])
    decoder = json.JSONDecoder()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            data, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _strip_code_fence(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _normalize_contract(raw: dict | None, fallback_text: str,
                        decision: Decision) -> dict[str, str]:
    data = raw if isinstance(raw, dict) else {}
    raw_type = str(data.get("type") or "").strip()
    ctype = raw_type if raw_type in VALID_CONTRACT_TYPES else _infer_contract_type(decision, fallback_text)
    next_step = _clean_contract_step(data.get("next_step") or "")
    if not next_step:
        next_step = _DEFAULT_NEXT_STEP.get(ctype, _DEFAULT_NEXT_STEP["quick_answer"])
    return {
        "type": ctype,
        "next_step": next_step,
    }


def _fallback_contract(fallback_text: str, decision: Decision) -> dict[str, str]:
    ctype = _infer_contract_type(decision, fallback_text)
    return {
        "type": ctype,
        "next_step": _DEFAULT_NEXT_STEP.get(ctype, _DEFAULT_NEXT_STEP["quick_answer"]),
    }


def _infer_contract_type(decision: Decision, fallback_text: str = "") -> str:
    text = f"{decision.reply_context or ''}\n{decision.text or ''}\n{fallback_text or ''}"
    low = text.lower()
    if any(tok in text for tok in ("刘小排", "亦仁", "导师", "资料", "理论", "案例", "调研", "研究", "论证")):
        return "research"
    if any(tok in text for tok in ("派", "分配", "员工", "岗位", "主管", "调配", "切换", "负责人")):
        return "dispatch"
    if any(tok in text for tok in ("验收", "验证", "核对", "确认", "排查", "测试", "日志", "耗时", "数据", "证据", "截图")):
        return "verification"
    if any(tok in text for tok in ("卡住", "卡点", "阻塞", "不行", "失败", "报错", "无法", "不能", "超时")):
        return "blocker"
    if "?" in text or "？" in text or any(tok in text for tok in ("哪些", "哪个", "是否", "要不要", "什么意思")):
        return "clarification"
    if any(tok in low for tok in ("research", "source", "cite", "benchmark")):
        return "research"
    if any(tok in low for tok in ("verify", "test", "log", "latency", "evidence")):
        return "verification"
    if any(tok in low for tok in ("dispatch", "owner", "assign", "worker")):
        return "dispatch"
    if any(tok in low for tok in ("blocker", "blocked", "timeout", "error")):
        return "blocker"
    return "quick_answer"


def _clean_contract_step(value: object) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) > 80:
        text = text[:80].rstrip("，,。 ") + "。"
    return text


def _join_api_path(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    suffix = "/" + path.strip("/")
    if base.endswith("/v1"):
        return base + suffix
    return base + "/v1" + suffix


def _post_json(url: str, payload: dict, headers: dict, timeout_s: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = dict(headers)
    # Some provider gateways block Python urllib's default User-Agent with a
    # Cloudflare 1010 before the request reaches the model API. curl succeeds,
    # so use a conservative client UA for this latency-sensitive direct path.
    headers.setdefault("user-agent", "curl/8.7.1")
    headers.setdefault("accept", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"http {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(str(e)) from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"invalid json response: {e}") from e


def _parse_anthropic_text(data: dict) -> str:
    chunks = []
    for item in data.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text") or ""))
    if chunks:
        return "\n".join(chunks)
    return str(data.get("text") or "")


def _parse_openai_responses_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            chunks.append(content)
            continue
        for part in content or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"}:
                chunks.append(str(part.get("text") or ""))
    if chunks:
        return "\n".join(chunks)
    return str(data.get("text") or "")


def _parse_openai_chat_text(data: dict) -> str:
    chunks: list[str] = []
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        msg = choice.get("message") or {}
        if isinstance(msg, dict) and msg.get("content"):
            chunks.append(str(msg.get("content") or ""))
        elif choice.get("text"):
            chunks.append(str(choice.get("text") or ""))
    if chunks:
        return "\n".join(chunks)
    return str(data.get("text") or "")


def _clean_model_text(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    prefixes = ("首响：", "回复：", "老板：")
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].lstrip()
    max_chars = int(tunables.tunable("router.first_response.max_chars", 180))
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip("，,。 ") + "。"
    return cleaned


def _is_status_probe_request(decision: Decision) -> bool:
    text = f"{decision.reply_context or ''}\n{decision.text or ''}".casefold()
    return any(marker in text for marker in _STATUS_PROBE_MARKERS)


def _has_concrete_status_probe_evidence(text: str) -> bool:
    low = str(text or "").casefold()
    return (
        any(marker in low for marker in _STATUS_PROBE_ACTION_MARKERS)
        and any(marker in low for marker in _STATUS_PROBE_EVIDENCE_MARKERS)
    )


def _first_response_quality_error(decision: Decision, text: str) -> str:
    if _is_status_probe_request(decision) and not _has_concrete_status_probe_evidence(text):
        return "status probe first response lacks concrete evidence"
    return ""


def _log_failure(decision: Decision, local_id: str, error: str, elapsed_ms: int,
                 result: FirstResponseResult) -> None:
    local_facts.append_log(
        "manager", "first_response_failed",
        (f"trace={local_id or decision.msg_id}; msg_id={decision.msg_id}; "
         f"elapsed_ms={elapsed_ms}; provider={result.provider}; "
         f"model={result.model}; error={error}"),
        ref=local_id or decision.msg_id,
    )
