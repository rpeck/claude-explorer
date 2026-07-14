from __future__ import annotations

import json
from pathlib import Path

from backend.mcp_config_detect import detect_mcp_in_file


def _write(p: Path, servers: dict) -> Path:
    p.write_text(json.dumps({"mcpServers": servers}))
    return p


def test_found_in_file(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "x.json", {
        "claude-sessions": {"command": "uvx", "args": ["claude-explorer", "mcp"]},
    })
    reg = detect_mcp_in_file(cfg, "user")
    assert reg.found is True
    assert reg.scope == "user"
    assert reg.server_name == "claude-sessions"


def test_not_found_returns_path_no_raise(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "x.json", {"other": {"command": "uvx", "args": ["x"]}})
    reg = detect_mcp_in_file(cfg, "desktop")
    assert reg.found is False
    assert reg.config_path == cfg


def test_absent_file_no_raise(tmp_path: Path) -> None:
    reg = detect_mcp_in_file(tmp_path / "absent.json", "user")
    assert reg.found is False
