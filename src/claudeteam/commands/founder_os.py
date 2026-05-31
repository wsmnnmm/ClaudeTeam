"""`claudeteam founder-os` — AI-native startup stage gates for teams."""
from __future__ import annotations

import sys
from pathlib import Path

from claudeteam.commands import fleet_health
from claudeteam.runtime import team_registry
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag, print_json,
    read_json, reject_extra_args,
)


USAGE = """usage: claudeteam founder-os [--stage idea|mvp|launch|scale] [--json]
       claudeteam founder-os --audit-root <dir> [--json] [--registry-script <path>] [--no-registry] [team-dir ...]

Examples:
  claudeteam founder-os
  claudeteam founder-os --stage mvp
  claudeteam founder-os --json
  claudeteam founder-os --audit-root /Users/wsm/Project
"""

_STAGE_ALIASES = {
    "idea": "idea",
    "创意": "idea",
    "创意验证": "idea",
    "mvp": "mvp",
    "launch": "launch",
    "上线": "launch",
    "scale": "scale",
    "扩张": "scale",
}

_STAGES = [
    {
        "id": "idea",
        "label": "Idea / 创意验证",
        "goal": "证明问题真实、具体、频繁，并且有一群可触达的人正在痛。",
        "exit_gate": [
            "明确谁痛、多久痛、严重到什么程度、现在怎么凑合解决。",
            "完成反方论证：为什么这个想法会失败，谁会赢，用户为什么不买。",
            "拿到真实人类证据：访谈、帖子、竞品差评、付费/试用线索。",
        ],
        "ai_job": [
            "把模糊想法磨成可验证假设。",
            "做竞争格局、TAM/SAM/SOM、趋势顺逆风和买家地图。",
            "审访谈问题，去掉诱导性和面向未来的假问题。",
        ],
        "do_not": [
            "不要把能跑的 demo 当作验证。",
            "不要只让 AI 找支持证据。",
            "不要在证据撑不住时扩张任务范围。",
        ],
        "boss_question": "今天哪一个动作最能证明这个问题真的有人痛？",
        "artifact": "problem brief / 访谈记录 / 反方备忘录 / 竞品证据表",
    },
    {
        "id": "mvp",
        "label": "MVP / 最小可行",
        "goal": "用最小核心交互证明真实用户愿意用、回来、付费或推荐。",
        "exit_gate": [
            "提前写好 CLAUDE.md、范围文档、架构约束和不做清单。",
            "定义激活、留存、Day 7/Day 30、付费、推荐等 PMF 信号。",
            "上线给真实用户前完成安全 review 和数据暴露检查。",
        ],
        "ai_job": [
            "Claude Code 只执行已决策的核心交互，不借机扩范围。",
            "每个 session 结束更新架构/范围/假设日志，防上下文漂移。",
            "对 traction 做怀疑论复盘，识别假 PMF。",
        ],
        "do_not": [
            "不要按功能完成度汇报 MVP。",
            "不要因为实现便宜就加酷功能。",
            "不要把真实用户数据交给未经安全 review 的代码。",
        ],
        "boss_question": "今天哪一个最小改动最能产生 PMF 证据？",
        "artifact": "CLAUDE.md / MVP scope / security review / metrics baseline",
    },
    {
        "id": "launch",
        "label": "Launch / 上线增长",
        "goal": "把早期 traction 变成可重复渠道和不依赖创始人的运营系统。",
        "exit_gate": [
            "增长可解释：渠道、CAC、LTV、回本周期、转化漏斗能讲清。",
            "产品扛住生产负载：安全、合规、监控、恢复路径到位。",
            "客服、bug 分诊、周报、sprint 和反馈循环不靠老板记得做。",
        ],
        "ai_job": [
            "跑架构 audit，分诊必须先修、并行修、可接受的债。",
            "把创始人注意力流向做 audit，设计自动化/委派/保留判断。",
            "建立轻量产品管理系统：spec 模板、bug 决策树、周指标简报。",
        ],
        "do_not": [
            "不要让老板成为所有客服、bug、报告和产品决策入口。",
            "不要用 launch 热闹替代留存和付费。",
            "不要在原市场未稳时追逐新市场变量。",
        ],
        "boss_question": "今天哪一个系统能把老板从重复运营里解放出来？",
        "artifact": "growth dashboard / support SOP / bug triage / compliance backlog",
    },
    {
        "id": "scale",
        "label": "Scale / 规模扩张",
        "goal": "把领域知识、用户数据、集成深度和工作流锁定变成护城河。",
        "exit_gate": [
            "创始人一周不在，核心运营仍能可审计地运转。",
            "企业买家需要的 SLA、支持、合规、文档和监控基础设施到位。",
            "能回答：资金充足的对手今天复制功能，用户为什么仍留下。",
        ],
        "ai_job": [
            "把创始人脑内知识外化成可搜索 context、skills 和测试用例。",
            "把持续使用数据变成产品改进 feedback loop。",
            "为前十大客户做工作流/集成/切换成本 audit。",
        ],
        "do_not": [
            "不要把护城河理解成更多功能按钮。",
            "不要把关键机构知识留在老板脑子里。",
            "不要把 GTM、支持、合规当成临时救火。",
        ],
        "boss_question": "今天哪一个动作会让产品更难被复制或替换？",
        "artifact": "domain playbook / edge-case tests / data flywheel / workflow audit",
    },
]

_TEAM_ROLES = [
    "ClaudeTeam: 创业操作系统底座，负责可靠路由、记忆、驾驶舱、健康检查和自动兜底。",
    "Product Lab: 主战场，负责问题验证、MVP、收款和 PMF 证据。",
    "TODO002: 创始人训练场，负责课程内化、需求嗅觉和每日判断复盘。",
    "WebsiteChuhai: Launch/GTM 外挂，负责渠道、出海、发布、内容和增长证据。",
    "Work Assistant: 现实工作样本库，负责真实 bug、交付、排障和质量闸门。",
]

_COCKPIT_FIELDS = [
    "阶段",
    "阶段出口证据",
    "今天最小证据动作",
    "当前动作",
    "阻塞",
    "老板下一步",
    "不做什么",
    "下次汇报",
]
_TERMINAL = {"已完成", "已取消"}


def _normalise_stage(raw: str) -> str:
    key = raw.strip().lower()
    return _STAGE_ALIASES.get(key, "")


def _selected(stage: str) -> list[dict]:
    if not stage:
        return list(_STAGES)
    return [s for s in _STAGES if s["id"] == stage]


def _payload(stage: str = "") -> dict:
    return {
        "operating_question": "今天哪一个动作，最能证明有人真的需要、愿意用、愿意付费？",
        "rule": "No stage, no task. No evidence, no build. No system, no scale.",
        "stages": _selected(stage),
        "team_roles": _TEAM_ROLES,
        "cockpit_fields": _COCKPIT_FIELDS,
    }


def _task_text(task: dict, *keys: str) -> str:
    for key in keys:
        value = str(task.get(key) or "").strip()
        if value:
            return value
    return ""


def _task_title(task: dict) -> str:
    return _task_text(task, "title", "id") or "untitled"


def _clip(text: str, limit: int = 180) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _missing_task_fields(task: dict) -> list[str]:
    missing: list[str] = []
    stage_raw = _task_text(task, "founder_stage", "stage", "current_stage", "当前阶段")
    if not stage_raw or not _normalise_stage(stage_raw):
        missing.append("当前阶段")
    if not _task_text(task, "stage_exit_evidence", "exit_evidence",
                      "evidence", "阶段出口证据"):
        missing.append("阶段出口证据")
    if not _task_text(task, "evidence_action", "today_evidence_action",
                      "today_action", "今天最小证据动作"):
        missing.append("今天最小证据动作")
    if not _task_text(task, "non_goal", "do_not", "not_doing", "不做什么"):
        missing.append("不做什么")
    return missing


def _read_team_tasks(team_dir: Path) -> list[dict]:
    data = read_json(team_dir / "state" / "tasks.json",
                     {"tasks": [], "_meta": {"last_id": 0}})
    rows = data.get("tasks", [])
    return rows if isinstance(rows, list) else []


def _audit_team(team_dir: Path) -> dict:
    open_tasks = [
        t for t in _read_team_tasks(team_dir)
        if t.get("status") not in _TERMINAL
    ]
    missing_rows = []
    for task in open_tasks:
        missing = _missing_task_fields(task)
        if not missing:
            continue
        missing_rows.append({
            "team": team_dir.name,
            "team_dir": str(team_dir),
            "task_id": str(task.get("id") or "?"),
            "title": _task_title(task),
            "assignee": str(task.get("assignee") or "?"),
            "status": str(task.get("status") or "?"),
            "missing_fields": missing,
        })
    return {
        "team": team_dir.name,
        "team_dir": str(team_dir),
        "open_tasks": len(open_tasks),
        "missing_open_tasks": len(missing_rows),
        "missing": missing_rows,
    }


def _registry_audit_rows(root: Path, team_dirs: list[Path], *,
                         registry_script: Path | None = None) -> list[dict]:
    script = registry_script or team_registry.default_script(root)
    sources = team_registry.load(script)
    local_configs = {str((path / "claudeteam.toml").resolve()) for path in team_dirs}
    local_labels = {path.name for path in team_dirs}
    rows: list[dict] = []
    for source in sources:
        label = str(source.get("label") or source.get("key") or "未命名团队")
        if label in local_labels:
            continue
        config_path = str(source.get("config_path") or "")
        if config_path and str(Path(config_path).expanduser().resolve()) in local_configs:
            continue
        key = str(source.get("key") or "")
        chat_id = str(source.get("chat_id") or "")
        status = str(source.get("status") or "待核验")
        if key == "smart_partner":
            audit_status = "需要老板动作"
            missing = ["是否纳入 ClaudeTeam 审计"]
            note = "智能伙伴不在 ClaudeTeam 任务账本中，必须先决定是否接入。"
        else:
            audit_status = "待核验"
            missing = ["远端 Founder OS 审计结果", "云机真实任务账本"]
            note = f"{status}，需云机回写。"
        rows.append({
            "label": label,
            "key": key,
            "chat_id": chat_id,
            "status": status,
            "config_path": config_path,
            "audit_status": audit_status,
            "missing": missing,
            "notes": note,
        })
    return rows


def _audit_payload(team_dirs: list[Path], *, root: Path,
                   include_registry: bool = True,
                   registry_script: Path | None = None) -> dict:
    teams = [_audit_team(path) for path in team_dirs]
    missing_rows = [row for team in teams for row in team["missing"]]
    external = (_registry_audit_rows(root, team_dirs,
                                     registry_script=registry_script)
                if include_registry else [])
    return {
        "ok": not missing_rows and not external,
        "teams_checked": len(teams),
        "open_tasks": sum(team["open_tasks"] for team in teams),
        "missing_open_tasks": len(missing_rows),
        "teams": teams,
        "missing": missing_rows,
        "external_sources": external,
        "manager_instruction": (
            "For every missing row, update the task with founder_stage, "
            "stage_exit_evidence, evidence_action, and non_goal. Do not invent "
            "evidence; if unknown, keep the task missing and report a blocker."
        ),
    }


def _emit_audit_text(payload: dict) -> None:
    print(
        "founder-os audit: "
        f"{payload['teams_checked']} team(s), "
        f"open={payload['open_tasks']}, "
        f"missing={payload['missing_open_tasks']}"
    )
    for team in payload["teams"]:
        print(
            f"- {team['team']} | open={team['open_tasks']} | "
            f"missing={team['missing_open_tasks']}"
        )
        for row in team["missing"][:12]:
            missing = "、".join(row["missing_fields"])
            print(
                f"  {row['task_id']} [{row['status']}] "
                f"{_clip(row['title'], 90)} -> {row['assignee']} | 缺：{missing}"
            )
        if team["missing_open_tasks"] > 12:
            print(f"  ... 还有 {team['missing_open_tasks'] - 12} 条未显示")
    if payload["missing_open_tasks"]:
        print("\nmanager instruction:")
        print("  不准编证据；未知就保持缺失并报 blocker。每条活跃任务补：")
        print("  当前阶段 / 阶段出口证据 / 今天最小证据动作 / 不做什么。")
    if payload.get("external_sources"):
        print("\nregistry-only sources:")
        for row in payload["external_sources"]:
            print(
                f"- {row['label']} | {row['audit_status']} | "
                f"{row['status']} | {row['notes']}"
            )
        print("  这些来源不在本机任务账本里，需要远端回写或老板决定是否接入。")


def _emit_list(label: str, rows: list[str]) -> None:
    print(f"  {label}:")
    for row in rows:
        print(f"    - {row}")


def _emit_text(payload: dict) -> None:
    print("Founder OS v1 — AI 原生创业阶段闸门")
    print(f"核心问题：{payload['operating_question']}")
    print(f"硬规则：{payload['rule']}")
    print()
    for stage in payload["stages"]:
        print(f"{stage['label']}")
        print(f"  目标：{stage['goal']}")
        print(f"  老板问题：{stage['boss_question']}")
        print(f"  产物：{stage['artifact']}")
        _emit_list("出口证据", stage["exit_gate"])
        _emit_list("AI 用法", stage["ai_job"])
        _emit_list("不做", stage["do_not"])
        print()
    print("团队分工：")
    for row in payload["team_roles"]:
        print(f"  - {row}")
    print()
    print("驾驶舱字段：")
    print("  " + " / ".join(payload["cockpit_fields"]))


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    audit_root = pop_flag(rest, "--audit-root")
    raw_stage = pop_flag(rest, "--stage") or ""
    if audit_root is not None:
        if raw_stage:
            return error_exit("--stage cannot be used with --audit-root")
        registry_script_arg = pop_flag(rest, "--registry-script")
        include_registry = not pop_bool_flag(rest, "--no-registry")
        if (rc := reject_extra_args([a for a in rest if a.startswith("--")], USAGE)) is not None:
            return rc
        team_dirs = [Path(item).expanduser().resolve() for item in rest]
        if not team_dirs:
            team_dirs = fleet_health._discover(Path(audit_root).expanduser().resolve())
        payload = _audit_payload(
            team_dirs, root=Path(audit_root).expanduser().resolve(),
            include_registry=include_registry,
            registry_script=(Path(registry_script_arg).expanduser().resolve()
                             if registry_script_arg else None)
            if include_registry else None)
        if as_json:
            print_json(payload)
        else:
            _emit_audit_text(payload)
        return 0 if payload["ok"] else 1
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    stage = ""
    if raw_stage:
        stage = _normalise_stage(raw_stage)
        if not stage:
            return error_exit(f"unknown stage: {raw_stage}\n\n{USAGE}")
    payload = _payload(stage)
    if as_json:
        print_json(payload)
    else:
        _emit_text(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
