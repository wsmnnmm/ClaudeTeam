"""Thin wrapper around the lark-cli binary.

Single function: `call(args, *, profile, timeout, cwd) -> dict | None`.

Returns the `data` field of lark-cli's JSON response on success, `{}` if
stdout is empty, `None` on any failure.  Proxy bypass is automatic when
`LARK_CLI_NO_PROXY=1` is set in the environment.

Performance: prefer the direct lark-cli binary over `npx`. Going
through `npx` adds npm's package-lookup overhead (tens of seconds per
call); `resolve_cli_prefix` picks the direct binary when one is on disk
(`lark-cli` on PATH or the npx cache binary at
`~/.npm/_npx/<hash>/node_modules/.bin/lark-cli`), keeping a real
round-trip around ~0.6s on a macOS host. Default timeout = 90s gives
plenty of margin; bump via `CLAUDETEAM_LARK_TIMEOUT` only if your
network actually is slow.

Tests inject a fake subprocess.run via the `run=` kwarg.
"""
from __future__ import annotations

import contextlib
import json
import os
import pwd
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Callable

from claudeteam.util import env_str


_PROXY_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
)
_APP_CREDENTIAL_KEYS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "LARKSUITE_CLI_APP_ID",
    "LARKSUITE_CLI_APP_SECRET",
    "LARKSUITE_CLI_TENANT_ACCESS_TOKEN",
)

# Container-deploy token bootstrap. lark-cli on macOS host reads app
# secrets from the system keychain; that path doesn't work in a
# Linux container and lark-cli answers "no access token available
# for bot" even when FEISHU_APP_SECRET / FEISHU_APP_ID are set in
# env. Auto-fetching `LARKSUITE_CLI_TENANT_ACCESS_TOKEN` from
# app_id+app_secret here means both one-shot `lark.call()` and the
# long-running `event +subscribe` daemon pick up a fresh token
# without an entrypoint script.
_TENANT_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal")
# Per-uid path in the system temp dir; the file is written 0600 (it
# holds a bearer token and must not be world-readable on a shared host).
_TENANT_TOKEN_CACHE = os.path.join(
    tempfile.gettempdir(), f"claudeteam_tenant_token_{os.getuid()}.json")
_TENANT_TOKEN_REFRESH_BUFFER_S = 60   # refetch when within 60s of expiry
_TENANT_TOKEN_FETCH_RETRIES = 3
_TENANT_TOKEN_FETCH_RETRY_SLEEP_S = 0.2
_TENANT_TOKEN_STALE_GRACE_S = 300


def _tenant_token_cache_path(app_id: str | None,
                             cache_path: str | None = None) -> str:
    """Return the tenant-token cache path for the current Feishu app.

    Multiple ClaudeTeam teams can run on the same host with different
    Feishu bots. A single global cache lets one bot reuse another bot's
    tenant_access_token, which makes messages appear from the wrong app.
    Default cache files are therefore app-scoped:
    `/tmp/claudeteam_tenant_token_<app_id>.json`.

    Tests and specialised callers may pass `cache_path` to force an exact
    path; those entries are still guarded by the `app_id` stored in JSON.
    """
    if cache_path is not None:
        return cache_path
    if not app_id:
        return _TENANT_TOKEN_CACHE
    root, ext = os.path.splitext(_TENANT_TOKEN_CACHE)
    safe_app_id = "".join(
        ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in app_id
    )
    return f"{root}_{safe_app_id}{ext or '.json'}"


def _profile_app_id(profile: str, *, home: str | None = None) -> str:
    """Return the app id configured for a lark-cli profile, if known."""
    app_id, _ = _profile_app_credentials(profile, home=home)
    return app_id


def _profile_app_credentials(profile: str, *, home: str | None = None) -> tuple[str, str]:
    """Return `(app_id, app_secret)` for a lark-cli profile, if known."""
    profile = (profile or "").strip()
    if not profile:
        return "", ""
    root = home or pwd.getpwuid(os.getuid()).pw_dir
    path = os.path.join(root, ".lark-cli", "config.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read())
    except (OSError, json.JSONDecodeError):
        return "", ""
    apps = data.get("apps") if isinstance(data, dict) else None
    if not isinstance(apps, list):
        return "", ""
    for app in apps:
        if not isinstance(app, dict):
            continue
        if str(app.get("name") or "").strip() == profile:
            return (
                str(app.get("appId") or "").strip(),
                str(app.get("appSecret") or "").strip(),
            )
    return "", ""


def _strip_mismatched_profile_credentials(env: dict[str, str],
                                          profile: str | None) -> None:
    """Remove inherited app creds when they conflict with the lark profile.

    Agent panes can inherit Feishu app credentials from another running
    team while still selecting this team's `LARK_CLI_PROFILE`. Passing both
    to lark-cli makes sends authenticate as the wrong bot and Feishu returns
    "Bot/User can NOT be out of the chat". If the profile is known locally,
    trust the profile binding and drop the mismatched app env before token
    bootstrap.
    """
    prof = (profile or env.get("LARK_CLI_PROFILE") or "").strip()
    expected = _profile_app_id(prof, home=env.get("HOME"))
    actual = (env.get("FEISHU_APP_ID")
              or env.get("LARKSUITE_CLI_APP_ID")
              or "").strip()
    if not (expected and actual) or expected == actual:
        return
    for key in _APP_CREDENTIAL_KEYS:
        env.pop(key, None)


# ── app credentials (written by `feishu connect`) ────────────────────
# Single source of truth for the registered Feishu app: a 0600 file in the
# state dir. `feishu connect` writes it after the QR register; both consumers
# read it back through the resolvers below — the sidecar ingress (subprocess_env
# injects FEISHU_APP_ID/SECRET) and lark-cli egress (the tenant-token fetch).
# This replaces the old host-keychain-vs-Docker-.env split with one file that
# works identically in both. Env vars still take precedence (advanced override).
def app_creds_file():
    from claudeteam.runtime import paths
    return paths.state_file("feishu_app.json")


def load_app_creds() -> dict:
    """Read the persisted app creds. Returns {} if absent/unreadable."""
    try:
        with open(app_creds_file(), "r", encoding="utf-8") as fh:
            data = json.loads(fh.read())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_app_creds(*, app_id: str, app_secret: str,
                   owner_open_id: str = "", tenant: str = "feishu") -> None:
    """Persist app creds 0600. Created owner-only and O_NOFOLLOW (it holds a
    secret; same hardening as the tenant-token cache)."""
    from claudeteam.runtime import paths
    paths.ensure_state_dir()
    path = app_creds_file()
    payload = json.dumps({
        "app_id": app_id, "app_secret": app_secret,
        "owner_open_id": owner_open_id, "tenant": tenant,
    }).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)  # tighten if it pre-existed


def _resolve_app_id_secret() -> tuple[str, str]:
    """App id + secret with env precedence, falling back to the creds file.
    Empty strings when nothing supplies them."""
    app_id = env_str("FEISHU_APP_ID") or env_str("LARKSUITE_CLI_APP_ID")
    app_secret = (env_str("FEISHU_APP_SECRET")
                  or env_str("LARKSUITE_CLI_APP_SECRET"))
    if not (app_id and app_secret):
        creds = load_app_creds()
        app_id = app_id or str(creds.get("app_id", ""))
        app_secret = app_secret or str(creds.get("app_secret", ""))
    return app_id, app_secret


def sidecar_path():
    """Path to the Feishu Channel sidecar (`scripts/feishu_channel/sidecar.js`)
    — used for both the `run` event ingress (router) and `feishu connect`.
    Override the directory with CLAUDETEAM_FEISHU_SIDECAR_DIR; otherwise
    repo-relative to this package."""
    from pathlib import Path
    override = env_str("CLAUDETEAM_FEISHU_SIDECAR_DIR")
    base = (Path(override) if override
            else Path(__file__).resolve().parents[3] / "scripts" / "feishu_channel")
    return base / "sidecar.js"


def _fetch_tenant_token(app_id: str, app_secret: str) -> dict | None:
    """POST app_id+app_secret → Feishu tenant_access_token endpoint.

    Returns `{"token": str, "expire_at": <epoch_seconds>}` on success
    (with the buffer subtracted so the cache flips before the wire
    expiry hits) or None on any network / parse / API failure.
    """
    import json as _json
    import time as _time
    import urllib.error
    import urllib.request
    body = _json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        _TENANT_TOKEN_URL, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(_TENANT_TOKEN_FETCH_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode("utf-8", errors="ignore"))
            break
        except (urllib.error.URLError, OSError, _json.JSONDecodeError):
            if attempt >= _TENANT_TOKEN_FETCH_RETRIES - 1:
                return None
            _time.sleep(_TENANT_TOKEN_FETCH_RETRY_SLEEP_S * (attempt + 1))
    token = data.get("tenant_access_token")
    expire = int(data.get("expire", 0) or 0)
    if not token:
        return None
    return {
        "token": str(token),
        "expire_at": int(_time.time()) + max(0, expire - _TENANT_TOKEN_REFRESH_BUFFER_S),
    }


def _ensure_tenant_token(*, fetch: Callable | None = None,
                         now: Callable | None = None,
                         cache_path: str | None = None,
                         source_env: dict[str, str] | None = None) -> str | None:
    """Return a usable tenant_access_token from env / cache / live fetch.

    Resolution order:
      1. `FEISHU_APP_ID` + `FEISHU_APP_SECRET` (or `LARKSUITE_CLI_*`
         aliases) in env — use only that app's cache/fetch path.
      2. `LARKSUITE_CLI_TENANT_ACCESS_TOKEN` already in env and no
         app creds are available — use as-is.
      3. Current app's cache file with `expire_at > now` and matching
         `app_id` — use it.
      4. None of the above — return None and let lark-cli's own auth
         path try (works on macOS host with keychain).

    `fetch` and `now` are injectable for tests so we don't hit the
    network during unit tests.
    """
    import json as _json
    import time as _time
    source = source_env if source_env is not None else os.environ

    def _get(key: str) -> str:
        return str(source.get(key, "") or "").strip()

    app_id = _get("FEISHU_APP_ID") or _get("LARKSUITE_CLI_APP_ID")
    app_secret = (_get("FEISHU_APP_SECRET")
                  or _get("LARKSUITE_CLI_APP_SECRET"))
    existing = _get("LARKSUITE_CLI_TENANT_ACCESS_TOKEN")
    if existing and not (app_id and app_secret):
        return existing
    # Resolve cache_path at call time so test patches of the
    # module-level _TENANT_TOKEN_CACHE constant take effect; default
    # args bind at function-definition time and would freeze the
    # original /tmp path before any patch could land.
    cache_path = _tenant_token_cache_path(app_id, cache_path)
    now_fn = now or _time.time
    now_t = int(now_fn())
    cached = None
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            cached = _json.loads(fh.read())
        cached_app_id = cached.get("app_id")
        if (cached_app_id == app_id
                and int(cached.get("expire_at", 0)) > now_t
                and cached.get("token")):
            return str(cached["token"])
    except (OSError, _json.JSONDecodeError, ValueError):
        pass
    if not (app_id and app_secret):
        return None
    fresh = (fetch or _fetch_tenant_token)(app_id, app_secret)
    if not fresh or not fresh.get("token"):
        cached_token = ""
        cached_expire_at = 0
        cached_app_id = ""
        if isinstance(cached, dict):
            cached_token = str(cached.get("token") or "")
            cached_expire_at = int(cached.get("expire_at", 0) or 0)
            cached_app_id = str(cached.get("app_id") or "")
        if (cached_token
                and cached_app_id == app_id
                and now_t - cached_expire_at <= _TENANT_TOKEN_STALE_GRACE_S):
            return cached_token
        return None
    fresh = dict(fresh)
    fresh["app_id"] = app_id
    try:
        # The token is a bearer credential. Write to a PRIVATE temp file we own
        # (mkstemp → 0600, unique name), then atomically rename over the cache —
        # so a pre-planted *regular* file at the predictable cache path can't
        # capture the token on a shared host (O_NOFOLLOW alone didn't: a co-tenant
        # could pre-create a world-readable regular file and we'd write into it).
        d = os.path.dirname(cache_path) or tempfile.gettempdir()
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".cttok_")
        with os.fdopen(fd, "wb") as f:
            f.write(_json.dumps(fresh).encode("utf-8"))
        os.replace(tmp, cache_path)
    except OSError:
        pass  # cache write best-effort; the in-memory return is the load-bearing path
    return str(fresh["token"])


def subprocess_env(profile: str | None = None) -> dict[str, str]:
    """Build the env to hand to any lark-cli subprocess (one-shot `call` or
    long-running `event +subscribe`). Strips HTTP/HTTPS/ALL proxy vars when
    LARK_CLI_NO_PROXY is truthy, since lark-cli doesn't honor that variable
    itself — it's a wrapper-side flag.

    Also injects `LARKSUITE_CLI_TENANT_ACCESS_TOKEN` when env vars
    supply app_id+app_secret but lark-cli has no keychain access
    (the Linux container case). No-op on macOS host where the token
    is empty and lark-cli's keychain path takes over.

    Pins HOME to the host user's pw_dir so lark-cli finds
    `~/.lark-cli/config.json` regardless of caller HOME. Claude panes
    spawn with HOME=<state_dir>/agent-home/<agent> for ~/.claude.json
    isolation; without this pin, `claudeteam say` from inside an agent
    pane inherited the per-agent HOME and lark-cli failed to locate
    its profile/keychain entry (rc=2). Use pw_dir, not the env's HOME,
    so the override is robust against env tampering by the caller.
    """
    env = os.environ.copy()
    from claudeteam.runtime import tunables
    proxy_url = str(tunables.tunable("feishu.proxy_url", "") or "").strip()
    # `feishu.no_proxy` cascade: legacy env LARK_CLI_NO_PROXY first
    # (predates tunables), then tunable lookup. Truthy => strip proxies.
    legacy = env_str("LARK_CLI_NO_PROXY").lower()
    if legacy in {"1", "true", "yes", "on"}:
        no_proxy = True
    elif legacy in {"0", "false", "no", "off"}:
        no_proxy = False
    else:
        no_proxy = bool(tunables.tunable("feishu.no_proxy", False))
    if proxy_url:
        for key in _PROXY_KEYS:
            env[key] = proxy_url
    elif no_proxy:
        for key in _PROXY_KEYS:
            env.pop(key, None)
    env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    _strip_mismatched_profile_credentials(env, profile)
    bootstrap_env = dict(env)
    prof_app_id = ""
    prof_app_secret = ""
    if not (env.get("LARKSUITE_CLI_APP_ID") or env.get("FEISHU_APP_ID")):
        prof = (profile or env.get("LARK_CLI_PROFILE") or "").strip()
        prof_app_id, prof_app_secret = _profile_app_credentials(
            prof, home=env.get("HOME"))
        if prof_app_id and prof_app_secret:
            bootstrap_env["LARKSUITE_CLI_APP_ID"] = prof_app_id
            bootstrap_env["LARKSUITE_CLI_APP_SECRET"] = prof_app_secret
    token = _ensure_tenant_token(source_env=bootstrap_env)
    if token:
        # lark-cli refuses to start if TENANT_ACCESS_TOKEN is set without a
        # matching LARKSUITE_CLI_APP_ID/SECRET pair — token alone gets
        # `Error: blocked by env: LARKSUITE_CLI_TENANT_ACCESS_TOKEN is set
        # but LARKSUITE_CLI_APP_ID is missing`; token+app_id-only fails
        # the WebSocket subscribe with `app_id or app_secret is null`
        # (lark-cli's persistent-connection SDK re-auths off env-vars,
        # not just the cached token).
        # Propagate all three together; if app_id/secret aren't available
        # in env, skip injection and let lark-cli's profile/keychain
        # path take over.
        app_id = (bootstrap_env.get("LARKSUITE_CLI_APP_ID")
                  or bootstrap_env.get("FEISHU_APP_ID"))
        app_secret = (bootstrap_env.get("LARKSUITE_CLI_APP_SECRET")
                      or bootstrap_env.get("FEISHU_APP_SECRET"))
        if app_id and app_secret:
            env["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] = token
            env["LARKSUITE_CLI_APP_ID"] = app_id
            env["LARKSUITE_CLI_APP_SECRET"] = app_secret
            if app_id == (env.get("FEISHU_APP_ID") or "").strip():
                env["FEISHU_APP_SECRET"] = app_secret
            # Point lark-cli at a ClaudeTeam-owned config dir so a stale global
            # ~/.lark-cli/config.json (e.g. a different app from a prior
            # `lark-cli config init`) can't hijack egress — with no config
            # there, lark-cli authenticates off the injected token+app_id (the
            # app `feishu connect` registered). Only when we HAVE creds; a
            # pure-keychain host deploy (token is None) keeps the global dir.
            from claudeteam.runtime import paths
            cfg_dir = paths.state_file("lark-cli")
            try:
                cfg_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            else:
                env["LARKSUITE_CLI_CONFIG_DIR"] = str(cfg_dir)
    return env


def resolve_cli_prefix() -> list[str]:
    """Return the argv prefix for invoking lark-cli, preferring direct
    binaries over `npx` to skip npm's package-lookup overhead.

    Resolution order (first hit wins):
      1. `CLAUDETEAM_LARK_CLI_BIN` env — operator explicit override.
      2. `lark-cli` on PATH — npm global install (`npm i -g @larksuite/cli`).
      3. The npx cache binary at
         `~/.npm/_npx/<hash>/node_modules/.bin/lark-cli` (auto-installed
         once when npx ran).
      4. `npx @larksuite/cli` — fallback when nothing direct is on disk.

    Resolved fresh on each call so a newly-installed lark-cli takes
    effect without restarting daemons. Direct binary saves ~250–500
    ms per send vs going through `npx`.
    """
    # `feishu.cli_bin` cascade: legacy env first, then tunable lookup.
    override = env_str("CLAUDETEAM_LARK_CLI_BIN")
    if not override:
        from claudeteam.runtime import tunables
        override = str(tunables.tunable("feishu.cli_bin", "") or "")
    if override and os.path.exists(override):
        return [override]
    direct = shutil.which("lark-cli")
    if direct:
        return [direct]
    home = os.path.expanduser("~/.npm/_npx")
    if os.path.isdir(home):
        for entry in os.listdir(home):
            candidate = os.path.join(home, entry,
                                      "node_modules/.bin/lark-cli")
            if os.path.exists(candidate):
                return [candidate]
    return ["npx", "@larksuite/cli"]


def _build_argv(args: list[str], profile: str) -> list[str]:
    base = resolve_cli_prefix()
    if profile:
        base += ["--profile", profile]
    return base + list(args)


def _resolve_timeout(explicit: int | None) -> int:
    """Resolve subprocess timeout in seconds. Caller arg wins; otherwise
    routes through tunables (env > claudeteam.toml > default 90).

    Clamp the final value to >=1 — a garbage env like
    CLAUDETEAM_LARK_TIMEOUT=0 would otherwise make subprocess.run
    insta-TimeoutExpired on every call. The legacy
    `CLAUDETEAM_LARK_TIMEOUT` env var is still honored as an alias.
    """
    if explicit is not None:
        return max(1, int(explicit))
    legacy = env_str("CLAUDETEAM_LARK_TIMEOUT").strip()
    if legacy:
        try:
            return max(1, int(legacy))
        except ValueError:
            pass
    from claudeteam.runtime import tunables
    return max(1, int(tunables.tunable("router.lark_call_timeout_s", 90)))


def _terminate_process_group(proc: subprocess.Popen) -> None:
    """Best-effort cleanup for lark-cli's node wrapper and child process.

    `subprocess.run(..., timeout=...)` only kills the direct child. The
    lark-cli wrapper often leaves the real bin process orphaned, which then
    keeps stale subscribe/list calls alive under PPID=1.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        with contextlib.suppress(OSError):
            proc.kill()
        return
    with contextlib.suppress(OSError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(OSError):
        os.killpg(pgid, signal.SIGKILL)
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        proc.wait(timeout=2)


def _default_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run lark-cli in its own process group so timeouts clean descendants."""
    timeout = kwargs.pop("timeout", None)
    capture_output = bool(kwargs.pop("capture_output", False))
    text = bool(kwargs.pop("text", False))
    env = kwargs.pop("env", None)
    stdout = subprocess.PIPE if capture_output else None
    stderr = subprocess.PIPE if capture_output else None
    proc = subprocess.Popen(
        cmd, stdout=stdout, stderr=stderr, text=text, env=env,
        start_new_session=True, **kwargs,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(proc)
        try:
            out, err = proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            out = exc.output
            err = exc.stderr
        raise subprocess.TimeoutExpired(
            cmd=cmd, timeout=timeout, output=out, stderr=err,
        ) from exc
    return subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode, stdout=out, stderr=err,
    )


def call(args: list[str], *, profile: str = "", timeout: int | None = None,
         cwd: str | None = None, run: Callable = _default_run) -> dict | None:
    """Execute lark-cli; return parsed `data` JSON, `{}` on empty stdout, None on failure.

    `profile` selects the lark-cli profile (`--profile X`).  Pass empty
    string to use the default profile.

    The function intentionally swallows network / lark-cli errors and
    prints a one-line warning instead of raising — callers that need
    to distinguish failure modes should check the return value.
    """
    cmd = _build_argv(args, profile)
    timeout_s = _resolve_timeout(timeout)
    t0 = time.monotonic()
    try:
        run_kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": timeout_s,
            "env": subprocess_env(profile=profile),
        }
        if cwd:
            run_kwargs["cwd"] = cwd
        r = run(cmd, **run_kwargs)
    except subprocess.TimeoutExpired:
        elapsed = (time.monotonic() - t0)
        print(f"  ⚠️ lark-cli timeout ({timeout_s}s after {elapsed:.1f}s): {' '.join(args[:3])}")
        return None
    except FileNotFoundError:
        # npx itself isn't on PATH. claudeteam say / router / chat all hit
        # this — better one-line warn than a top-level traceback.
        print(f"  ⚠️ npx not found on PATH; install Node.js to enable lark-cli")
        return None
    except OSError as e:
        # Other Popen-time OS failures (permission, fork failed, etc.).
        # Caller will see None and propagate as "send failed".
        print(f"  ⚠️ lark-cli could not be launched: {e}")
        return None
    if r.returncode != 0:
        # lark-cli sometimes prints structured JSON
        # ({"ok":false,"msg":"invalid receive_id","code":230001}) to
        # stdout AND exits non-zero. A naive `stderr.splitlines()[-1]`
        # returns just the trailing `}` and loses the real cause. Try
        # JSON first (stdout, then stderr); fall back to the first
        # non-empty line.
        for blob in (r.stdout, r.stderr):
            blob = (blob or "").strip()
            if not blob:
                continue
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                reason = _extract_error_message(parsed)
                print(f"  ⚠️ lark-cli failed (rc={r.returncode}): {reason}"[:200])
                return None
        head = _plain_error_preview(r.stdout, r.stderr)
        print(f"  ⚠️ lark-cli failed (rc={r.returncode}): {head}"[:200])
        return None
    if not r.stdout.strip():
        return {}
    try:
        full = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        # Don't silently swallow — JSON corruption from lark-cli is rare
        # but when it happens, the operator wants to know (typically means
        # lark-cli printed banner text into stdout, or got proxied to an
        # auth wall). One-line preview helps debugging without flooding
        # the daemon log.
        preview = r.stdout.strip().splitlines()[0][:120] if r.stdout.strip() else "(empty)"
        print(f"  ⚠️ lark-cli returned non-JSON ({e}): {preview}")
        return None
    # lark-cli wraps results in {"ok": ..., "data": ...} or returns data directly.
    # `ok: false` means the API returned an error even though lark-cli exited 0.
    if isinstance(full, dict) and full.get("ok") is False:
        reason = _extract_error_message(full)
        print(f"  ⚠️ lark-cli API error: {reason}"[:200])
        return None
    return full.get("data", full)


def _extract_error_message(full: dict) -> str:
    """Pull the most informative human-readable string out of lark-cli's
    error-shape variants. Real responses seen in the wild:

      {"ok": false, "msg": "plain message"}
      {"ok": false, "error": "plain string"}
      {"ok": false, "error": {"type": "validation", "message": "..."}}
      {"ok": false, "error": {"type": "api_error", "code": 230002,
                              "message": "HTTP 400: Bot/User can NOT be out of the chat."}}

    When error is a structured dict, a naive `or "?"` chain returns the
    dict and the warning line prints `{'type': ..., 'message': '...'}` —
    useless to operators. Extract `error.message` when error is a dict,
    falling back through msg / code / "?" if nothing useful is present.
    """
    if msg := full.get("msg"):
        return str(msg)
    err = full.get("error")
    if isinstance(err, dict):
        # Prefer message; tag with type/code if present so the line
        # gives operators both the human string AND the API code.
        message = err.get("message") or err.get("code") or "?"
        kind = err.get("type")
        return f"{message} (type={kind})" if kind else str(message)
    if isinstance(err, str) and err:
        return err
    if code := full.get("code"):
        return str(code)
    return "?"


def _plain_error_preview(stdout: str | None, stderr: str | None) -> str:
    """Pick the useful line from non-JSON lark-cli failures.

    Some commands print progress first, then the real cause, e.g.
    `uploading image: foo.png` followed by `Error: image size ...`.
    Prefer explicit Error lines so the audit log points at the fix.
    """
    lines = [
        line.strip()
        for line in f"{stderr or ''}\n{stdout or ''}".splitlines()
        if line.strip()
    ]
    if not lines:
        return ""
    for line in lines:
        if line.lower().startswith("error:"):
            return line
    return lines[-1]
