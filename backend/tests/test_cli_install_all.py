"""Test the install all subcommand (Task 6)."""
from __future__ import annotations

from click.testing import CliRunner

import backend.mcp_config_install as mci
import cli.main as cm
from cli.main import main


def _ok(target):
    return mci.InstallResult(target, True, True, f"{target} done")


def test_install_all_runs_watcher_and_mcp(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(cm, "_do_watcher", lambda *a, **k: seen.append("watcher") or _ok("watcher"))
    monkeypatch.setattr(mci, "install_mcp_code", lambda scope="user", **k: seen.append("code") or _ok("code"))
    monkeypatch.setattr(mci, "install_mcp_desktop", lambda **k: seen.append("desktop") or _ok("desktop"))
    res = CliRunner().invoke(main, ["install", "all"])
    assert res.exit_code == 0
    assert seen == ["watcher", "code", "desktop"]


def test_install_all_continues_and_exits_one_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(cm, "_do_watcher", lambda *a, **k: mci.InstallResult("watcher", False, False, "wfail"))
    monkeypatch.setattr(mci, "install_mcp_code", lambda scope="user", **k: _ok("code"))
    monkeypatch.setattr(mci, "install_mcp_desktop", lambda **k: _ok("desktop"))
    res = CliRunner().invoke(main, ["install", "all"])
    assert res.exit_code == 1
    assert "wfail" in res.output
    assert "code done" in res.output      # later targets still ran


def test_install_all_uninstall(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(cm, "_do_watcher", lambda pb, iv, un, **k: seen.append(("watcher", un)) or _ok("watcher"))
    monkeypatch.setattr(mci, "uninstall_mcp_code", lambda scope="user", **k: seen.append("u-code") or _ok("code"))
    monkeypatch.setattr(mci, "uninstall_mcp_desktop", lambda **k: seen.append("u-desktop") or _ok("desktop"))
    res = CliRunner().invoke(main, ["install", "all", "--uninstall"])
    assert res.exit_code == 0
    assert seen == [("watcher", True), "u-code", "u-desktop"]
