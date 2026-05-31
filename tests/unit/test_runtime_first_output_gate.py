"""Tests for runtime/first_output_gate.py."""
from __future__ import annotations

from helpers import attr_patch, isolated_env
from claudeteam.runtime import first_output_gate


def _task(title: str = "核对 bug") -> dict:
    return {"id": "T-1", "title": title, "description": "", "topic": ""}


def test_vague_status_is_not_valid_first_output():
    result = first_output_gate.check(_task(), {
        "content": "正在处理",
        "artifact": "",
    })

    assert not result.valid
    assert result.reason == "空话"


def test_actionable_blocker_is_valid_first_output():
    result = first_output_gate.check(_task(), {
        "content": "blocker: 小红书后台登录态失效，已刷新失败两次，需要老板扫码，10 分钟后复查",
        "artifact": "",
    })

    assert result.valid


def test_short_card_is_valid_first_output_when_task_asks_for_short_card():
    result = first_output_gate.check({
        "id": "T-1",
        "title": "晨训校准",
        "description": "08:12 前只给 manager 一张短卡。\n格式必须短：\n直觉结论：\n关键线索：\n反证 / 不确定点：\n最小验证动作：\n今天会应用到的真实任务：",
        "topic": "",
    }, {
        "content": "\n".join([
            "直觉结论：先查现场证据，不默认是业务代码。",
            "关键线索：昨天已证实坏镜像、半安装污染和 import gate 失败。",
            "反证 / 不确定点：官方 PyPI 最小安装还没复测。",
            "最小验证动作：看 live 进程、venv 落盘、pip 源和 import gate。",
            "今天会应用到的真实任务：MoneyPrinterTurbo 环境排查。",
        ]),
        "artifact": "",
    })

    assert result.valid


def test_four_point_live_report_is_valid_first_output_when_task_asks_for_four_points():
    result = first_output_gate.check({
        "id": "T-1",
        "title": "MoneyPrinterTurbo 半安装处置",
        "description": "请 20 分钟内回我 4 点：1) 当前安装落在哪；2) 该保留什么；3) 该停止什么；4) 下次恢复步骤。",
        "topic": "",
    }, {
        "content": "T-164 现场四点：1) 当前半安装目录在 /tmp/MoneyPrinterTurbo；2) 该保留代码目录、config.toml、pyproject.toml；3) 该停止继续跑前台 pip/uv 安装；4) 下次恢复先确认 8080/8501 空闲，再跑官方 PyPI 最小安装和 import gate。",
        "artifact": "",
    })

    assert result.valid


def test_fake_tmp_path_is_rejected():
    with isolated_env(team={"agents": {"manager": {}}}):
        result = first_output_gate.check(_task(), {
            "content": "artifact: /tmp/fake.png",
            "artifact": "",
        })

    assert not result.valid
    assert result.reason == "路径不合法"


def test_existing_relative_artifact_is_valid():
    with isolated_env(team={"agents": {"manager": {}}}) as tmp:
        artifact = tmp / "artifacts" / "T-1" / "out.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("ok", encoding="utf-8")
        result = first_output_gate.check(_task(), {
            "content": "artifact: artifacts/T-1/out.md",
            "artifact": "",
        })

    assert result.valid


def test_unusable_url_is_rejected():
    with attr_patch(
        first_output_gate,
        check_url=lambda url, timeout_s=2.0: (False, "http 404", "text/html"),
    ):
        result = first_output_gate.check(_task(), {
            "content": "artifact: https://example.com/missing.md",
            "artifact": "",
        })

    assert not result.valid
    assert result.reason == "证据不可用"


def test_image_task_rejects_non_image_url():
    with attr_patch(
        first_output_gate,
        check_url=lambda url, timeout_s=2.0: (True, "http 200", "text/html"),
    ):
        result = first_output_gate.check(_task("生成一张活动配图"), {
            "content": "artifact: https://example.com/report",
            "artifact": "",
        })

    assert not result.valid
    assert result.reason == "证据不符"
