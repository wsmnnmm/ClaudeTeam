"""`claudeteam traffic-brief` — lightweight traffic data assistant brief."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from claudeteam.commands import cockpit_sync
from claudeteam.util import (
    atomic_write_text, error_exit, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, read_jsonl, reject_extra_args, flock,
)


USAGE = """usage: claudeteam traffic-brief [--ledger <jsonl>] [--out <file>]
                                [--json] [--today <YYYY-MM-DD>]
                                [--max-rows <n>] [--append-json <json>]

Examples:
  claudeteam traffic-brief
  claudeteam traffic-brief --out runtime-health/traffic-brief.md
  claudeteam traffic-brief --ledger runtime-health/traffic/traffic-ledger.jsonl --json
  claudeteam traffic-brief --append-json '{"platform":"小红书","content":"深圳 AI 编程局","views":230}'
"""

DEFAULT_LEDGER = Path("runtime-health/traffic/traffic-ledger.jsonl")
DEFAULT_OUT = Path("runtime-health/traffic-brief.md")
NUMERIC_FIELDS = (
    "views", "comments", "private_messages", "add_wechat",
    "effective_leads",
)


def _parse_positive_int(raw: str | None, *, default: int, flag: str) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{flag} must be an integer") from None
    if value <= 0:
        raise ValueError(f"{flag} must be positive")
    return value


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(cockpit_sync._CST)).strftime("%Y-%m-%d")


def _text(record: dict[str, Any], key: str, default: str = "") -> str:
    value = record.get(key, default)
    return str(value or default).strip()


def _number(record: dict[str, Any], key: str) -> int:
    raw = record.get(key, 0)
    if raw in (None, ""):
        return 0
    try:
        return int(float(str(raw).strip()))
    except ValueError:
        return 0


def _normalise(record: dict[str, Any]) -> dict[str, Any]:
    out = {
        "date": _text(record, "date"),
        "platform": _text(record, "platform", "未知平台"),
        "content": _text(record, "content", "未命名内容"),
        "action": _text(record, "action", "未记录动作"),
        "source": _text(record, "source"),
        "notes": _text(record, "notes"),
    }
    for field in NUMERIC_FIELDS:
        out[field] = _number(record, field)
    return out


def _load_records(path: Path) -> list[dict[str, Any]]:
    return [_normalise(row) for row in read_jsonl(path) if isinstance(row, dict)]


def _append_record(path: Path, raw_json: str, *, today: str | None = None) -> None:
    try:
        record = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--append-json must be valid JSON: {exc.msg}") from None
    if not isinstance(record, dict):
        raise ValueError("--append-json must be a JSON object")
    normalized = _normalise(record)
    if not normalized["date"]:
        normalized["date"] = today or _today()
    path.parent.mkdir(parents=True, exist_ok=True)
    with flock(path.with_suffix(path.suffix + ".lock")):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n")


def _sum(records: list[dict[str, Any]], field: str) -> int:
    return sum(_number(record, field) for record in records)


def _record_line(record: dict[str, Any]) -> str:
    source = f"｜来源:{record['source']}" if record.get("source") else ""
    notes = f"｜{record['notes']}" if record.get("notes") else ""
    return (
        f"- {record['platform']}｜{record['content']}｜浏览 {record['views']} / "
        f"评论 {record['comments']} / 私信 {record['private_messages']} / "
        f"加微 {record['add_wechat']} / 有效线索 {record['effective_leads']}"
        f"{source}{notes}"
    )


def _anomalies(records: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for record in records:
        label = f"{record['platform']}《{record['content']}》"
        if record["views"] >= 100 and record["comments"] == 0:
            rows.append(f"{label}: 浏览不低但评论为 0，优先复核标题/首图是否吸引错人。")
        if record["private_messages"] > 0 and record["add_wechat"] == 0:
            rows.append(f"{label}: 有私信但未加微，老板优先看承接话术和回复时机。")
        if record["comments"] > 0 and record["private_messages"] == 0:
            rows.append(f"{label}: 有评论但无私信，补一条低风险评论区承接。")
    return rows[:5]


def _next_actions(today_records: list[dict[str, Any]], all_records: list[dict[str, Any]]) -> list[str]:
    if not today_records:
        return [
            "先别扩团队。今天只把小红书/抖音/视频号后台截图或口述数据发给 Hermes。",
            "记录字段只要：平台、内容、浏览、评论、私信、加微、有效线索、备注。",
            "老板只亲自做高价值私聊、最终发布和平台风险判断。",
        ]
    actions = []
    if _sum(today_records, "effective_leads") > 0:
        actions.append("先跟进有效线索，别继续沉迷改系统。")
    if _sum(today_records, "comments") == 0:
        actions.append("今天没有评论信号，明天先改选题/首图，不追播放量。")
    if _sum(today_records, "private_messages") > _sum(today_records, "add_wechat"):
        actions.append("有部分私信未转加微，老板复核承接话术，AI 只整理候选回复。")
    if not actions:
        actions.append("明天延续当前战场，只加一条新内容实验，不扩平台。")
    if len(all_records) < 3:
        actions.append("先凑满 3 天日报再谈流量团升级。")
    return actions[:4]


def build_brief(records: list[dict[str, Any]], *,
                today: str | None = None,
                now: datetime | None = None,
                max_rows: int = 6) -> dict[str, Any]:
    day = today or _today(now)
    today_records = [row for row in records if row.get("date") == day]
    latest = records[-max_rows:]
    visible_records = today_records[-max_rows:] if today_records else latest
    totals = {field: _sum(today_records, field) for field in NUMERIC_FIELDS}
    return {
        "generated_at": cockpit_sync._fmt_time(now or datetime.now(cockpit_sync._CST)),
        "today": day,
        "has_today": bool(today_records),
        "record_count": len(records),
        "today_count": len(today_records),
        "totals": totals,
        "records": visible_records,
        "anomalies": _anomalies(today_records),
        "next_actions": _next_actions(today_records, records),
    }


def render_markdown(brief: dict[str, Any]) -> str:
    totals = brief["totals"]
    lines = [
        "# 今日流量简报",
        "",
        f"更新: {brief['generated_at']}",
        f"日期: {brief['today']}",
        "",
        (
            "结论: "
            f"有效线索 {totals['effective_leads']} / 加微信 {totals['add_wechat']} / "
            f"私信 {totals['private_messages']} / 评论 {totals['comments']} / "
            f"浏览 {totals['views']}。"
        ),
        "",
    ]
    if not brief["has_today"]:
        lines.append(
            "今天还没有流量数据。这里的数据指你已发布内容后的后台数字；"
            "可以把后台截图或口述数字发给 Hermes，她只记账和出简报，不会登录平台操盘。"
        )
        if brief["records"]:
            lines.append("")
            lines.append("最近记录:")
    else:
        lines.append("今日记录:")
    for record in brief["records"]:
        lines.append(_record_line(record))
    lines.extend(["", "异常提醒:"])
    if brief["anomalies"]:
        lines.extend(f"- {item}" for item in brief["anomalies"])
    else:
        lines.append("- 暂无异常；别把播放量当唯一目标，先看评论、私信、加微和有效线索。")
    lines.extend(["", "下一步:"])
    lines.extend(f"- {item}" for item in brief["next_actions"])
    lines.extend([
        "",
        "边界: AI 数据助理只做记录、提醒、素材整理和复盘；不代替老板发内容、私信高价值用户或判断人设风险。",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    ledger_arg = pop_flag(rest, "--ledger")
    out_arg = pop_flag(rest, "--out")
    today_arg = pop_flag(rest, "--today")
    max_rows_arg = pop_flag(rest, "--max-rows")
    append_json_arg = pop_flag(rest, "--append-json")
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    try:
        max_rows = _parse_positive_int(max_rows_arg, default=6, flag="--max-rows")
    except ValueError as exc:
        return error_exit(f"❌ {exc}")
    ledger = Path(ledger_arg).expanduser().resolve() if ledger_arg else DEFAULT_LEDGER.resolve()
    out_path = Path(out_arg).expanduser().resolve() if out_arg else None
    if append_json_arg:
        try:
            _append_record(ledger, append_json_arg, today=today_arg)
        except (OSError, ValueError) as exc:
            return error_exit(f"❌ failed to append traffic record: {exc}")
    records = _load_records(ledger)
    brief = build_brief(records, today=today_arg, max_rows=max_rows)
    rendered = (
        json.dumps(brief, ensure_ascii=False, indent=2) + "\n"
        if as_json else render_markdown(brief)
    )
    if out_path:
        try:
            atomic_write_text(out_path, rendered)
        except OSError as exc:
            return error_exit(f"❌ failed to write traffic brief: {exc}")
    if as_json:
        print_json(brief)
    else:
        print(rendered, end="")
    return 0
