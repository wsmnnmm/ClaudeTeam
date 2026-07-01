"""`claudeteam router`

Long-running event subscriber: spawns the `@larksuite/channel` sidecar
(`node scripts/feishu_channel/sidecar.js run`, the official WebSocket →
NDJSON ingress) and feeds each NDJSON line into the routing loop
(`feishu/subscribe.process_lines`).

Boot order:
  1. Validate chat_id + agents (fast-fail BEFORE pidlock so up.py
     can detect "no pid written" and surface the boot error).
  2. Acquire `state_dir/router.pid` via pidlock so two routers
     can't fight.
  3. Replay `pending_lines(chat_id)` to backfill anything received
     while the daemon was down (catchup-on-restart cursor).
  4. Spawn the subscribe subprocess in its own session (so
     SIGTERMing the daemon kills the entire node sidecar
     tree via killpg).
  5. Spawn a daemon thread that polls the subscribe child's exit
     code every ~20s and self-SIGTERMs when it dies (lark-cli
     occasionally exits silently while npm-exec parent keeps
     stdout open, blocking readline forever).
  6. Drive `process_lines` over the subscribe stdout iterator.

Stops on:
  - Ctrl-C → SIGINT
  - SIGTERM → handler reaps subscribe group, releases pidlock, exit 0
  - subscribe child dies → watchdog thread SIGTERMs us; same cleanup.

Writes pid to `state_dir/router.pid` so `runtime.watchdog.is_alive`
can supervise. Watchdog separately reaps orphan `+subscribe`
processes left by a SIGKILL'd predecessor before respawning.
"""
from __future__ import annotations

import html
import collections
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from claudeteam.feishu import base_intake, catchup, chat as _chat, lark
from claudeteam.feishu.deliver import apply as _deliver_apply
from claudeteam.feishu.media import download_message_resource
from claudeteam.feishu.subscribe import _extract_text, process_lines
from claudeteam.runtime import config, paths, pidlock, tunables, wake
from claudeteam.store import local_facts
from claudeteam.util import error_exit, maybe_print_help, warn


def _subscribe_event_types() -> str:
    types = ["im.message.receive_v1"]
    if base_intake.enabled():
        for event_type in base_intake.event_types():
            if event_type not in types:
                types.append(event_type)
    return ",".join(types)


def _build_subscribe_cmd(profile: str = "", *,
                         sidecar=lark.sidecar_path) -> list[str]:
    """Build the Feishu event-ingress argv.

    The sidecar opens the official long-connection WebSocket and emits each
    inbound message as one NDJSON line in the lark-cli `--compact` flat shape
    that `feishu.subscribe.process_lines` already parses — so the whole
    routing loop downstream is unchanged. App creds reach it via
    `lark.subprocess_env()` (the same env the Popen uses), not the argv.

    Tests inject `sidecar=` so the argv is deterministic. `profile` is unused
    now (the sidecar binds to the resolved app creds, not a lark-cli profile)
    but kept in the signature so the daemon call site stays as-is.

    Replaces the former `lark-cli event +subscribe` argv: that path silently
    dropped its WebSocket on macOS and split events across connections under
    the old `--force`; the SDK long-connection is the supported ingress.
    """
    return ["node", str(sidecar()), "run"]


def _build_agent_adapters(agents_dict: dict) -> dict:
    """Resolve every team-known agent to its CliAdapter once.

    Pre-building this map keeps `_inject_to_pane`'s per-target adapter
    lookup disk-read-free for cached agents. Adapters whose `cli`
    value is bogus get skipped (no entry); the apply call falls back
    to the config-driven lookup which surfaces the KeyError as a
    per-agent warning instead of a build-time abort.
    """
    from claudeteam.agents import get_adapter
    adapters: dict = {}
    for name, cfg in agents_dict.items():
        cli = cfg.get("cli", "claude-code")
        try:
            adapters[name] = get_adapter(cli)
        except KeyError:
            continue
    return adapters


def _make_apply_with_wake(*, session: str, chat_id: str, profile: str,
                          team_agents: list[str], agent_adapters: dict,
                          lazy_agents: frozenset):
    """Build the per-event deliver wrapper with hot-path config pre-bound.

    chat_id / lark_profile / session are deployment-stable; binding
    them in a closure here saves 2-4 disk reads per inbound message
    compared to letting `deliver.apply` re-resolve via `config.<getter>()`
    each time. The pre-built `agent → CliAdapter` map plays the same
    role for `_inject_to_pane` — unknown agents (not in the cached
    map) fall back to a config-driven lookup so a typo surfaces as a
    per-agent warning instead of dropping the whole event.

    Operator edits to `chat_id` need a `claudeteam down + up` to take
    effect (subscribe is bound to the startup chat_id, pidlock
    prevents a parallel daemon). Per-agent fields like `lazy` /
    `card_color` / `specialty` ARE live-readable through other code
    paths (slash handlers via `_live_agents()`, identity via
    `claudeteam reidentify`).
    """
    def lookup_adapter(agent: str):
        cached = agent_adapters.get(agent)
        if cached is not None:
            return cached
        from claudeteam.agents import adapter_for_agent
        return adapter_for_agent(agent)

    def _apply_with_wake(decision):
        return _deliver_apply(decision, wake_fn=wake.wake_if_dormant,
                              session=session, chat_id=chat_id,
                              profile=profile, team_agents=team_agents,
                              lazy_agents=lazy_agents,
                              adapter_for_agent=lookup_adapter)
    return _apply_with_wake


def _clip(text: str, limit: int = 700) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def _list_recent_as_user() -> bool:
    legacy = os.environ.get("CLAUDETEAM_LARK_SEND_AS", "").strip().lower()
    if legacy:
        return legacy != "bot"
    return str(tunables.tunable("feishu.send_as", "user")).lower() != "bot"


def _reply_candidate_ids(event: dict) -> list[str]:
    fields = (
        "reply_to",
        "parent_id",
        "parent_message_id",
        "reply_to_id",
        "reply_message_id",
        "quote_message_id",
        "quoted_message_id",
        "root_id",
        "thread_id",
    )
    raw = []
    for field in fields:
        raw.append(event.get(field, ""))
    raw.extend(event.get("reply_lookup_ids") or [])
    seen: set[str] = set()
    out: list[str] = []
    for value in raw:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _card_text_from_content(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        return ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return ""
    bits: list[str] = []
    header = data.get("header") if isinstance(data, dict) else {}
    if isinstance(header, dict):
        title = header.get("title") or {}
        if isinstance(title, dict):
            bits.append(str(title.get("content") or title.get("text") or ""))
    body = data.get("body") if isinstance(data, dict) else {}
    elements = body.get("elements") if isinstance(body, dict) else []
    bits.extend(_card_element_text(elements))
    return _clip(html.unescape("\n".join(bit for bit in bits if bit).strip()), 1200)


def _card_element_text(value) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_card_element_text(item))
        return out
    if not isinstance(value, dict):
        return []
    out = []
    for key in ("content", "text"):
        text = str(value.get(key) or "").strip()
        if text:
            out.append(text)
    for key in ("elements", "columns"):
        out.extend(_card_element_text(value.get(key)))
    return out


def _message_text_from_recent_row(row: dict) -> str:
    content = catchup._extract_content(row)
    msg_type = str(row.get("msg_type") or row.get("message_type") or "text")
    message_id = str(row.get("message_id") or "")
    if msg_type == "interactive":
        text = _card_text_from_content(content)
        if text:
            return text
    text = _extract_text(content, msg_type, message_id=message_id)
    return _clip(text or str(content or ""), 1200)


def _reply_context_from_row(row: dict) -> str:
    sender = row.get("sender") or {}
    sender_name = str(
        sender.get("name")
        or sender.get("id")
        or sender.get("sender_type")
        or "unknown"
    )
    text = _clip(_message_text_from_recent_row(row), 700)
    if not text:
        text = "(父消息无可解析文本，可能是图片/卡片/已删除消息)"
    return "\n".join([
        "[飞书回复上下文]",
        "这条老板消息是在回复上一条消息，不是孤立提问。",
        f"- 父消息 id: {row.get('message_id') or ''}",
        f"- 父消息发送者: {sender_name}",
        f"- 父消息类型: {row.get('msg_type') or row.get('message_type') or 'unknown'}",
        f"- 父消息摘要: {text}",
        "答复时必须先解释父消息/被回复内容，再回答老板新问句。",
    ])


def _make_reply_context_resolver(chat_id: str, profile: str) -> Callable:
    cache: dict[str, dict] = {}
    loaded = False

    def _add_row(row: dict) -> None:
        message_id = str(row.get("message_id") or "").strip()
        if message_id and message_id not in cache:
            cache[message_id] = row

    def _load_local_rows() -> None:
        for row in _local_reply_rows():
            _add_row(row)

    def _load_recent_rows(as_user: bool) -> None:
        for row in _chat.list_recent(
            chat_id, page_size=50, profile=profile, as_user=as_user,
        ):
            _add_row(row)

    def _resolver(event: dict) -> str:
        nonlocal loaded
        candidates = _reply_candidate_ids(event)
        child_id = str(
            event.get("message_id") or event.get("msg_id") or ""
        ).strip()
        if not candidates and not child_id:
            return ""
        if not loaded:
            loaded = True
            _load_local_rows()
            primary_as_user = _list_recent_as_user()
            try:
                _load_recent_rows(primary_as_user)
            except Exception as e:
                warn(f"⚠️  reply-context recent fetch failed: {e}")
            if not any(cache.get(candidate) for candidate in candidates):
                try:
                    _load_recent_rows(not primary_as_user)
                except Exception as e:
                    warn(f"⚠️  reply-context fallback fetch failed: {e}")
        if not candidates and child_id and cache.get(child_id):
            candidates = _reply_candidate_ids(cache[child_id])
        if not candidates:
            return ""
        row = next((cache.get(candidate) for candidate in candidates
                    if cache.get(candidate)), None)
        if row:
            return _reply_context_from_row(row)
        reply_to = candidates[0]
        return "\n".join([
            "[飞书回复上下文]",
            "这条老板消息是在回复上一条消息，不是孤立提问。",
            f"- 父消息 id: {reply_to}",
            f"- 已尝试候选 id: {', '.join(candidates)}",
            "- 父消息摘要: 飞书最近历史和本地发送日志都未取到，可能太旧、历史权限不足或事件只给了线程根。",
            "答复时必须先说明未取到被回复内容，并用最近消息/任务/记忆核对后再回答。",
        ])

    return _resolver


def _local_reply_rows() -> list[dict]:
    rows: list[dict] = []
    try:
        logs = local_facts.list_logs("manager", limit=500)
    except Exception:
        return rows
    for log in logs:
        kind = str(log.get("type") or "")
        content = str(log.get("content") or "")
        if kind == "first_response_sent":
            row = _first_response_log_row(content)
            if row:
                rows.append(row)
        elif kind == "say":
            message_id = str(log.get("ref") or "").strip()
            if message_id:
                rows.append(_text_row(
                    message_id,
                    content,
                    sender_name="manager",
                    sender_type="app",
                ))
    return rows


def _first_response_log_row(content: str) -> dict:
    message_match = re.search(r"(?:^|;\s*)response_message_id=([^;]+)", content)
    if not message_match:
        return {}
    message_id = message_match.group(1).strip()
    text_marker = "; text="
    text = ""
    if text_marker in content:
        text = content.split(text_marker, 1)[1].strip()
    return _text_row(message_id, text, sender_name="manager", sender_type="app")


def _text_row(message_id: str, text: str, *,
              sender_name: str, sender_type: str) -> dict:
    return {
        "message_id": message_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "sender": {
            "name": sender_name,
            "id": sender_name,
            "sender_type": sender_type,
        },
    }


def _terminate_subscribe_group(proc: subprocess.Popen) -> None:
    """Kill the entire subscribe process group (npx + node + lark-cli).

    A plain proc.terminate() only signals npx; the lark-cli grandchild
    then lives on as an orphan after each up/down cycle.
    Putting the subprocess in its own session (start_new_session=True at
    Popen time) means we can take the whole group out with one killpg.
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _load_seen_msg_ids() -> set[str]:
    """Load persisted dedup set from disk, truncating to
    `router.seen_max_lines` (claudeteam.toml; default 5000) to bound the
    file. Returns empty set if missing or unreadable — best-effort,
    never fails the daemon."""
    path = paths.router_seen_file()
    try:
        if not path.exists():
            return set()
        with path.open("r", encoding="utf-8") as f:
            ids = [line.strip() for line in f if line.strip()]
    except OSError:
        return set()
    seen_max = int(tunables.tunable("router.seen_max_lines", 5000))
    if len(ids) > seen_max:
        # Truncate file in place so it doesn't grow unbounded.
        try:
            kept = ids[-seen_max:]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            ids = kept
        except OSError:
            pass
    return set(ids)


def _make_on_progress(last_transport_ok_at: list[float],
                      events_seen: list[int] | None = None) -> Callable:
    """Build the on_progress callback bound to a mutable timestamp slot.

    Every successfully handled event, plus every classified permanent DROP:
    - refreshes `last_transport_ok_at[0]` so the watchdog knows the
      message transport is making progress. The catchup heartbeat also
      refreshes this when the REST poll succeeds with no messages.
    - appends the message_id to `state/router.seen` so the dedup set
      survives across process restarts. Without this, router self-
      restarts (driven by stale-detect or watchdog) re-apply messages
      that catchup re-fetches because seen_msg_ids was an in-memory
      set (e.g. a /tmux manager card forwarded into the manager inbox
      every ~3.5min on every restart cycle).
    """
    def _on_progress(decision, stats):
        catchup.record_decision(decision)
        last_transport_ok_at[0] = time.monotonic()
        if events_seen is not None:
            events_seen[0] += 1
        msg_id = getattr(decision, "msg_id", "")
        if msg_id:
            try:
                seen_path = paths.router_seen_file()
                seen_path.parent.mkdir(parents=True, exist_ok=True)
                with seen_path.open("a", encoding="utf-8") as f:
                    f.write(msg_id + "\n")
            except OSError:
                pass  # best-effort; in-memory set still dedups in this run
    return _on_progress


def _platform_default_stale_event_threshold_s() -> float:
    """Default stale-event threshold split by platform — root cause
    of the previous 180/600 churn was platform-specific WebSocket
    behaviour, not a single-knob tuning problem.

    macOS (Darwin) → 120s. lark-cli 1.0.23 WebSocket subscribe silently
    drops on macOS without reconnecting (the subscribe child stays
    alive but stops delivering events; only self-SIGTERM + watchdog
    respawn + catchup recovers). A tighter
    threshold lets recovery happen in ~2 min instead of ~10. Quiet-chat
    overhead is acceptable on a dev laptop.

    Linux (and everything else) → 600s. WebSocket is stable here; quiet
    chats shouldn't churn through respawns. History on this platform:
    1200s → too lax (manager not seeing a user msg for 7+ min); 180s →
    too tight (a genuinely quiet chat respawned every ~3 min, churning
    router.log into a wall of "no events for 180s; respawning"). 600s is the
    calibrated middle.
    """
    import platform
    return 120.0 if platform.system() == "Darwin" else 600.0


def _stale_event_threshold_s() -> float:
    """Max seconds router will tolerate with no transport heartbeat before
    self-SIGTERM'ing for a watchdog respawn.

    Resolved via runtime.tunables — priority env > claudeteam.toml >
    platform-aware default (see `_platform_default_stale_event_threshold_s`).
    Legacy `CLAUDETEAM_ROUTER_STALE_S` env (without `_EVENT_THRESHOLD`) is
    still honored as a backwards-compat alias since it shipped first.
    """
    # Legacy env-var alias (shipped before the tunables framework).
    legacy = os.environ.get("CLAUDETEAM_ROUTER_STALE_S", "").strip()
    if legacy:
        try:
            v = float(legacy)
            if v > 0:
                return v
        except ValueError:
            pass
    return float(tunables.tunable(
        "router.stale_event_threshold_s",
        _platform_default_stale_event_threshold_s()))


def _subscribe_rotate_reason(idle: float, threshold: float,
                             events_seen: int) -> str:
    """Return the router log line before self-SIGTERM for subscribe rotation."""
    if events_seen == 0:
        return (f"  ℹ️ no live events for {idle:.0f}s — rotating subscribe "
                f"(none inbound yet this session; on macOS the WebSocket often "
                f"goes quiet, catchup refetches on restart)")
    return (f"  ⚠️ live events stopped after {idle:.0f}s idle "
            f"(threshold {threshold:.0f}s) — rotating subscribe for respawn")


def _catchup_poll_interval_s() -> float:
    """Seconds between explicit REST catchup heartbeats.

    This turns `event +subscribe` into the low-latency fast path rather
    than the only path. If the WebSocket silently stalls, boss messages
    are still found by `chat-messages-list` on this cadence.
    """
    return float(tunables.tunable("router.catchup_poll_interval_s", 30.0))


def _run_catchup_once(chat: str, profile: str, loop_kwargs: dict, *,
                      label: str = "catchup"):
    """Fetch and process missed messages once.

    Returns LoopStats on success, None on fetch/process failure. Kept as
    a small helper so the startup catchup and background heartbeat share
    identical error handling and tests can exercise the catchup fast path
    without starting the long-running router daemon.
    """
    try:
        pending = catchup.pending_lines(chat, profile=profile)
    except Exception as e:
        warn(f"⚠️  {label} fetch failed: {e}")
        return None
    try:
        stats = process_lines(iter(pending), **loop_kwargs)
    except Exception as e:
        warn(f"⚠️  {label} process failed: {e}")
        return None
    non_dedup_drops = stats.dropped - stats.drops_by_reason.get("dedup", 0)
    if pending and (stats.handled or non_dedup_drops):
        print(
            f"📥 {label}: replayed {len(pending)} candidate(s); "
            f"handled={stats.handled} dropped={stats.dropped}")
    return stats


def _watch_catchup_heartbeat(chat: str, profile: str, loop_kwargs: dict,
                             stop_event: threading.Event,
                             last_transport_ok_at: list[float],
                             last_subscribe_line_at: list[float]) -> None:
    """Background REST heartbeat + low-latency backfill loop.

    Why this exists: a live-looking lark-cli subscribe process can stop
    delivering events. Waiting for "no events for N seconds" makes the
    boss experience depend on a stale threshold. This loop actively asks
    Feishu for recent messages; if it finds one the stream missed, it
    processes it immediately, then restarts the router so the WebSocket
    fast path reconnects.
    """
    interval_s = _catchup_poll_interval_s()
    if interval_s <= 0:
        return
    failures = 0
    consecutive_miss_hits = 0
    failure_reconnect_count = int(tunables.tunable(
        "router.catchup_failure_reconnect_count", 3))
    miss_reconnect_count = int(tunables.tunable(
        "router.catchup_miss_reconnect_count", 1))
    restart_on_miss = bool(tunables.tunable(
        "router.restart_on_catchup_miss", True))
    miss_grace_s = float(tunables.tunable(
        "router.catchup_miss_reconnect_grace_s", 5.0))

    while not stop_event.wait(interval_s):
        idle_before = time.monotonic() - last_subscribe_line_at[0]
        stats = _run_catchup_once(chat, profile, loop_kwargs,
                                  label="catchup heartbeat")
        if stats is None:
            failures += 1
            # Transport failure: don't credit a "miss" to the subscribe
            # fast path. The miss counter only tracks real catchup hits;
            # transport-level issues have their own failure_reconnect
            # escalation path above.
            consecutive_miss_hits = 0
            if (failure_reconnect_count > 0
                    and failures >= failure_reconnect_count):
                print(
                    "  ⚠️ catchup heartbeat failed "
                    f"{failures} time(s); router will reconnect")
                os.kill(os.getpid(), signal.SIGTERM)
                return
            continue

        failures = 0
        last_transport_ok_at[0] = time.monotonic()
        if stats.handled <= 0:
            # No missed messages this tick → reset the counter. A heartbeat
            # that simply had nothing to catch up is a healthy read, not
            # a sustained subscribe fast-path lag. The threshold
            # miss_reconnect_count only escalates when the fast path
            # *actually* missed messages for K consecutive heartbeats.
            consecutive_miss_hits = 0
            continue
        consecutive_miss_hits += 1
        print(
            "  ⚠️ catchup heartbeat handled "
            f"{stats.handled} missed message(s); subscribe fast path lagged")
        if (restart_on_miss
                and idle_before >= miss_grace_s
                and consecutive_miss_hits >= max(1, miss_reconnect_count)):
            print("  🔁 restarting router to reconnect subscribe fast path")
            os.kill(os.getpid(), signal.SIGTERM)
            return


_WS_FAIL_MARKERS = (
    "ws connect failed",
    "connect failed",
    "reconnect",
    "persistent connection",
    "长连接",
)


def _diagnose_sidecar_exit(returncode, recent_lines) -> None:
    """Print the sidecar tail and common WebSocket setup hints, best-effort."""
    try:
        tail = [line for line in list(recent_lines or []) if line.strip()][-12:]
    except RuntimeError:
        tail = []
    if tail:
        print("     ↳ sidecar 最后输出：")
        for line in tail:
            print(f"       {line}")
    blob = "\n".join(tail).lower()
    if any(marker in blob for marker in _WS_FAIL_MARKERS):
        print("     ↳ 诊断：sidecar 的 WebSocket(长连接)没建起来。最常见两条：")
        print("       1) 该应用没开长连接订阅 → 飞书开发者后台 → 事件与回调 → 订阅方式")
        print("          → 改成「使用长连接接收事件/回调」(不是 Webhook URL)，保存。")
        print("       2) HTTPS_PROXY 挡了 WebSocket → 启动前 `export LARK_CLI_NO_PROXY=1`")
        print("          (或写进 $CLAUDETEAM_SECRETS_FILE / shell profile)。")


def _tee_recent(stream, sink):
    """Yield stream lines while keeping a bounded raw-output tail in sink."""
    for line in stream:
        sink.append(line.rstrip("\n"))
        yield line


def _watch_subscribe_health(proc: subprocess.Popen, stop_event: threading.Event,
                            last_transport_ok_at: list[float],
                            events_seen: list[int],
                            recent_lines=None) -> None:
    """Background thread: kill the daemon if the subscribe child dies OR
    all message-transport heartbeats stop.

    Two failure modes covered:

    (a) `lark-cli event +subscribe` exits silently — the lark-cli
        grandchild can vanish while npm-exec parent stays running.
        With npm-exec still holding stdout open, the main thread's
        `process_lines(proc.stdout, ...)` would block forever on
        readline, never noticing.

    (b) Neither subscribe stdout nor the REST catchup heartbeat has
        shown life for the threshold window. Quiet chats are no longer
        mistaken for dead subscriptions because successful catchup
        polls update `last_transport_ok_at`.

    Both modes terminate via SIGTERM-to-self so the registered handler
    reaps the subscribe group cleanly. Watchdog respawns from there.
    """
    threshold = _stale_event_threshold_s()
    # Short enough to detect a silent subscribe death in <30s, long
    # enough not to busy-loop. Toml-overridable via
    # router.subscribe_watchdog_period_s.
    period_s = float(tunables.tunable("router.subscribe_watchdog_period_s", 20.0))
    while not stop_event.wait(period_s):
        if proc.poll() is not None:
            print(f"  ⚠️ subscribe child exited (rc={proc.returncode}); router will exit so watchdog can respawn")
            _diagnose_sidecar_exit(proc.returncode, recent_lines)
            os.kill(os.getpid(), signal.SIGTERM)
            return
        idle = time.monotonic() - last_transport_ok_at[0]
        if idle > threshold:
            print(_subscribe_rotate_reason(idle, threshold, events_seen[0]))
            os.kill(os.getpid(), signal.SIGTERM)
            return


def _catchup_slash_fresh_ms() -> int:
    """Freshness threshold (ms) below which a catchup-delivered slash is
    treated as a live WS-fallback command (dispatch) rather than a stale
    backlog replay (suppress). Tunable; default 180s. <=0 is clamped to the
    default so a misconfig can't make everything 'stale' and re-eat slashes."""
    from claudeteam.runtime import tunables
    try:
        v = int(tunables.tunable("router.catchup_slash_fresh_ms", 600_000))
    except (TypeError, ValueError):
        v = 600_000
    return v if v > 0 else 180_000


def _notify_catchup_skips(default_target: str, *, dropped_stale: int,
                          slash_skipped: int) -> None:
    """After catchup, tell the routing-target agent (default manager) if the
    replay skipped anything — over-cap stale messages or un-replayed control
    commands — so it doesn't over-claim it received the whole backlog.
    No-op when nothing was skipped. Best-effort: a
    notice-write failure must never break bring-up."""
    if dropped_stale <= 0 and slash_skipped <= 0:
        return
    parts = []
    if dropped_stale > 0:
        parts.append(f"{dropped_stale} 条超上限的陈旧历史消息未重放")
    if slash_skipped > 0:
        parts.append(f"{slash_skipped} 条控制命令(/...)按规则未重放")
    note = ("⚠️ 恢复(catchup)时跳过了部分历史：" + "；".join(parts)
            + "。本次你**未必收到完整 backlog**——涉及老板原话/约束请 "
              "`task intent get` 现读或请老板重发，别按记忆脑补。")
    try:
        from claudeteam.store import local_facts
        local_facts.append_message(default_target, "router", note, priority="高")
    except Exception as e:
        warn(f"⚠️ catchup skip-notice failed: {e}")


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam router"):
        return 0

    chat = config.chat_id()
    if not chat:
        return error_exit("❌ chat_id not set in runtime_config.json")

    agents = config.agent_names()
    if not agents:
        return error_exit("❌ claudeteam.toml has no agents")

    pid_file = paths.router_pid_file()
    if not pidlock.acquire(pid_file, name="router"):
        return 1

    profile = config.lark_profile()
    cmd = _build_subscribe_cmd(profile)
    print(f"🚀 router subscribing on chat {chat} (profile={profile or '<default>'})")

    proc = None
    stop_watchdog = None
    last_transport_ok_at = [time.monotonic()]
    last_subscribe_line_at = [time.monotonic()]
    events_seen = [0]
    recent_lines: collections.deque = collections.deque(maxlen=30)
    try:
        # Bind deployment-stable config values into apply_fn at daemon
        # startup so deliver.apply doesn't re-resolve them on every
        # inbound event (saves 1-4 disk reads per message). The
        # agent→adapter map plays the same role for the inject path.
        # `lazy_agents` is still pre-computed and threaded into
        # SlashContext for back-compat, but slash handlers now use
        # `_live_agents()` themselves so config edits are live.
        team_data = config.load_team()
        agents_dict = team_data.get("agents", {})
        apply_fn = _make_apply_with_wake(
            session=team_data.get("session", "ClaudeTeam"),
            chat_id=chat,
            profile=profile,
            team_agents=agents,
            agent_adapters=_build_agent_adapters(agents_dict),
            lazy_agents=frozenset(name for name, cfg in agents_dict.items()
                                  if cfg.get("lazy")),
        )

        # Persisted dedup set — survives daemon restarts so catchup
        # replay after stale-detect / watchdog respawn doesn't re-apply
        # already-handled messages.
        seen = _load_seen_msg_ids()
        event_lock = threading.Lock()

        def _bump_subscribe_alive():
            now = time.monotonic()
            last_transport_ok_at[0] = now
            last_subscribe_line_at[0] = now

        base_loop_kwargs = dict(
            team_agents=agents,
            chat_id=chat,
            default_target="manager",
            apply_fn=apply_fn,
            base_event_fn=(
                (lambda payload: base_intake.handle_payload(
                    payload, profile=profile))
                if base_intake.enabled() else None
            ),
            on_progress=_make_on_progress(last_transport_ok_at, events_seen),
            seen_msg_ids=seen,
            event_lock=event_lock,
            resource_downloader=(
                lambda message_id, resource_key, resource_type:
                    download_message_resource(
                        message_id,
                        resource_key,
                        resource_type,
                        profile=profile,
                        as_user=False,
                    )
            ),
            reply_context_resolver=_make_reply_context_resolver(chat, profile),
        )
        live_loop_kwargs = dict(base_loop_kwargs,
                                on_line_received=_bump_subscribe_alive)
        catchup_loop_kwargs = dict(base_loop_kwargs,
                                   on_line_received=None)

        # Fresh teams need an anchor for future heartbeat catchup. Without a
        # cursor, pending_lines intentionally refuses to replay history, but
        # that also means a stalled live subscribe can never recover the first
        # post-start message.
        catchup.bootstrap_cursor_if_empty()

        # Catchup: replay anything newer than the cursor before going live
        _run_catchup_once(chat, profile, catchup_loop_kwargs,
                          label="startup catchup")

        # Catchup can spend up to router.lark_call_timeout_s inside
        # chat-messages-list. Start the live-subscribe stale timer after
        # replay finishes, otherwise a slow catchup can make a healthy
        # fresh stream look idle immediately.
        now = time.monotonic()
        last_transport_ok_at[0] = now
        last_subscribe_line_at[0] = now

        # Two precautions on the subscribe child:
        # - env=lark.subprocess_env(profile=profile) strips HTTPS_PROXY under LARK_CLI_NO_PROXY=1
        #   (round 6 D-class bug — lark-cli long-poll dies behind a proxy).
        # - start_new_session=True puts the npx → node → lark-cli chain in its
        #   own process group so SIGTERMing the router can kill the whole tree
        #   in one killpg call (round 7 D2 — orphaned grandchildren).
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # line-buffered
                env=lark.subprocess_env(profile=profile),
                start_new_session=True,
            )
        except FileNotFoundError:
            return error_exit("❌ npx / lark-cli not found in PATH")

        # Now that proc exists, install a SIGTERM handler that reaps the
        # subscribe group before exiting. (Plain sys.exit propagates SystemExit
        # past the except blocks, never running proc.terminate.)
        def _on_sigterm(*_):
            _terminate_subscribe_group(proc)
            sys.exit(0)
        signal.signal(signal.SIGTERM, _on_sigterm)

        # Spawn the subscribe-health watchdog thread. It exits the daemon
        # cleanly if lark-cli dies under us — without it, process_lines would
        # block forever on stdout that npm-exec parent keeps open after the
        # lark-cli grandchild vanishes. Also self-terminates if events stop
        # flowing for too long (silent-subscribe-stall mode).
        stop_watchdog = threading.Event()
        threading.Thread(
            target=_watch_subscribe_health,
            args=(proc, stop_watchdog, last_transport_ok_at, events_seen,
                  recent_lines),
            daemon=True,
        ).start()
        threading.Thread(
            target=_watch_catchup_heartbeat,
            args=(chat, profile, catchup_loop_kwargs, stop_watchdog,
                  last_transport_ok_at, last_subscribe_line_at),
            daemon=True,
        ).start()

        if proc.stdout is None:
            return error_exit("❌ lark-cli started without stdout pipe")

        stats = process_lines(_tee_recent(proc.stdout, recent_lines),
                              **live_loop_kwargs)
        print(f"router exited: handled={stats.handled} dropped={stats.dropped}")
        return 0 if proc.wait() == 0 else 1
    except KeyboardInterrupt:
        print("router stopped (Ctrl-C)")
        return 0
    finally:
        # Reap the subscribe tree on EVERY exit path so we don't leak a
        # node + lark-cli pair per up/down cycle.
        if stop_watchdog is not None:
            stop_watchdog.set()
        if proc is not None:
            _terminate_subscribe_group(proc)
        pidlock.release(pid_file)
