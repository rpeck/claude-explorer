"""Detect whether `claude-explorer mcp` is registered in MCP client config
files (Claude Code: ~/.claude.json user scope + ./.mcp.json project scope;
Claude Desktop: claude_desktop_config.json).

Config-file detection only. Claude Desktop `.mcpb`/DXT bundle installs are
NOT detectable from disk (tracked in the app's binary LevelDB/IndexedDB
store), so callers treat a Desktop "not found" as WARN-with-caveat, not a
hard failure. This module is the read side that a future `install-mcp`
command reuses to stay idempotent. Stdlib only — keep it out of any heavy
import path.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class McpRegistration:
    found: bool
    config_path: Path | None
    scope: str | None
    server_name: str | None


def claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _entry_matches(command: str, args: list) -> bool:
    """True iff (command, args) resolves to `claude-explorer ... mcp`.

    Handles `uvx claude-explorer mcp`, `uv run --directory X claude-explorer
    mcp`, and an absolute path to a `claude-explorer` binary with `mcp`.
    """
    tokens = [Path(str(command)).name] + [str(a) for a in (args if isinstance(args, list) else [])]
    if "claude-explorer" not in tokens:
        return False
    idx = tokens.index("claude-explorer")
    return "mcp" in tokens[idx + 1:]


def _scan(path: Path, scope: str) -> McpRegistration | None:
    """Return a found McpRegistration if `path` registers our server, else
    None. Missing/corrupt files yield None (never raise)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return None
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        if _entry_matches(entry.get("command", ""), entry.get("args", [])):
            return McpRegistration(True, path, scope, name)
    return None


def detect_mcp_in_claude_code(
    user_config: Path | None = None,
    project_config: Path | None = None,
) -> McpRegistration:
    user = user_config or (Path.home() / ".claude.json")
    project = project_config or (Path.cwd() / ".mcp.json")
    for path, scope in ((user, "user"), (project, "project")):
        hit = _scan(path, scope)
        if hit is not None:
            return hit
    return McpRegistration(False, None, None, None)


def detect_mcp_in_claude_desktop(config_path: Path | None = None) -> McpRegistration:
    path = config_path or claude_desktop_config_path()
    hit = _scan(path, "desktop")
    if hit is not None:
        return hit
    # Report the path we looked at so the caller can name it in the fix hint.
    return McpRegistration(False, path, None, None)
