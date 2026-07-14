from __future__ import annotations

import json
from pathlib import Path

from backend import mcp_config_install as mci


def test_install_code_direct_write_when_no_claude(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mci, "_claude_available", lambda: False)
    cfg = tmp_path / ".claude.json"
    r = mci.install_mcp_code("user", config_path=cfg)
    assert r.ok and r.changed and r.target == "code"
    assert json.loads(cfg.read_text())["mcpServers"][mci.SERVER_NAME] == mci.mcp_block()


def test_install_code_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mci, "_claude_available", lambda: False)
    cfg = tmp_path / ".claude.json"
    mci.install_mcp_code("user", config_path=cfg)
    r = mci.install_mcp_code("user", config_path=cfg)
    assert r.ok and r.changed is False
    assert "already configured" in r.detail.lower()


def test_install_code_uses_claude_cli_uvx_form_when_not_installed(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(mci, "_claude_available", lambda: True)
    monkeypatch.setattr(mci, "_installed_claude_explorer", lambda: None)  # force uvx form
    monkeypatch.setattr(mci, "_run_claude", lambda args: (calls.append(args) or (0, "added")))
    cfg = tmp_path / ".claude.json"  # absent → detect not-found → CLI path taken
    r = mci.install_mcp_code("user", config_path=cfg)
    assert r.ok and r.changed
    assert calls == [["mcp", "add", "--scope", "user", mci.SERVER_NAME, "--",
                      "uvx", "claude-explorer", "mcp"]]


def test_install_code_uses_claude_cli_prefers_installed_entrypoint(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(mci, "_claude_available", lambda: True)
    monkeypatch.setattr(mci, "_installed_claude_explorer", lambda: "/opt/bin/claude-explorer")
    monkeypatch.setattr(mci, "_run_claude", lambda args: (calls.append(args) or (0, "added")))
    r = mci.install_mcp_code("user", config_path=tmp_path / ".claude.json")
    assert r.ok and r.changed
    assert calls == [["mcp", "add", "--scope", "user", mci.SERVER_NAME, "--",
                      "/opt/bin/claude-explorer", "mcp"]]


def test_install_code_claude_cli_failure_is_failed_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mci, "_claude_available", lambda: True)
    monkeypatch.setattr(mci, "_run_claude", lambda args: (1, "boom"))
    r = mci.install_mcp_code("user", config_path=tmp_path / ".claude.json")
    assert r.ok is False and "boom" in r.detail


def test_install_desktop_writes_and_mentions_restart(tmp_path) -> None:
    cfg = tmp_path / "claude_desktop_config.json"
    r = mci.install_mcp_desktop(config_path=cfg)
    assert r.ok and r.changed and r.target == "desktop"
    assert "restart" in r.detail.lower()


def test_install_corrupt_target_is_failed_not_raised(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mci, "_claude_available", lambda: False)
    cfg = tmp_path / ".claude.json"
    cfg.write_text("{ not json ")
    r = mci.install_mcp_code("user", config_path=cfg)
    assert r.ok is False
    assert cfg.read_text() == "{ not json "  # untouched, not clobbered


def test_uninstall_code_direct_edit(tmp_path) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"mcpServers": {mci.SERVER_NAME: mci.mcp_block()}}))
    r = mci.uninstall_mcp_code("user", config_path=cfg)
    assert r.ok and r.changed
    assert mci.SERVER_NAME not in json.loads(cfg.read_text())["mcpServers"]


def test_uninstall_absent_is_ok_noop(tmp_path) -> None:
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {}}))
    r = mci.uninstall_mcp_desktop(config_path=cfg)
    assert r.ok and r.changed is False


def test_mcp_command_prefers_installed_entrypoint(monkeypatch) -> None:
    monkeypatch.setattr(mci, "_installed_claude_explorer", lambda: "/opt/bin/claude-explorer")
    assert mci.mcp_command() == ("/opt/bin/claude-explorer", ["mcp"])
    assert mci.mcp_block() == {
        "type": "stdio", "command": "/opt/bin/claude-explorer", "args": ["mcp"],
    }


def test_mcp_command_falls_back_to_uvx_when_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(mci, "_installed_claude_explorer", lambda: None)
    assert mci.mcp_command() == ("uvx", ["claude-explorer", "mcp"])
    assert mci.mcp_block()["command"] == "uvx"
