"""Every by-value copy of DEFAULT_CREDENTIALS_PATH must be in the fixture.

Earned 2026-09-25. ``_isolated_credentials_path`` patched five bindings,
but eight modules import the constant by value at module load.
``backend.routers.files`` and ``fetcher.mitmproxy_addon`` read their copy
at call time and were not patched, so a test that went through them read
the developer's real ``~/.claude-explorer/credentials.json``.

This test reads each module's top-level imports and fails when a module
holds a copy that ``CREDENTIALS_PATH_BINDINGS`` does not name. An import
inside a function is fine: it reads the patched source when it runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.tests.conftest import CREDENTIALS_PATH_BINDINGS

REPO = Path(__file__).resolve().parents[2]
PACKAGES = ("backend", "cli", "fetcher", "mcp_server")
NAME = "DEFAULT_CREDENTIALS_PATH"


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def _top_level_importers() -> set[str]:
    found: set[str] = set()
    for package in PACKAGES:
        for path in (REPO / package).rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:  # module level only
                if isinstance(node, ast.ImportFrom) and any(
                    alias.name == NAME for alias in node.names
                ):
                    found.add(_module_name(path))
    return found


def test_every_module_level_copy_is_patched() -> None:
    patched = {target.rsplit(".", 1)[0] for target in CREDENTIALS_PATH_BINDINGS}
    missing = _top_level_importers() - patched
    assert not missing, (
        f"These modules import {NAME} by value, but "
        f"CREDENTIALS_PATH_BINDINGS in backend/tests/conftest.py does not "
        f"patch them: {sorted(missing)}. Add them, or tests that reach them "
        f"read the real credentials file."
    )


def test_the_scan_finds_the_known_importers() -> None:
    """Guard the guard: an empty scan would pass the test above vacuously."""
    found = _top_level_importers()
    assert {"backend.routers.fetch", "backend.routers.files", "fetcher.bulk_fetch"} <= found


def test_every_binding_names_a_real_module() -> None:
    """A stale entry hides behind raising=False, so check each one here."""
    for target in CREDENTIALS_PATH_BINDINGS:
        module = target.rsplit(".", 1)[0]
        path = REPO.joinpath(*module.split(".")).with_suffix(".py")
        assert path.is_file(), f"{target}: no module at {path}"
