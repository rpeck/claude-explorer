"""Canary: the fetcher suite never touches the real home folders.

On 2026-11-27 each `claude-explorer fetch` test of this suite copied the real
~/.claude/projects into a new machines/<id>/ folder of the real archive: the
fetcher suite had no folder isolation, and `fetch` runs the archive copy
before it loads the credentials. The isolation now lives in the root
conftest.py. These tests fail if this suite loses it again.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from cli.main import main

_FOLDER_VARS = ("CLAUDE_EXPLORER_DATA_DIR", "CLAUDE_DIR", "CLAUDE_DESKTOP_APP_DIR", "CODEX_HOME")


def _real_home() -> Path:
    """The user's home from the OS account, not from HOME (a test can change HOME)."""
    if sys.platform == "win32":
        return Path(os.environ.get("USERPROFILE") or Path.home())
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _inside(path: str | Path, root: Path) -> bool:
    return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root))


def _machine_folders() -> list[str]:
    """The names (never the contents) in the real archive's machines folder."""
    try:
        return sorted(os.listdir(_real_home() / ".claude-explorer" / "conversations" / "machines"))
    except OSError:
        return []


def test_every_folder_variable_points_into_the_test_temp_area(tmp_path_factory: pytest.TempPathFactory) -> None:
    base = tmp_path_factory.getbasetemp()
    for name in _FOLDER_VARS:
        value = os.environ.get(name)
        assert value, f"{name} is not set for this test"
        assert _inside(value, base), f"{name}={value} is outside the test temp area {base}"


def test_cli_fetch_copies_only_inside_the_test_temp_area(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fetch` runs the archive copy (before the credentials load); every folder it uses is a temp folder.

    Only the names of the real machines folder are compared: the user's own
    watcher can write in the real archive while the suite runs, so file
    times there are not stable.
    """
    from backend import write_gate

    seen: list[dict[str, Any]] = []
    real_run = write_gate.run_archive_syncs

    def spy(store, reason, lock_timeout_s):  # type: ignore[no-untyped-def]
        from backend.config import get_settings

        seen.append({
            "data_dir": store.data_dir, "claude_dir": store.claude_dir,
            "cowork_root": store.cowork_root, "codex_home": get_settings().codex_home,
        })
        return real_run(store, reason, lock_timeout_s)

    monkeypatch.setattr(write_gate, "run_archive_syncs", spy)
    before = _machine_folders()

    result = CliRunner().invoke(main, ["fetch", "--credentials", str(tmp_path / "missing.json")])

    assert seen, f"the archive copy did not run, so this canary proves nothing: {result.output!r}"
    base = tmp_path_factory.getbasetemp()
    for folders in seen:
        for name, path in folders.items():
            if path is not None:
                assert _inside(path, base), f"fetch used the real {name}: {path}"
    assert _machine_folders() == before, "fetch added a folder to the real archive's machines folder"
