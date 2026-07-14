from __future__ import annotations

from click.testing import CliRunner

import cli.main as cm
from cli.main import main


def _stub_os_helpers(monkeypatch) -> list:
    """Replace all six OS watcher helpers with recorders so no real
    launchd/systemd/schtasks call happens. Returns the call log."""
    calls = []
    for fn in ("_install_macos", "_install_linux", "_install_windows"):
        monkeypatch.setattr(cm, fn, lambda *a, _n=fn, **k: calls.append(_n))
    for fn in ("_uninstall_macos", "_uninstall_linux", "_uninstall_windows"):
        monkeypatch.setattr(cm, fn, lambda *a, _n=fn, **k: calls.append(_n))
    return calls


def test_install_group_lists_watcher(monkeypatch) -> None:
    res = CliRunner().invoke(main, ["install", "--help"])
    assert res.exit_code == 0
    assert "watcher" in res.output


def test_install_watcher_subcommand_runs_one_os_helper(monkeypatch) -> None:
    calls = _stub_os_helpers(monkeypatch)
    res = CliRunner().invoke(main, ["install", "watcher"])
    assert res.exit_code == 0
    # exactly one platform install helper fired
    assert len([c for c in calls if c.startswith("_install_")]) == 1


def test_install_watcher_uninstall_runs_one_os_helper(monkeypatch) -> None:
    calls = _stub_os_helpers(monkeypatch)
    res = CliRunner().invoke(main, ["install", "watcher", "--uninstall"])
    assert res.exit_code == 0
    assert len([c for c in calls if c.startswith("_uninstall_")]) == 1


def test_deprecated_alias_delegates_and_warns(monkeypatch) -> None:
    calls = _stub_os_helpers(monkeypatch)
    res = CliRunner().invoke(main, ["install-watcher"])
    assert res.exit_code == 0
    assert "deprecated" in res.output.lower()
    assert len([c for c in calls if c.startswith("_install_")]) == 1
