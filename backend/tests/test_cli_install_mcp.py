from __future__ import annotations

from click.testing import CliRunner

import backend.mcp_config_install as mci
from cli.main import main


def _ok(target):
    return mci.InstallResult(target, True, True, f"{target} done")


def test_mcp_code_only(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(mci, "install_mcp_code",
                        lambda scope="user", **k: seen.append(("code", scope)) or _ok("code"))
    monkeypatch.setattr(mci, "install_mcp_desktop", lambda **k: _ok("desktop"))
    res = CliRunner().invoke(main, ["install", "mcp", "--client", "code"])
    assert res.exit_code == 0
    assert seen == [("code", "user")]          # desktop NOT called
    assert "code done" in res.output


def test_mcp_all_runs_both(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(mci, "install_mcp_code", lambda scope="user", **k: seen.append("code") or _ok("code"))
    monkeypatch.setattr(mci, "install_mcp_desktop", lambda **k: seen.append("desktop") or _ok("desktop"))
    res = CliRunner().invoke(main, ["install", "mcp", "--client", "all"])
    assert res.exit_code == 0
    assert seen == ["code", "desktop"]


def test_mcp_scope_project_passed_through(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(mci, "install_mcp_code", lambda scope="user", **k: seen.append(scope) or _ok("code"))
    monkeypatch.setattr(mci, "install_mcp_desktop", lambda **k: _ok("desktop"))
    CliRunner().invoke(main, ["install", "mcp", "--client", "code", "--scope", "project"])
    assert seen == ["project"]


def test_mcp_partial_failure_exit_one(monkeypatch) -> None:
    monkeypatch.setattr(mci, "install_mcp_code", lambda scope="user", **k: _ok("code"))
    monkeypatch.setattr(mci, "install_mcp_desktop",
                        lambda **k: mci.InstallResult("desktop", False, False, "nope"))
    res = CliRunner().invoke(main, ["install", "mcp", "--client", "all"])
    assert res.exit_code == 1
    assert "nope" in res.output


def test_mcp_uninstall_routes_to_uninstall_fns(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(mci, "uninstall_mcp_code", lambda scope="user", **k: seen.append("u-code") or _ok("code"))
    monkeypatch.setattr(mci, "uninstall_mcp_desktop", lambda **k: seen.append("u-desktop") or _ok("desktop"))
    res = CliRunner().invoke(main, ["install", "mcp", "--client", "all", "--uninstall"])
    assert res.exit_code == 0
    assert seen == ["u-code", "u-desktop"]
