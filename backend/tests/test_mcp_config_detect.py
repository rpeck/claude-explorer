from __future__ import annotations

import json
from pathlib import Path

from backend.mcp_config_detect import (
    detect_mcp_in_claude_code,
    detect_mcp_in_claude_desktop,
)


def _write(p: Path, servers: dict) -> Path:
    p.write_text(json.dumps({"mcpServers": servers}))
    return p


def test_code_user_scope_uvx_form(tmp_path: Path) -> None:
    user = _write(tmp_path / ".claude.json", {
        "claude-sessions": {"command": "uvx", "args": ["claude-explorer", "mcp"]},
    })
    reg = detect_mcp_in_claude_code(user_config=user, project_config=tmp_path / "absent.json")
    assert reg.found is True
    assert reg.scope == "user"
    assert reg.server_name == "claude-sessions"


def test_code_project_scope_uv_run_form(tmp_path: Path) -> None:
    proj = _write(tmp_path / ".mcp.json", {
        "x": {"command": "uv", "args": ["run", "--directory", "/p", "claude-explorer", "mcp"]},
    })
    reg = detect_mcp_in_claude_code(user_config=tmp_path / "absent.json", project_config=proj)
    assert reg.found is True
    assert reg.scope == "project"


def test_code_absolute_binary_form(tmp_path: Path) -> None:
    user = _write(tmp_path / ".claude.json", {
        "x": {"command": "/opt/bin/claude-explorer", "args": ["mcp"]},
    })
    reg = detect_mcp_in_claude_code(user_config=user, project_config=tmp_path / "absent.json")
    assert reg.found is True


def test_code_unrelated_server_not_found(tmp_path: Path) -> None:
    user = _write(tmp_path / ".claude.json", {
        "other": {"command": "uvx", "args": ["some-other-tool"]},
    })
    reg = detect_mcp_in_claude_code(user_config=user, project_config=tmp_path / "absent.json")
    assert reg.found is False


def test_absent_file_is_not_found_no_raise(tmp_path: Path) -> None:
    reg = detect_mcp_in_claude_code(
        user_config=tmp_path / "absent.json", project_config=tmp_path / "absent2.json"
    )
    assert reg.found is False


def test_corrupt_json_is_not_found_no_raise(tmp_path: Path) -> None:
    bad = tmp_path / ".claude.json"
    bad.write_text("{ not json ")
    reg = detect_mcp_in_claude_code(user_config=bad, project_config=tmp_path / "absent.json")
    assert reg.found is False


def test_desktop_found(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "claude_desktop_config.json", {
        "claude-sessions": {"command": "uvx", "args": ["claude-explorer", "mcp"]},
    })
    reg = detect_mcp_in_claude_desktop(config_path=cfg)
    assert reg.found is True
    assert reg.scope == "desktop"


def test_desktop_missing_mcpservers_key(tmp_path: Path) -> None:
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"preferences": {}}))
    reg = detect_mcp_in_claude_desktop(config_path=cfg)
    assert reg.found is False
    assert reg.config_path == cfg


def test_non_list_args_does_not_raise(tmp_path: Path) -> None:
    bad = _write(tmp_path / ".claude.json", {
        "x": {"command": "uvx", "args": 42},  # malformed: args not a list
    })
    reg = detect_mcp_in_claude_code(user_config=bad, project_config=tmp_path / "absent.json")
    assert reg.found is False  # must not raise
