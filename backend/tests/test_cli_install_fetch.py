"""Test the install fetch subcommand (Task 7)."""
from __future__ import annotations

from click.testing import CliRunner

import cli.main as cm
import cli.scheduled_fetch_install as sfi
from cli.main import main


def test_install_fetch_runs_installer(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(sfi, "install", lambda python_bin, interval: calls.append(interval))
    res = CliRunner().invoke(main, ["install", "fetch", "--interval", "1800"])
    assert res.exit_code == 0
    assert calls == [1800]


def test_install_fetch_uninstall(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(sfi, "uninstall", lambda: calls.append("u"))
    res = CliRunner().invoke(main, ["install", "fetch", "--uninstall"])
    assert res.exit_code == 0
    assert calls == ["u"]


def test_install_all_includes_fetch(monkeypatch) -> None:
    seen = []
    import backend.mcp_config_install as mci
    monkeypatch.setattr(cm, "_do_watcher", lambda *a, **k: mci.InstallResult("watcher", True, True, "watcher done"))
    monkeypatch.setattr(cm, "_do_scheduled_fetch",
                        lambda interval, uninstall: seen.append("fetch") or mci.InstallResult("fetch", True, True, "fetch done"))
    monkeypatch.setattr(mci, "install_mcp_code", lambda scope="user", **k: mci.InstallResult("code", True, True, "code done"))
    monkeypatch.setattr(mci, "install_mcp_desktop", lambda **k: mci.InstallResult("desktop", True, True, "desktop done"))
    res = CliRunner().invoke(main, ["install", "all"])
    assert res.exit_code == 0
    assert "fetch" in seen
