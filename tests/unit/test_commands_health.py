"""Tests for `claudeteam health`."""
from __future__ import annotations

import contextlib
import shutil
import tempfile
from pathlib import Path

from helpers import attr_patch, env_patch, isolated_env, run_cli, tmux_patch
from claudeteam.commands import health as health_cmd


@contextlib.contextmanager
def _stub_host_checks():
    with attr_patch(health_cmd, _lark_process_rows=lambda: []), \
            attr_patch(shutil, which=lambda name, *a, **kw: f"/usr/bin/{name}"):
        yield


@contextlib.contextmanager
def _stub_tmux(*, session_alive: bool, panes_with_cli: list[str] = (),
               panes_without_cli: list[str] = ()):
    """Replace tmux.has_session/has_window/capture_pane for health probing."""
    all_panes = list(panes_with_cli) + list(panes_without_cli)

    def capture_pane(target, lines=80):
        if target.window in panes_with_cli:
            return "bypass permissions on\n? for shortcuts\n>"
        return "$ "

    with tmux_patch(
            has_session=lambda s: session_alive,
            has_window=lambda target: target.window in all_panes,
            capture_pane=capture_pane), \
            _stub_host_checks():
        yield


# ── happy path ──────────────────────────────────────────────────


def test_health_all_green_returns_zero():
    """No reds AND no warnings → green footer."""
    team = {"session": "S", "agents": {"manager": {"cli": "claude-code"}}}
    rc_cfg = {"chat_id": "oc_x", "lark_profile": "prod"}
    with isolated_env(team=team, runtime_config=rc_cfg), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]), \
            _stub_which({"claude"}), \
            env_patch(HTTPS_PROXY=None, HTTP_PROXY=None):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "team config" in out
        assert "chat_id: oc_x" in out
        assert "lark_profile: prod" in out
        assert "tmux session: S" in out
        assert "manager: pane ready" in out
        # Daemons / cursor lines are ⚠️ / ℹ️ in this isolated test rig
        # (no pid files); footer should report warnings, not "all green"
        assert "no errors" in out
        assert "warning" in out


# ── red checks ──────────────────────────────────────────────────


def test_health_returns_one_when_session_down():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=False):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "tmux session S not running" in out


def test_health_returns_one_when_chat_id_blank():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": ""}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "chat_id is empty" in out


def test_health_returns_one_when_team_config_missing():
    """No claudeteam.toml AND no team.json → can't deploy. Health
    surfaces this as a red so the operator sees it before running up."""
    with isolated_env(runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True):
        # don't call isolated_env(team=...) so neither config file exists
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "team config missing" in out


def test_health_returns_one_when_state_and_toml_config_mismatch():
    """If state points at one team but config points elsewhere, health is red.

    This catches the dangerous operator mistake where tasks are written to
    one team's state while commands read another team's claudeteam.toml.
    """
    from claudeteam.runtime import tunables

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        real = root / "real-team"
        wrong = root / "wrong-team"
        (real / "state").mkdir(parents=True)
        wrong.mkdir()
        (real / "claudeteam.toml").write_text("[team]\nsession = 'Real'\n", encoding="utf-8")
        (wrong / "claudeteam.toml").write_text(
            "chat_id = 'oc_wrong'\nlark_profile = 'prod'\n"
            "[team]\nsession = 'Wrong'\n"
            "[team.agents.manager]\ncli = 'claude-code'\n",
            encoding="utf-8",
        )
        tunables.reset_cache()
        with env_patch(
                CLAUDETEAM_STATE_DIR=str(real / "state"),
                CLAUDETEAM_CONFIG_FILE=str(wrong / "claudeteam.toml"),
                CLAUDETEAM_TEAM_FILE=str(wrong / "team.json"),
                CLAUDETEAM_RUNTIME_CONFIG=str(wrong / "runtime_config.json"),
        ), _stub_tmux(session_alive=True, panes_with_cli=["manager"]):
            rc, out, _ = run_cli(["health"])
        tunables.reset_cache()

    assert rc == 1
    assert "config/state mismatch" in out
    assert str(real / "claudeteam.toml") in out


def test_health_returns_one_when_pane_window_missing():
    team = {"session": "S", "agents": {"manager": {}, "missing_w": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "missing_w: no tmux window" in out


# ── warnings (non-fatal) ────────────────────────────────────────


def test_health_warns_when_pane_up_but_no_cli_marker():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=[], panes_without_cli=["manager"]), \
            _stub_which({"claude"}):
        rc, out, _ = run_cli(["health"])
        assert rc == 0  # warning only
        assert "CLI not ready yet" in out


def test_health_warns_when_ready_pane_heartbeat_is_stale():
    import json
    from claudeteam.runtime import paths
    from claudeteam.util import now_ms

    team = {"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_host_checks(), tmux_patch(
            has_session=lambda s: True,
            has_window=lambda target: target.window == "manager",
            capture_pane=lambda target, lines=80: "\n\n  gpt-5.5 xhigh · /work"):
        paths.facts_dir().mkdir(parents=True, exist_ok=True)
        stale = now_ms() - 31 * 60 * 1000
        (paths.facts_dir() / "heartbeats.json").write_text(
            json.dumps({"manager": stale}), encoding="utf-8")

        rc, out, _ = run_cli(["health"])

        assert rc == 0
        assert "manager: pane ready (codex-cli) but heartbeat is stale" in out
        assert "warning" in out


def test_health_footer_separates_stale_heartbeat_from_feishu_auth():
    import json
    from claudeteam.runtime import paths
    from claudeteam.util import now_ms

    team = {"session": "S", "agents": {"worker": {"cli": "codex-cli"}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x", "lark_profile": "prod"}), \
            _stub_host_checks(), \
            attr_patch(health_cmd.watchdog, all_known_specs=lambda: []), \
            tmux_patch(
                has_session=lambda s: True,
                has_window=lambda target: target.window == "worker",
                capture_pane=lambda target, lines=80: "\n\n  gpt-5.5 xhigh · /work"):
        paths.facts_dir().mkdir(parents=True, exist_ok=True)
        stale = now_ms() - 31 * 60 * 1000
        (paths.facts_dir() / "heartbeats.json").write_text(
            json.dumps({"worker": stale}), encoding="utf-8")

        rc, out, _ = run_cli(["health"])

        assert rc == 0
        assert "worker: pane ready (codex-cli) but heartbeat is stale" in out
        assert "agent heartbeat stale only" in out
        assert "not a Feishu CLI/App Secret warning" in out


def test_health_json_includes_warning_categories():
    import json
    from claudeteam.runtime import paths
    from claudeteam.util import now_ms

    team = {"session": "S", "agents": {"worker": {"cli": "codex-cli"}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x", "lark_profile": "prod"}), \
            _stub_host_checks(), \
            attr_patch(health_cmd.watchdog, all_known_specs=lambda: []), \
            tmux_patch(
                has_session=lambda s: True,
                has_window=lambda target: target.window == "worker",
                capture_pane=lambda target, lines=80: "\n\n  gpt-5.5 xhigh · /work"):
        paths.facts_dir().mkdir(parents=True, exist_ok=True)
        stale = now_ms() - 31 * 60 * 1000
        (paths.facts_dir() / "heartbeats.json").write_text(
            json.dumps({"worker": stale}), encoding="utf-8")

        rc, out, _ = run_cli(["health", "--json"])

        assert rc == 0
        data = json.loads(out)
        assert data["warn_categories"] == {"stale_heartbeat": 1}


def test_health_warns_when_ready_pane_contains_provider_error():
    team = {"session": "S", "agents": {"manager": {}}}

    def capture_pane(target, lines=80):
        return (
            "API Error: API returned an empty or malformed response (HTTP 200) "
            "— check for a proxy or gateway intercepting the request\n"
            "⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        )

    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_host_checks(), tmux_patch(
            has_session=lambda s: True,
            has_window=lambda target: target.window == "manager",
            capture_pane=capture_pane):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "provider/api error" in out
        assert "pane reachable but" in out


def test_health_treats_codex_xhigh_status_line_as_ready():
    team = {"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}

    def capture_pane(target, lines=80):
        return "\n\n  gpt-5.5 xhigh · /srv/ai/projects/product-lab"

    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_host_checks(), tmux_patch(
            has_session=lambda s: True,
            has_window=lambda target: target.window == "manager",
            capture_pane=capture_pane):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "manager: pane ready (codex-cli)" in out
        assert "CLI not ready yet" not in out


def test_health_lazy_pane_without_marker_is_green():
    """A pane marked lazy in team.json is expected to have no ready marker
    until first message. Don't yellow-flag the operator over expected state."""
    team = {"session": "S", "agents": {"sleeper": {"cli": "claude-code", "lazy": True}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=[], panes_without_cli=["sleeper"]), \
            _stub_which({"claude"}):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "lazy pane" in out
        assert "CLI not ready yet" not in out


def test_health_warns_when_lark_profile_blank():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x", "lark_profile": ""}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]), \
            _stub_which({"claude"}):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "lark_profile blank" in out


def test_health_warns_when_router_pid_missing():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]), \
            _stub_which({"claude"}):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "router: no pid file" in out


def test_health_info_when_cursor_empty():
    """Empty cursor on first run is informational, not a warning — it only
    advances on inbound events, not self-originated say calls."""
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]), \
            _stub_which({"claude"}):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "router cursor: empty" in out
        assert "ℹ️" in out  # info marker, not warn marker
        # ensure "advances on first inbound event" is in the cursor line
        assert "first inbound event" in out
        # #5: empty cursor → tell the operator how to confirm inbound works,
        # instead of leaving "is it working?" unanswerable.
        assert "inbound: none observed yet" in out


def test_health_shows_inbound_age_when_cursor_present():
    """#5: with a cursor, health prints a positive 'inbound: last event …'
    signal. On macOS the live WS goes quiet and router.log only shows the
    rotate line, so this is the at-a-glance answer to 'is inbound working?'."""
    from claudeteam.commands import health
    from claudeteam.feishu import catchup
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}):
        # epoch-ms string passes straight through _to_epoch_ms → create_time_ms
        catchup.write_cursor("om_test", "1782220000000")
        rep = health.HealthReport()
        health._check_cursor(rep)
    text = "\n".join(rep.lines)
    assert "router cursor: om_test" in text
    assert "inbound: last event" in text
    assert "ago" in text  # ago_ms-formatted


# ── memory section ──────────────────────────────────────────────


def test_health_memory_section_lists_agents_with_entries():
    """When agents have written memory, list them inline (one-liner if
    ≤5 agents). Doesn't change the rc — informational only."""
    from claudeteam.store import memory
    team = {"session": "S",
            "agents": {"manager": {}, "worker_cc": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), \
            _stub_tmux(session_alive=True,
                       panes_with_cli=["manager", "worker_cc"]), \
            _stub_which({"claude"}):
        memory.append("manager", "decision", "x")
        memory.append("worker_cc", "note", "y")
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "memory: 2 agent(s) with entries" in out
        assert "manager" in out and "worker_cc" in out


# ── binaries / env ──────────────────────────────────────────────


def _stub_which(present: set[str]):
    """shutil.which replacement: returns a fake path for names in `present`,
    None for everything else. Doesn't fall through to the real PATH."""
    return attr_patch(
        shutil,
        which=lambda name, *a, **kw: f"/usr/bin/{name}" if name in present else None,
    )


def test_health_red_when_binary_missing():
    team = {"session": "S", "agents": {"m": {"cli": "claude-code"}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["m"]), _stub_which(set()):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "claude: not on PATH" in out


def test_health_warns_when_proxy_set_without_no_proxy():
    team = {"session": "S", "agents": {"m": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["m"]), \
            env_patch(HTTPS_PROXY="http://proxy:7890", LARK_CLI_NO_PROXY=None):
        rc, out, _ = run_cli(["health"])
        assert "proxy env=http://proxy:7890 set without LARK_CLI_NO_PROXY" in out


def test_health_silent_when_proxy_unset():
    team = {"session": "S", "agents": {"m": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["m"]), \
            env_patch(HTTPS_PROXY=None, HTTP_PROXY=None, ALL_PROXY=None):
        rc, out, _ = run_cli(["health"])
        assert "proxy env" not in out



def test_health_info_when_proxy_set_with_no_proxy_flag():
    """Proxy env set + LARK_CLI_NO_PROXY=1 → informational ℹ️ rather
    than warning ⚠️. The wrapper strips proxy at lark.subprocess_env(),
    so this is intentional + harmless — but the env var still shows
    so operators don't get confused why their proxy isn't applying."""
    team = {"session": "S", "agents": {"m": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["m"]), \
            env_patch(HTTPS_PROXY="http://proxy:7890", LARK_CLI_NO_PROXY="1"):
        rc, out, _ = run_cli(["health"])
        assert "proxy env set" in out
        assert "wrapper will strip" in out
        # Confirm it's INFO not WARNING — the test would also fire a
        # warning on bad emoji selection, so check the explicit string.
        assert "ℹ️" in out


def test_health_info_when_proxy_set_with_toml_no_proxy_flag():
    """`[feishu] no_proxy = true` is just as effective as the legacy env
    flag, so health should not scare the operator with a false warning."""
    team = {"session": "S", "agents": {"m": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}) as tmp, \
            _stub_tmux(session_alive=True, panes_with_cli=["m"]), \
            env_patch(HTTPS_PROXY="http://proxy:7890", LARK_CLI_NO_PROXY=None):
        (tmp / "claudeteam.toml").write_text(
            """
chat_id = "oc_x"
lark_profile = "prod"
[team]
session = "S"
[team.agents.m]
cli = "claude-code"
[feishu]
no_proxy = true
""".strip(),
            encoding="utf-8",
        )
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "feishu.no_proxy=true" in out
        assert "set without LARK_CLI_NO_PROXY" not in out


def test_health_no_proxy_flag_truthy_variants_all_recognised():
    """LARK_CLI_NO_PROXY accepts 1/true/yes/on (case-insensitive). Make
    sure the ℹ️ branch fires for the full set, not just the literal '1'."""
    team = {"session": "S", "agents": {"m": {}}}
    for truthy in ("1", "true", "True", "YES", "on"):
        with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), \
                _stub_tmux(session_alive=True, panes_with_cli=["m"]), \
                env_patch(HTTPS_PROXY="http://p", LARK_CLI_NO_PROXY=truthy):
            rc, out, _ = run_cli(["health"])
            assert "wrapper will strip" in out, (
                f"LARK_CLI_NO_PROXY={truthy!r} should be recognised as truthy")


def test_health_red_when_lark_cli_process_is_stuck_for_profile():
    """A stuck lark-cli child means the message transport is not healthy
    even when router/watchdog pid files look alive."""
    team = {"session": "S", "agents": {"m": {}}}
    rows = [{
        "pid": "123",
        "ppid": "1",
        "stat": "UE",
        "command": "lark-cli --profile prod event +subscribe --as bot",
    }]
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x", "lark_profile": "prod"}), \
            _stub_tmux(session_alive=True, panes_with_cli=["m"]), \
            attr_patch(health_cmd, _lark_process_rows=lambda: rows):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "lark-cli stuck process(es): 1 for profile prod" in out


def test_health_ignores_stuck_lark_cli_from_other_profile():
    team = {"session": "S", "agents": {"m": {}}}
    rows = [{
        "pid": "123",
        "ppid": "1",
        "stat": "UE",
        "command": "lark-cli --profile other event +subscribe --as bot",
    }]
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x", "lark_profile": "prod"}), \
            _stub_tmux(session_alive=True, panes_with_cli=["m"]), \
            attr_patch(health_cmd, _lark_process_rows=lambda: rows):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "lark-cli stuck process" not in out


# ── help ────────────────────────────────────────────────────────


# ── --json mode ─────────────────────────────────────────────────


def test_health_json_emits_machine_readable_object():
    """--json dumps {ok, bad, warn, lines} so CI scripts can
    branch on `ok` without grepping the formatted output."""
    import json as _json
    team = {"session": "S", "agents": {"manager": {"cli": "claude-code"}}}
    rc_cfg = {"chat_id": "oc_x", "lark_profile": "prod"}
    with isolated_env(team=team, runtime_config=rc_cfg), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]), \
            _stub_which({"claude"}), \
            env_patch(HTTPS_PROXY=None, HTTP_PROXY=None):
        rc, out, _ = run_cli(["health", "--json"])
        # No reds → exit 0
        assert rc == 0
        data = _json.loads(out)
        assert isinstance(data, dict)
        assert data["ok"] is True
        assert data["bad"] == 0
        assert data["warn"] >= 0
        assert isinstance(data["lines"], list)
        assert any("team config" in line for line in data["lines"])


def test_health_json_with_bad_check_returns_one_and_ok_false():
    """When a check fails, JSON mode still exits 1 and ok=False."""
    import json as _json
    # team.json missing → red
    rc_cfg = {"chat_id": "oc_x"}
    with isolated_env(runtime_config=rc_cfg), _stub_tmux(session_alive=False):
        rc, out, _ = run_cli(["health", "--json"])
        assert rc == 1
        data = _json.loads(out)
        assert data["ok"] is False
        assert data["bad"] >= 1


def test_health_json_unknown_args_returns_one():
    """Mistyped flag should fail loudly, not silently accept."""
    rc, _, err = run_cli(["health", "--lol"])
    assert rc == 1
    assert "unexpected args" in err
