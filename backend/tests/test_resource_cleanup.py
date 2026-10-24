"""Code that opens a SQLite connection also closes it (2026-10-23).

``with sqlite3.connect(...) as conn:`` commits or rolls back at the end of
the block. It does not close the connection. Python 3.13 reports a
connection that nobody closed with a ``ResourceWarning`` when the object
goes away. The default warning filters hide it, so a test must show it.
"""

from __future__ import annotations

import gc
import warnings

from backend.search_index import fts5_available


def _resource_warnings(call) -> list[str]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        call()
        gc.collect()
    return [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]


def test_the_fts5_probe_closes_its_connection() -> None:
    assert _resource_warnings(fts5_available) == []


def test_no_code_uses_a_bare_sqlite_connection_as_a_context_manager() -> None:
    """``with sqlite3.connect(p) as conn:`` leaves the connection open.

    Use ``with closing(sqlite3.connect(p)) as conn, conn:`` instead: the
    first manager closes, and the second commits as before.
    """
    import re
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    files = subprocess.run(
        ["git", "ls-files", "-z", "*.py"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    pattern = re.compile(r"with\s+sqlite3\.connect\(")
    this_file = Path(__file__).resolve()
    hits = [
        f"{rel}:{n}"
        for rel in files
        if rel
        if (repo / rel).resolve() != this_file
        for n, line in enumerate((repo / rel).read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []
