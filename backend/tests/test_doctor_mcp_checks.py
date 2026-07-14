from __future__ import annotations

from pathlib import Path

import backend.doctor as doctor
from backend.doctor import Status
from backend.mcp_config_detect import McpRegistration


def test_mcp_code_found_is_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        doctor, "detect_mcp_in_claude_code",
        lambda: McpRegistration(True, Path("/h/.claude.json"), "user", "claude-sessions"),
    )
    r = doctor.check_mcp_code()
    assert r.status is Status.OK
    assert "user" in r.detail


def test_mcp_code_missing_is_warn_with_install_command(monkeypatch) -> None:
    monkeypatch.setattr(
        doctor, "detect_mcp_in_claude_code",
        lambda: McpRegistration(False, None, None, None),
    )
    r = doctor.check_mcp_code()
    assert r.status is Status.WARN
    assert "install mcp" in (r.fix_command or "")


def test_mcp_desktop_found_is_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        doctor, "detect_mcp_in_claude_desktop",
        lambda: McpRegistration(True, Path("/h/claude_desktop_config.json"), "desktop", "x"),
    )
    assert doctor.check_mcp_desktop().status is Status.OK


def test_mcp_desktop_missing_is_warn_not_fail_with_mcpb_caveat(monkeypatch) -> None:
    monkeypatch.setattr(
        doctor, "detect_mcp_in_claude_desktop",
        lambda: McpRegistration(False, Path("/h/claude_desktop_config.json"), None, None),
    )
    r = doctor.check_mcp_desktop()
    assert r.status is Status.WARN          # never FAIL — protects .mcpb users
    assert ".mcpb" in r.detail or ".mcpb" in (r.fix_command or "")
