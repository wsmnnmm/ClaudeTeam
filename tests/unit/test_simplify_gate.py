"""Unit tests for the simplify gate.

Each test builds a tiny synthetic tree under a temp dir, then runs
the rule(s) against it.  This keeps the tests fast and decoupled
from the actual project's file shape — they verify the rules fire
when they should and stay quiet when they shouldn't.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

# tests/ is on sys.path (tests/run.py puts it there)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simplify_gate import (  # noqa: E402
    Finding, _dead_private, _import_heavy, _long_uncommented, _meta_health,
    _r300, _swallowed_except, _two_use, main, scan,
)


def _write(root: Path, rel: str, body: str) -> Path:
    """Write `body` to `root/rel`, creating parent dirs as needed."""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return p


def _init_pkg(root: Path, pkg: str) -> None:
    """Create an empty __init__.py so the package imports cleanly."""
    (root / pkg).mkdir(parents=True, exist_ok=True)
    (root / pkg / "__init__.py").touch()


def _by_rule(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


# ─── R300 ──────────────────────────────────────────────────────────
def test_r300_flags_files_over_300_lines():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        # 301-line file
        _write(td.src, "pkg/big.py", "x = 1\n" * 301)
        findings = _r300(td.src)
    rules = [f.file for f in findings]
    assert any("big.py" in f for f in rules), findings


def test_r300_quiet_under_ceiling():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/small.py", "x = 1\n" * 50)
        findings = _r300(td.src)
    assert findings == []


# ─── TWO_USE ───────────────────────────────────────────────────────
def test_two_use_flags_command_module_with_only_cli_registration():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _init_pkg(td.src / "pkg" / "commands", "commands")
        # cli.py registers the command (imports it)
        _write(td.src, "pkg/cli.py", "from .commands import lonely\n")
        _write(td.src, "pkg/commands/__init__.py", "")
        _write(td.src, "pkg/commands/lonely.py",
               "def main(argv):\n    return 0\n")
        findings = _two_use(td.src)
    assert any("lonely.py" in f.file and "cli.py" in f.message
               for f in findings), findings


def test_two_use_quiet_when_3_callers():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        for i in range(3):
            _write(td.src, f"pkg/caller_{i}.py",
                   f"from pkg import util\nutil.thing()\n")
        _write(td.src, "pkg/util.py", "def thing(): return 1\n")
        findings = _two_use(td.src)
    # util.py is imported by 3 files → no warning
    util_findings = [f for f in findings if "util.py" in f.file]
    assert util_findings == [], util_findings


def test_two_use_flags_module_with_1_importer():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/caller.py", "from pkg import rare\nrare.thing()\n")
        _write(td.src, "pkg/rare.py", "def thing(): return 1\n")
        findings = _two_use(td.src)
    assert any("rare.py" in f.file for f in findings), findings


# ─── IMPORT_HEAVY ──────────────────────────────────────────────────
def test_import_heavy_flags_subprocess_in_command():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _init_pkg(td.src / "pkg" / "commands", "commands")
        _write(td.src, "pkg/commands/send.py",
               "import subprocess\ndef main(): return 0\n")
        findings = _import_heavy(td.src)
    assert any("send.py" in f.file and "subprocess" in f.message
               for f in findings), findings


def test_import_heavy_quiet_when_routed_through_runtime():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _init_pkg(td.src / "pkg" / "commands", "commands")
        _init_pkg(td.src / "pkg" / "runtime", "runtime")
        # runtime owns the subprocess import
        _write(td.src, "pkg/runtime/tmux.py",
               "import subprocess\ndef run(): return subprocess.run([])\n")
        # commands/ uses runtime, NOT subprocess directly
        _write(td.src, "pkg/commands/send.py",
               "from pkg.runtime import tmux\ndef main():\n    tmux.run()\n    return 0\n")
        findings = _import_heavy(td.src)
    assert all("send.py" not in f.file for f in findings), findings


# ─── SWALLOWED_EXCEPT ─────────────────────────────────────────────
def test_swallowed_except_flags_except_pass():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", textwrap.dedent("""\
            try:
                x = 1
            except Exception:
                pass
        """))
        findings = _swallowed_except(td.src)
    assert any("m.py" in f.file for f in findings), findings


def test_swallowed_except_flags_except_continue():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", textwrap.dedent("""\
            for x in items:
                try:
                    do(x)
                except ValueError:
                    continue
        """))
        findings = _swallowed_except(td.src)
    assert any("m.py" in f.file for f in findings), findings


def test_swallowed_except_quiet_when_logged():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", textwrap.dedent("""\
            try:
                x = 1
            except Exception as e:
                log.error("oops: %s", e)
        """))
        findings = _swallowed_except(td.src)
    assert findings == []


# ─── DEAD_PRIVATE ──────────────────────────────────────────────────
def test_dead_private_flags_function_with_no_callers():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", "def _orphan():\n    return 1\n")
        findings = _dead_private(td.src)
    assert any("_orphan" in f.message for f in findings), findings


def test_dead_private_quiet_when_called_internally():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", textwrap.dedent("""\
            def _helper():
                return 1

            def public():
                return _helper() + 1
        """))
        findings = _dead_private(td.src)
    assert not any("_helper" in f.message for f in findings), findings


def test_dead_private_quiet_when_called_via_test():
    """A test file in the same src/ tree that imports a private helper
    keeps it alive.  We model the "real" test-uses-helper relationship
    by putting a fake test file inside the same scan root, so the
    DEAD_PRIVATE rule sees the call site without having to reach into
    the real `tests/` directory (which would couple this test to the
    project tree)."""
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", "def _helper():\n    return 1\n")
        _write(td.src, "pkg/test_m.py",
               "from pkg.m import _helper\ndef test_it():\n"
               "    assert _helper() == 1\n")
        findings = _dead_private(td.src)
    # The test file is in the same src/ tree, so its call to
    # _helper() counts as a real call site.
    assert not any("_helper" in f.message for f in findings), findings


# ─── LONG_UNCOMMENTED ──────────────────────────────────────────────
def test_long_uncommented_flags_50_line_block_without_comment():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        body = "def big():\n" + "    x = {}\n" * 50 + "    return x\n"
        _write(td.src, "pkg/m.py", body)
        findings = _long_uncommented(td.src)
    assert any("big" in f.message for f in findings), findings


def test_long_uncommented_quiet_when_comments_interspersed():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        # 50 stmts, each followed by a comment → no long block
        body = "def big():\n" + ("    x = 1\n    # tally\n" * 25) + "    return x\n"
        _write(td.src, "pkg/m.py", body)
        findings = _long_uncommented(td.src)
    # Comments break the run; the longest contiguous run is < 30
    assert not any("big" in f.message for f in findings), findings


# ─── META_HEALTH ───────────────────────────────────────────────────
def test_meta_health_flags_check_function_without_docstring():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", "def check_x(state):\n    return True\n")
        findings = _meta_health(td.src)
    assert any("check_x" in f.message for f in findings), findings


def test_meta_health_quiet_when_docstring_present():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", textwrap.dedent('''\
            def check_x(state):
                """Returns True iff `state` has been verified end-to-end."""
                return True
        '''))
        findings = _meta_health(td.src)
    assert not any("check_x" in f.message for f in findings), findings


def test_meta_health_quiet_for_non_meta_names():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/m.py", "def render():\n    return 'x'\n")
        findings = _meta_health(td.src)
    assert findings == []


# ─── End-to-end: scan() + main() ──────────────────────────────────
def test_scan_returns_combined_findings_sorted_by_file():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _init_pkg(td.src / "pkg" / "commands", "commands")
        # R300 trigger
        _write(td.src, "pkg/commands/big.py", "x = 1\n" * 400 + "\n")
        # SWALLOWED_EXCEPT trigger
        _write(td.src, "pkg/commands/quiet.py",
               "try:\n    x = 1\nexcept Exception:\n    pass\n")
        findings = scan(td.src)
    files = [f.file for f in findings]
    # Both findings should appear, sorted by file then line
    assert files == sorted(files), findings
    assert any("big.py" in f for f in files)
    assert any("quiet.py" in f for f in files)


def test_main_advisory_returns_zero_with_findings():
    """Advisory mode (default) prints findings but exits 0."""
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/big.py", "x = 1\n" * 400 + "\n")
        rc = main(["--quiet", "--src", str(td.src)])
    assert rc == 0


def test_main_strict_returns_one_with_findings():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/big.py", "x = 1\n" * 400 + "\n")
        rc = main(["--strict", "--quiet", "--src", str(td.src)])
    assert rc == 1


def test_main_strict_returns_zero_when_clean():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/small.py", "x = 1\n")
        rc = main(["--strict", "--quiet", "--src", str(td.src)])
    assert rc == 0


def test_main_json_output_is_valid():
    with _TempDir() as td:
        _init_pkg(td.src, "pkg")
        _write(td.src, "pkg/big.py", "x = 1\n" * 400 + "\n")
        # Capture stdout
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--json", "--src", str(td.src)])
        data = json.loads(buf.getvalue())
    assert rc == 0
    assert isinstance(data, list)
    assert any(f["rule"] == "R300" for f in data)


# ─── Tempdir helper ────────────────────────────────────────────────
class _TempDir:
    """Context manager that exposes a fresh `src/` under a temp dir.

    The simplify_gate module is rooted at the actual project ROOT, so
    `_iter_py` walks whatever path it's given.  We pass `td.src` to
    every rule to keep tests isolated from the real project tree.
    """

    def __init__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "src"

    def __enter__(self):
        self.src.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()
