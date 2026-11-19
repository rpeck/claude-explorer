"""Asserts the cc_image_watcher -> cc_watcher hard-cut rename (Task B3).

Two-fold tombstone:

1. ``cc_watcher`` is importable and exposes the public surface
   (``run_watcher``) the lifespan + installed launchers depend on.
2. ``cc_image_watcher`` is gone from the source tree -- no shim, no
   alias, no stale string-literal reference. The repo-wide grep
   defends against accidental reintroduction via lazy imports,
   string-literal launcher templates, or comment drift.

This file is the only permitted occurrence of the old module name in
the repo; the grep test self-excludes by path.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

# Directories under the repo root that may legitimately contain ``.py``
# files we should NOT scan (caches, build artifacts, vendored deps).
_SCAN_EXCLUDE_DIRS = frozenset({
    ".venv",
    "venv",
    "ENV",
    ".uv",
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".eggs",
    ".tox",
    "frontend",  # JS only; defensive
    # Nested git worktrees are other checkouts, with their own copy of
    # this file; the self-exclusion by path cannot see those copies.
    ".worktrees",
    "worktrees",
})

# The only permitted occurrence of the old module name. Resolved at
# scan time so a symlinked test path still self-excludes.
_SELF_PATH = Path(__file__).resolve()

# Repo root: this file lives at ``backend/tests/test_module_renames.py``.
_REPO_ROOT = _SELF_PATH.parents[2]


def test_cc_watcher_module_exists() -> None:
    """The new module path is discoverable and exposes ``run_watcher``."""
    spec = importlib.util.find_spec("backend.cc_watcher")
    assert spec is not None, (
        "backend.cc_watcher must be importable after Task B3"
    )
    mod = importlib.import_module("backend.cc_watcher")
    assert hasattr(mod, "run_watcher"), (
        "backend.cc_watcher must expose run_watcher "
        "(used by backend/main.py lifespan and cli/watcher.py "
        "install-watcher launcher template)"
    )


def test_cc_image_watcher_removed() -> None:
    """The old module path is gone (no shim, no alias).

    Uses ``find_spec`` to assert discoverability rather than relying
    on ``ModuleNotFoundError`` alone -- the latter can be raised from
    INSIDE an existing module whose own imports are broken, which
    would give a false positive that the module is gone.
    """
    spec = importlib.util.find_spec("backend.cc_image_watcher")
    assert spec is None, (
        f"backend.cc_image_watcher must not be importable after "
        f"Task B3 (hard cut, no shim). Got spec: {spec!r}"
    )


def _iter_scanned_py_files(root: Path):
    """Yield every ``.py`` file under ``root`` honoring the exclude list."""
    for path in root.rglob("*.py"):
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in _SCAN_EXCLUDE_DIRS for part in rel_parts):
            continue
        if path.resolve() == _SELF_PATH:
            continue
        yield path


def test_no_stale_imports() -> None:
    """No ``.py`` file in the source tree references the old module name.

    Catches every form: ``from backend.cc_image_watcher``,
    ``import backend.cc_image_watcher``, dynamic
    ``importlib.import_module("backend.cc_image_watcher")``, string
    literals (e.g. the install-watcher launcher template body in
    ``fetcher/cli.py``), and comment / docstring references.
    """
    offenders: list[str] = []
    for py_file in _iter_scanned_py_files(_REPO_ROOT):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "cc_image_watcher" in text:
            offenders.append(str(py_file.relative_to(_REPO_ROOT)))
    assert not offenders, (
        "Stale `cc_image_watcher` references found in:\n  - "
        + "\n  - ".join(sorted(offenders))
        + "\nUpdate them to `cc_watcher` per Task B3 (hard cut, no shim)."
    )


def test_scan_skips_nested_worktrees(tmp_path: Path) -> None:
    """A nested worktree is another checkout, not this source tree."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "kept.py").write_text("x = 1\n")
    for nested in (".worktrees/feat/x", "worktrees/feat/y"):
        copy = tmp_path / nested / "backend" / "tests" / "copy.py"
        copy.parent.mkdir(parents=True)
        copy.write_text("cc_image_watcher\n")
    scanned = {p.relative_to(tmp_path).as_posix() for p in _iter_scanned_py_files(tmp_path)}
    assert scanned == {"backend/kept.py"}
