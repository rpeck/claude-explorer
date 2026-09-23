"""The watcher's unit-file paths must follow ``Path.home()`` at call time.

Pre-fix bug (found 2026-09-23): ``cli/watcher.py`` computed
``_LAUNCHD_PLIST_PATH``, ``_SYSTEMD_UNIT_PATH`` and
``_WATCHER_LAUNCHER_PATH`` once, at import time. A test that patched
``HOME`` and then ran ``install-watcher --uninstall`` still pointed at
the developer's REAL home. On a machine with the watcher installed, one
local ``pytest`` run unloaded the launchd job and deleted its plist. The
user then saw the "watcher not installed" banner, and Claude Code
screenshots were at risk until they reinstalled.

These tests pin two things:

* Each path resolves under the patched home.
* The uninstall functions act on the patched home only. Every
  ``subprocess.run`` argument that looks like a path stays inside
  ``tmp_path``.

They call the per-platform functions directly with a fake
``subprocess.run``, so they run identically on macOS, Linux and Windows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tests._platform_home import patch_home


def _record_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    from cli import watcher as watcher_mod

    calls: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        calls.append([str(c) for c in cmd])
        return _Done()

    monkeypatch.setattr(watcher_mod.subprocess, "run", fake_run)
    return calls


def _assert_paths_inside(calls: list[list[str]], root: Path) -> None:
    for cmd in calls:
        for arg in cmd:
            # Bare unit names such as "x.service" are not paths.
            if Path(arg).is_absolute():
                assert Path(arg).is_relative_to(root), (
                    f"subprocess call {cmd!r} touches {arg!r}, outside the "
                    f"patched home {root}"
                )


def test_watcher_paths_resolve_under_patched_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cli import watcher as watcher_mod

    patch_home(monkeypatch, tmp_path)
    for path in (
        watcher_mod._launchd_plist_path(),
        watcher_mod._systemd_unit_path(),
        watcher_mod._watcher_launcher_path(),
    ):
        assert path.is_relative_to(tmp_path), (
            f"{path} ignores the patched home {tmp_path}"
        )


def test_uninstall_macos_acts_on_patched_home_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cli import watcher as watcher_mod

    patch_home(monkeypatch, tmp_path)
    calls = _record_subprocess(monkeypatch)
    plist = tmp_path / "Library" / "LaunchAgents" / (
        f"{watcher_mod._LAUNCHD_LABEL}.plist"
    )
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>")

    watcher_mod._uninstall_macos()

    assert not plist.exists(), "uninstall must remove the plist in the patched home"
    _assert_paths_inside(calls, tmp_path)


def test_uninstall_linux_acts_on_patched_home_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cli import watcher as watcher_mod

    patch_home(monkeypatch, tmp_path)
    calls = _record_subprocess(monkeypatch)
    unit = tmp_path / ".config" / "systemd" / "user" / watcher_mod._SYSTEMD_UNIT_NAME
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\n")
    launcher = tmp_path / ".claude-explorer" / "cc-watcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# stub")

    watcher_mod._uninstall_linux()

    assert not unit.exists(), "uninstall must remove the unit in the patched home"
    assert not launcher.exists(), "uninstall must remove the launcher in the patched home"
    _assert_paths_inside(calls, tmp_path)
