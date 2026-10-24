"""Regression test for v1.0.9: `claude-explorer install-watcher` on Windows
MUST register the scheduled task WITHOUT requiring elevation.

Pre-fix bug (v1.0.7 / v1.0.8):
    ``cli/watcher.py:_install_windows`` passed ``/RL HIGHEST`` to
    ``schtasks /Create``. That flag tells Task Scheduler to run the
    registered task with the highest privileges available to the user.
    BUT — ``schtasks /Create`` with ``/RL HIGHEST`` requires elevation to
    register. Non-admin PowerShell sessions fail with
    ``ERROR: Access is denied``. Discovered during cross-platform
    install verification on Windows 11 ARM64 (Phase C2-W, 2026-06-13).

    The watcher only touches user-owned files
    (``~\\.claude-explorer\\``, the CC image cache under ``~\\.claude\\``).
    It does NOT need elevation. ``/RL HIGHEST`` was a copy-paste from
    a more security-restrictive template that doesn't apply here.

The fix drops ``/RL HIGHEST``; the task registers at default user
run-level, which is sufficient for the file copies the watcher does.
This test pins that contract: any future PR that re-adds ``/RL HIGHEST``
(or any other admin-requiring flag) fails this test.

The test runs on all platforms (not Windows-only) because we mock
out subprocess.run + the launcher write; the test exercises only the
command-shape contract, which is platform-independent code.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_install_windows_does_not_request_elevation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The schtasks /Create command MUST NOT pass /RL HIGHEST."""

    from cli import watcher as watcher_mod

    # Stub _write_watcher_launcher so we don't actually touch ~/.claude-explorer/
    # (and so the test runs identically on Linux/macOS CI).
    fake_launcher = tmp_path / "cc-watcher.py"
    fake_launcher.write_text("# stub")
    monkeypatch.setattr(
        watcher_mod, "_write_watcher_launcher", lambda interval: fake_launcher
    )

    # Capture subprocess calls without actually running them. _install_windows
    # checks .returncode on the schtasks /Create result; the second call
    # (schtasks /Run) doesn't care about return value (it's `check=False`).
    calls: list[list[str]] = []

    class _FakeCompleted:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    def _fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(watcher_mod.subprocess, "run", _fake_run)

    watcher_mod._install_windows(
        python_bin=str(tmp_path / "python.exe"), interval=600.0
    )

    assert calls, (
        "No subprocess.run calls captured; _install_windows did not invoke schtasks"
    )
    create_cmd = calls[0]
    assert create_cmd[0:2] == ["schtasks", "/Create"], (
        f"First subprocess call was not schtasks /Create: {create_cmd}"
    )

    # Required flags MUST remain.
    assert "/TN" in create_cmd, f"/TN missing from schtasks /Create: {create_cmd}"
    assert "/TR" in create_cmd, f"/TR missing from schtasks /Create: {create_cmd}"
    assert "/SC" in create_cmd, f"/SC missing from schtasks /Create: {create_cmd}"
    assert "ONLOGON" in create_cmd, (
        f"ONLOGON missing from schtasks /Create: {create_cmd}"
    )
    assert "/F" in create_cmd, f"/F (force overwrite) missing: {create_cmd}"

    # The bug: /RL HIGHEST requires elevation and breaks install-watcher
    # on non-admin PowerShell. The fix drops it.
    assert "/RL" not in create_cmd, (
        f"/RL flag present in schtasks /Create command — this requires "
        f"elevation to register and breaks non-admin install. Drop /RL "
        f"(and HIGHEST) to register at default user run-level. "
        f"Captured cmd: {create_cmd}"
    )
    assert "HIGHEST" not in create_cmd, (
        f"HIGHEST value present in schtasks /Create command — see /RL "
        f"assertion above. Captured cmd: {create_cmd}"
    )
