"""No return, break or continue may leave a ``finally`` block.

Earned 2026-10-13. A user's install of 1.1.1 ran on Python 3.15 and
printed ``SyntaxWarning: 'return' in a 'finally' block`` from
``backend/main.py``. Python 3.14 added that warning (PEP 765) because such
a statement silently discards any exception that is in flight: in the
lifespan shutdown, an error raised during the app's run would vanish.

CI tested only Python 3.13, which does not warn. This test reads the
syntax tree instead, so it catches the problem on every Python version.

Allowed, as in PEP 765:
* control flow inside a function defined within the ``finally``;
* ``break``/``continue`` for a loop that is itself inside the ``finally``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGES = ("backend", "cli", "fetcher", "mcp_server")


def _escapes(finalbody: list[ast.stmt]) -> list[ast.stmt]:
    """Statements in a finally body that transfer control out of it."""
    found: list[ast.stmt] = []

    def visit(node: ast.AST, in_loop: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return  # a nested scope may use control flow freely
        if isinstance(node, ast.Return):
            found.append(node)
        elif isinstance(node, (ast.Break, ast.Continue)) and not in_loop:
            found.append(node)
        loop = isinstance(node, (ast.For, ast.AsyncFor, ast.While))
        for child in ast.iter_child_nodes(node):
            # A loop's orelse runs after the loop, so it is not inside it.
            inner = in_loop or (loop and child not in getattr(node, "orelse", []))
            visit(child, inner)

    for stmt in finalbody:
        visit(stmt, in_loop=False)
    return found


def _violations(source: str) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Try, ast.TryStar)) and node.finalbody:
            lines.extend(stmt.lineno for stmt in _escapes(node.finalbody))
    return sorted(lines)


def test_no_control_flow_leaves_a_finally_block() -> None:
    problems = []
    for package in PACKAGES:
        for path in sorted((REPO / package).rglob("*.py")):
            for line in _violations(path.read_text(encoding="utf-8")):
                problems.append(f"{path.relative_to(REPO)}:{line}")
    assert not problems, (
        "return/break/continue leaves a finally block, which discards any "
        "in-flight exception (PEP 765; Python 3.14+ warns): " + ", ".join(problems)
    )


def test_the_checker_flags_and_allows_the_right_cases() -> None:
    """Guard the guard with known-bad and known-good shapes."""
    bad = "def f():\n    try:\n        pass\n    finally:\n        return\n"
    bad_break = "for x in y:\n    try:\n        pass\n    finally:\n        break\n"
    ok_loop = "def f():\n    try:\n        pass\n    finally:\n        for x in y:\n            continue\n"
    ok_nested = "def f():\n    try:\n        pass\n    finally:\n        def g():\n            return 1\n"
    assert _violations(bad) == [5]
    assert _violations(bad_break) == [5]
    assert _violations(ok_loop) == []
    assert _violations(ok_nested) == []
