"""Tests for `claudeteam say` — Feishu chat send + local mirror."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from pathlib import Path

from claudeteam import cli
from claudeteam.feishu import chat as feishu_chat
from claudeteam.store import local_facts


@contextlib.contextmanager
def _isolated(chat_id: str = "oc_test", profile: str = ""):
    with tempfile.TemporaryDirectory() as tmp:
        team = Path(tmp) / "team.json"
        team.write_text('{"agents":{"manager":{}}}', encoding="utf-8")
        rt = Path(tmp) / "runtime_config.json"
        rt.write_text(json.dumps({"chat_id": chat_id, "lark_profile": profile}), encoding="utf-8")
        old = {k: os.environ.get(k) for k in
               ("CLAUDETEAM_TEAM_FILE", "CLAUDETEAM_RUNTIME_CONFIG", "CLAUDETEAM_STATE_DIR")}
        os.environ["CLAUDETEAM_TEAM_FILE"] = str(team)
        os.environ["CLAUDETEAM_RUNTIME_CONFIG"] = str(rt)
        os.environ["CLAUDETEAM_STATE_DIR"] = str(Path(tmp) / "state")
        try:
            yield Path(tmp)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


@contextlib.contextmanager
def _fake_send():
    """Replace feishu_chat.send_text with a recorder."""
    state = {"calls": [], "result": {"message_id": "om_fake"}}

    def fake(chat_id, text, *, profile="", as_user=False, reply_to="", lark_run=None):
        state["calls"].append({
            "chat_id": chat_id, "text": text,
            "profile": profile, "as_user": as_user, "reply_to": reply_to,
        })
        return state["result"]

    original = feishu_chat.send_text
    feishu_chat.send_text = fake
    try:
        yield state
    finally:
        feishu_chat.send_text = original


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def test_say_sends_to_chat_and_logs_locally():
    with _isolated(), _fake_send() as send:
        rc, out, _ = _run(["say", "manager", "hello", "world"])
        assert rc == 0
        assert "manager → chat (om_fake)" in out
        assert send["calls"]
        call = send["calls"][0]
        assert call["chat_id"] == "oc_test"
        assert call["text"] == "[manager] hello world"
        # local mirror written
        logs = local_facts.list_logs("manager")
        assert len(logs) == 1
        assert logs[0]["type"] == "say"
        assert logs[0]["content"] == "hello world"


def test_say_default_identity_is_bot():
    with _isolated(), _fake_send() as send:
        _run(["say", "manager", "hi"])
        assert send["calls"][0]["as_user"] is False


def test_say_as_user_flag():
    with _isolated(), _fake_send() as send:
        _run(["say", "manager", "hi", "--as", "user"])
        assert send["calls"][0]["as_user"] is True


def test_say_reply_flag_threads_through():
    with _isolated(), _fake_send() as send:
        _run(["say", "manager", "hi", "--reply", "om_parent"])
        assert send["calls"][0]["reply_to"] == "om_parent"


def test_say_no_local_skips_log_write():
    with _isolated(), _fake_send():
        _run(["say", "manager", "hi", "--no-local"])
        assert local_facts.list_logs("manager") == []


def test_say_returns_one_when_chat_id_unset():
    with _isolated(chat_id=""), _fake_send():
        rc, _, err = _run(["say", "manager", "hi"])
        assert rc == 1
        assert "chat_id not set" in err


def test_say_returns_one_when_lark_returns_none():
    with _isolated(), _fake_send() as send:
        send["result"] = None
        rc, _, err = _run(["say", "manager", "hi"])
        assert rc == 1
        assert "Feishu send failed" in err


def test_say_threads_profile():
    with _isolated(profile="prod"), _fake_send() as send:
        _run(["say", "manager", "hi"])
        assert send["calls"][0]["profile"] == "prod"


def test_say_zero_or_one_arg_returns_one():
    rc, _, err = _run(["say"])
    assert rc == 1
    assert "usage:" in err
    rc, _, err = _run(["say", "manager"])
    assert rc == 1
    assert "usage:" in err
