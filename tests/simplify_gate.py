"""Pre-PR / pre-commit simplicity gate for ClaudeTeam.

Mechanical checks for the 5 CLAUDE.md simplicity rules + 4 project red
flags.  Designed to catch "design drift" before merging large
AI-written PRs — the kind that pass code review but violate the
project's own 300-line ceiling, two-use rule, or the "explain what
this check actually detects" discipline.

The 7 mechanical rules (each named in CLAUDE.md or in the project
review thread):

    R300            file > 300 LOC (CLAUDE.md "Single-file ceiling")
    TWO_USE         module has < 3 external importers (Two-use rule)
    IMPORT_HEAVY    commands/*.py imports subprocess/urllib/requests/socket
    SWALLOWED_EXCEPT  bare except: pass / continue (silent failures)
    DEAD_PRIVATE    private function with 0 external callers
    LONG_UNCOMMENTED  function body block > 30 lines without a comment
    META_HEALTH     check/audit/monitor/health function without
                    a docstring that says what it actually detects

Usage:
    python3 tests/simplify_gate.py            # advisory (exit 0)
    python3 tests/simplify_gate.py --strict   # exit 1 if any finding
    python3 tests/simplify_gate.py --json     # machine-readable
    python3 tests/simplify_gate.py --quiet    # suppress per-finding output

Importable: `from tests.simplify_gate import scan, Finding` works
because `tests/run.py` adds the tests/ root to sys.path.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# CLAUDE.md "Single-file ceiling: ~300 LOC"
R300_CEILING = 300

# CLAUDE.md "Two-use rule" — helpers earn at 3rd call site
TWO_USE_MIN = 3

# Project: a 30+ line block of code without a comment = "who is this for?"
LONG_UNCOMMENTED_THRESHOLD = 30

# Heavy imports that should be encapsulated in runtime/*, not sprinkled
# in commands/.  When a command file imports these, it usually means
# the command is doing I/O directly instead of going through a runtime
# helper, which is the "let a thousand I/O styles bloom" anti-pattern.
HEAVY_IMPORTS = frozenset({"subprocess", "urllib", "requests", "socket"})

# Function-name patterns that suggest "is this real or theater?"
# Lowercased: a function called `check_x` / `_audit` / `_monitor_y` /
# `health_z` is making a claim about the world.  CLAUDE.md has no rule
# for this but the project review (2026-06-12) flagged
# `evolution_health.py` as import-check theater — this rule is the
# mechanical version of that instinct.
META_HEALTH_NAMES = re.compile(
    r"^(check|audit|monitor|health|verify|validate|inspect)_|"
    r"_(check|audit|monitor|health|verify|validate|inspect)$"
)


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str          # "warn" | "block"
    file: str              # repo-relative path
    line: int              # 1-indexed; 0 if N/A
    message: str

    def short(self) -> str:
        loc = f":{self.line}" if self.line else ""
        return f"  {self.rule:<18} {self.file}{loc}  {self.message}"


# ─── Helpers ───────────────────────────────────────────────────────
def _iter_py(src_root: Path) -> list[Path]:
    return [p for p in sorted(src_root.rglob("*.py")) if p.name != "__init__.py"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _rel(path: Path) -> str:
    """Path relative to ROOT, or absolute if `path` is outside (e.g.
    a test fixture under /tmp).  Findings from non-project trees
    keep their absolute path so the test can still inspect them."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# ─── R300: file > 300 LOC ──────────────────────────────────────────
def _r300(src_root: Path) -> list[Finding]:
    out: list[Finding] = []
    for path in _iter_py(src_root):
        with path.open("rb") as fh:
            line_count = sum(1 for _ in fh)
        if line_count > R300_CEILING:
            out.append(Finding(
                "R300", "warn",
                _rel(path), 0,
                f"{line_count} lines (ceiling {R300_CEILING}); "
                f"split or explain 'why one file' in PR body",
            ))
    return out


# ─── TWO_USE: module < 3 external importers ───────────────────────
def _importers_of(target: Path, src_root: Path) -> set[str]:
    """Find files outside `target` that import from target.

    Matches all of:
        from pkg.target import X
        from .target import X
        from . import target
        import pkg.target
        import pkg.target.X as Y
    """
    stem = target.stem
    rel = target.relative_to(src_root).with_suffix("")
    parts = rel.parts
    dotted_abs = ".".join(parts)              # pkg.commands.lonely
    dotted_parent = ".".join(parts[:-1])      # pkg.commands
    found: set[str] = set()
    for other in _iter_py(src_root):
        if other == target:
            continue
        text = _read(other)
        for pat in (
            # from <abs> import …
            rf"^\s*from\s+{re.escape(dotted_abs)}\s+import\b",
            # from .<tail> import …  (relative import to target)
            rf"^\s*from\s+\.*{re.escape('.'.join(parts[-2:]))}\s+import\b",
            # from <parent> import <stem> …
            rf"^\s*from\s+{re.escape(dotted_parent)}\s+import\s+[^()\n]*\b{re.escape(stem)}\b",
            # from .<parent> import <stem> …
            rf"^\s*from\s+\.*{re.escape(parts[-2])}\s+import\s+[^()\n]*\b{re.escape(stem)}\b",
            # import <abs>
            rf"^\s*import\s+{re.escape(dotted_abs)}\b",
            # import <parent>.<stem>
            rf"^\s*import\s+[\w.]*{re.escape(stem)}\b",
        ):
            if re.search(pat, text, re.MULTILINE):
                found.add(_rel(other))
                break
    return found


def _two_use(src_root: Path) -> list[Finding]:
    out: list[Finding] = []
    for path in _iter_py(src_root):
        importers = _importers_of(path, src_root)
        # cli.py is a registration surface, not a "use".  A command
        # module only registered in cli.py with no other callers is
        # dead-but-listed.  For non-command modules, all importers count.
        is_command = "commands" in path.parts
        non_cli = {c for c in importers if not c.endswith("cli.py")} if is_command else importers
        if is_command and len(non_cli) == 0 and len(importers) > 0:
            out.append(Finding(
                "TWO_USE", "warn",
                _rel(path), 0,
                "command module: only registered in cli.py; "
                "no other module calls into it (dead-but-listed)",
            ))
        elif not is_command and 0 < len(importers) < TWO_USE_MIN:
            out.append(Finding(
                "TWO_USE", "warn",
                _rel(path), 0,
                f"only {len(importers)} external importer(s); "
                f"inline the helper if a third use doesn't materialize",
            ))
    return out


# ─── IMPORT_HEAVY: commands/ importing low-level libs ─────────────
def _import_heavy(src_root: Path) -> list[Finding]:
    out: list[Finding] = []
    # Find any `commands/` directory under src_root, not just the
    # top-level one.  The actual project has src/claudeteam/commands/.
    for cmd_dir in src_root.rglob("commands"):
        if not cmd_dir.is_dir():
            continue
        # Skip helper packages that happen to be named `commands`
        if "test" in cmd_dir.parts:
            continue
        for path in sorted(cmd_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            try:
                tree = ast.parse(_read(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                top: str | None = None
                if isinstance(node, ast.Import):
                    top = node.names[0].name.split(".")[0]
                elif isinstance(node, ast.ImportFrom):
                    top = (node.module or "").split(".")[0]
                if top and top in HEAVY_IMPORTS:
                    out.append(Finding(
                        "IMPORT_HEAVY", "warn",
                        _rel(path), node.lineno,
                        f"imports `{top}` directly; route through runtime/* "
                        f"so commands/ stays orchestration-only",
                    ))
    return out


# ─── SWALLOWED_EXCEPT: bare except: pass / continue ───────────────
_EXCEPT_PASS = re.compile(
    r"except[^:]*:\s*\n\s*(?:pass|continue)\b"
)
def _swallowed_except(src_root: Path) -> list[Finding]:
    out: list[Finding] = []
    for path in _iter_py(src_root):
        text = _read(path)
        for m in _EXCEPT_PASS.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append(Finding(
                "SWALLOWED_EXCEPT", "warn",
                _rel(path), line,
                "silent except: pass / continue; "
                "log it or re-raise — the reviewer can't tell why it's swallowed",
            ))
    return out


def _resolve_tests_root(src_root: Path) -> Path | None:
    """Return the tests/ dir to scan alongside `src_root`, or None.

    Only scan the project's tests/ when we're actually scanning the
    project's src/.  For test fixtures under /tmp, don't leak the
    project's tests in — the rule should be self-contained.
    """
    if src_root.resolve() == SRC.resolve():
        tests_root = ROOT / "tests"
        return tests_root if tests_root.is_dir() else None
    return None


# ─── DEAD_PRIVATE: private def referenced only by its own file ────
def _dead_private(src_root: Path) -> list[Finding]:
    out: list[Finding] = []
    # CLAUDE.md: "If `grep -rn '\\b_fn\\b'` shows only the definition,
    # remove it."  The mechanical proxy: walk every AST and collect
    # (1) the set of private defs, (2) the set of *referenced* names
    # anywhere in the project.  A private def is dead iff its name is
    # not in the reference set.
    #
    # "Referenced" means ANY use other than its own definition:
    #   - called: `_foo()` / `self._foo()` / `(x or _foo)()`
    #   - stored: `dict["/help"] = _foo` / `_DISPATCH = [_foo, ...]`
    #   - default: `def f(cb=_foo): ...`
    #   - assigned: `bar = _foo`
    #   - compared: `if _foo:` / `name == "_foo"`
    #   - decorated: `@_foo`
    #   - re-exported: `__all__ = ["_foo"]`
    # Tests count — a private helper exercised by tests is load-bearing.
    private_defs: dict[Path, list[tuple[str, int]]] = {}
    # ref_names: simple names referenced anywhere
    ref_names: set[str] = set()
    # ref_attrs: attribute names accessed anywhere (`self._foo` → "_foo")
    ref_attrs: set[str] = set()

    tests_root = _resolve_tests_root(src_root)
    roots_to_scan = [src_root]
    if tests_root is not None:
        roots_to_scan.append(tests_root)
    all_py: list[Path] = [p for root in roots_to_scan
                          for p in _iter_py(root)]

    for path in all_py:
        try:
            tree = ast.parse(_read(path))
        except SyntaxError:
            continue
        # Identify docstring regions to exclude
        docstring_ranges: list[tuple[int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    docstring_ranges.append(
                        (node.body[0].lineno, getattr(node.body[0], "end_lineno", node.body[0].lineno))
                    )
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_") and not node.name.startswith("__"):
                    private_defs.setdefault(path, []).append(
                        (node.name, node.lineno)
                    )
            # Collect all Name and Attribute references
            for child in ast.walk(node):
                # Skip names inside docstrings
                if isinstance(child, ast.Name) and _in_docstring(child.lineno, docstring_ranges):
                    continue
                if isinstance(child, ast.Name):
                    # Exclude the function/class name in its own def
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                            and child is node.name and isinstance(child, ast.Name):
                        continue
                    # Exclude import targets
                    if isinstance(node, (ast.Import, ast.ImportFrom, ast.alias)) and child is node:
                        continue
                    ref_names.add(child.id)
                elif isinstance(child, ast.Attribute):
                    if _in_docstring(child.lineno, docstring_ranges):
                        continue
                    ref_attrs.add(child.attr)
    for path, defs in private_defs.items():
        for name, lineno in defs:
            if name in ref_names or name in ref_attrs:
                continue
            # Don't flag private defs that live OUTSIDE the scan root
            # (those are project-internal dead code, not the caller's
            # concern when they're testing their own tree).
            if not _is_under(path, src_root):
                continue
            out.append(Finding(
                "DEAD_PRIVATE", "warn",
                _rel(path), lineno,
                f"private `{name}` has 0 references in src/ or tests/; "
                f"delete or move to a one-off script",
            ))
    return out


def _in_docstring(lineno: int, ranges: list[tuple[int, int]]) -> bool:
    for lo, hi in ranges:
        if lo <= lineno <= hi:
            return True
    return False


def _is_under(path: Path, root: Path) -> bool:
    """True if `path` is `root` or a descendant of `root`."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# ─── LONG_UNCOMMENTED: 30+ line block in a function without comment ─
def _long_uncommented(src_root: Path) -> list[Finding]:
    out: list[Finding] = []
    for path in _iter_py(src_root):
        try:
            src = _read(path)
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.body:
                continue
            # Walk the body statements; a "comment break" is any stmt
            # whose first source line starts with `#`.  We measure the
            # longest run of consecutive non-comment statement spans.
            run = 0
            run_start = node.body[0].lineno
            worst_run = 0
            worst_start = run_start
            for stmt in node.body:
                end = getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno
                has_comment = any(
                    lines[i].lstrip().startswith("#")
                    for i in range(stmt.lineno - 1, min(end, len(lines)))
                )
                if has_comment:
                    run = 0
                    run_start = end + 1
                else:
                    block = end - stmt.lineno + 1
                    run += block
                    if run > worst_run:
                        worst_run = run
                        worst_start = run_start
            if worst_run > LONG_UNCOMMENTED_THRESHOLD:
                out.append(Finding(
                    "LONG_UNCOMMENTED", "warn",
                    _rel(path), worst_start,
                    f"function `{node.name}` has a {worst_run}-line block "
                    f"without a comment; explain intent or split the function",
                ))
    return out


# ─── META_HEALTH: check/audit/monitor function without docstring ─
def _meta_health(src_root: Path) -> list[Finding]:
    out: list[Finding] = []
    for path in _iter_py(src_root):
        try:
            tree = ast.parse(_read(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not META_HEALTH_NAMES.search(node.name):
                continue
            # Has a leading docstring?
            if node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(node.body[0].value, ast.Constant) \
                    and isinstance(node.body[0].value.value, str) \
                    and node.body[0].value.value.strip():
                continue
            out.append(Finding(
                "META_HEALTH", "warn",
                _rel(path), node.lineno,
                f"`{node.name}` looks like a check/audit/monitor; "
                f"docstring must say what it actually detects "
                f"(or it will become import-check theater)",
            ))
    return out
RULES: list[Callable[[Path], list[Finding]]] = [
    _r300,
    _two_use,
    _import_heavy,
    _swallowed_except,
    _dead_private,
    _long_uncommented,
    _meta_health,
]


def scan(src_root: Path = SRC) -> list[Finding]:
    """Run all rules and return a sorted list of findings."""
    out: list[Finding] = []
    for rule in RULES:
        out.extend(rule(src_root))
    out.sort(key=lambda f: (f.file, f.line, f.rule))
    return out


def _format_text(findings: list[Finding]) -> str:
    if not findings:
        return "simplify_gate: clean"
    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
    lines = [f"simplify_gate: {len(findings)} finding(s) — "
             + ", ".join(f"{r}={n}" for r, n in sorted(by_rule.items()))]
    for f in findings:
        lines.append(f.short())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mechanical CLAUDE.md simplicity gate + project red flags",
    )
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any findings")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON output")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-finding output")
    parser.add_argument("--src", default=str(SRC),
                        help=f"source root to scan (default: {SRC})")
    args = parser.parse_args(argv)

    findings = scan(Path(args.src))

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    elif not args.quiet:
        print(_format_text(findings))

    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
