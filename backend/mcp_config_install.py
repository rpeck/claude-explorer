"""Write side for registering the `claude-explorer mcp` server in MCP
client configs (Claude Code: ~/.claude.json / ./.mcp.json; Claude
Desktop: claude_desktop_config.json).

Pairs with backend.mcp_config_detect (read side). Stdlib-only and
CLI-only — must stay OUT of the MCPB import closure. Writes are atomic
(temp + os.replace) and preserve every other top-level key; the public
install_*/uninstall_* functions never raise (a corrupt/unwritable
target yields a failed InstallResult, never a clobbered file).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .mcp_config_detect import claude_desktop_config_path, detect_mcp_in_file


SERVER_NAME = "claude-sessions"


def _installed_claude_explorer() -> str | None:
    """Absolute path to an installed ``claude-explorer`` entry point, or None.

    Module-level so tests can monkeypatch it."""
    return shutil.which("claude-explorer")


def mcp_command() -> tuple[str, list[str]]:
    """The (command, args) that launches the MCP server.

    Prefer the installed ``claude-explorer`` entry point by ABSOLUTE path:
    it's robust for GUI apps (Claude Desktop's PATH often omits ``uvx``, and
    an absolute command needs no PATH at all), and it runs THIS install
    rather than the published PyPI package. Fall back to ``uvx
    claude-explorer mcp`` only when it isn't installed (the zero-install
    path)."""
    exe = _installed_claude_explorer()
    if exe:
        return exe, ["mcp"]
    return "uvx", ["claude-explorer", "mcp"]


def mcp_block() -> dict:
    """The mcpServers entry value we write. Single source of truth."""
    cmd, args = mcp_command()
    return {"type": "stdio", "command": cmd, "args": args}


@dataclass
class InstallResult:
    target: str       # "code" | "desktop" | "watcher"
    ok: bool
    changed: bool
    detail: str


def _load_config(path: Path) -> dict:
    """Return the parsed config dict, or {} if the file is absent.

    Raises ValueError on corrupt JSON or a non-dict root — callers must
    NOT clobber a config they cannot parse.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level JSON is not an object")
    return data


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write data to path atomically (temp in same dir + os.replace).

    Creates parent dirs as needed. Sets 0o600 on the temp file before
    replace so a newly-created config isn't world-readable. Cleans up
    the temp file if the replace fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fixed temp suffix assumes a single writer (human-invoked CLI); two
    # concurrent installs to the same config would race on the temp file.
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            tmp.unlink()
        raise


def _merge_entry(path: Path, name: str, entry: dict) -> bool:
    """Ensure mcpServers[name] == entry in the config at path.

    Returns True if the file was changed, False if it already matched.
    Preserves all other top-level keys and other mcpServers entries.
    """
    data = _load_config(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    if servers.get(name) == entry:
        return False
    servers[name] = entry
    data["mcpServers"] = servers
    _atomic_write_json(path, data)
    return True


def _remove_entry(path: Path, name: str) -> bool:
    """Remove mcpServers[name] from the config at path.

    Returns True if an entry was removed, False if it wasn't present.
    """
    data = _load_config(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    data["mcpServers"] = servers
    _atomic_write_json(path, data)
    return True


def _code_config_path(scope: str) -> Path:
    """Return the config file path for Claude Code (user or project scope)."""
    if scope == "project":
        return Path.cwd() / ".mcp.json"
    return Path.home() / ".claude.json"


def _claude_available() -> bool:
    """Check if the claude CLI is available in the PATH."""
    return shutil.which("claude") is not None


def _run_claude(args: list[str]) -> tuple[int, str]:
    """Run `claude <args>`; return (returncode, combined stdout+stderr)."""
    proc = subprocess.run(
        ["claude", *args], capture_output=True, text=True, check=False
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def install_mcp_code(scope: str = "user", *, config_path: Path | None = None) -> InstallResult:
    """Install the MCP server in Claude Code config (user or project scope).

    Prefers the claude CLI if available; falls back to direct file write.
    Returns ok=False (not raised) on OSError or corrupt JSON.
    """
    path = config_path or _code_config_path(scope)
    try:
        reg = detect_mcp_in_file(path, scope)
        if reg.found:
            return InstallResult("code", True, False,
                                 f"already configured ({reg.server_name})")
        if _claude_available():
            cmd, args = mcp_command()
            rc, out = _run_claude(["mcp", "add", "--scope", scope, SERVER_NAME,
                                   "--", cmd, *args])
            if rc == 0:
                return InstallResult("code", True, True,
                                     f"registered via claude CLI ({scope} scope)")
            return InstallResult("code", False, False,
                                 f"claude mcp add failed: {out.strip()}")
        changed = _merge_entry(path, SERVER_NAME, mcp_block())
        return InstallResult("code", True, changed, f"wrote {path}")
    except (OSError, ValueError) as exc:
        return InstallResult("code", False, False, f"failed: {exc}")


def install_mcp_desktop(*, config_path: Path | None = None) -> InstallResult:
    """Install the MCP server in Claude Desktop config.

    Returns ok=False (not raised) on OSError or corrupt JSON.
    """
    path = config_path or claude_desktop_config_path()
    try:
        reg = detect_mcp_in_file(path, "desktop")
        if reg.found:
            return InstallResult("desktop", True, False,
                                 f"already configured ({reg.server_name})")
        changed = _merge_entry(path, SERVER_NAME, mcp_block())
        return InstallResult("desktop", True, changed,
                             f"wrote {path}; restart Claude Desktop to load it")
    except (OSError, ValueError) as exc:
        return InstallResult("desktop", False, False, f"failed: {exc}")


def uninstall_mcp_code(scope: str = "user", *, config_path: Path | None = None) -> InstallResult:
    """Uninstall the MCP server from Claude Code config (user or project scope).

    Always uses direct file edit (not the claude CLI) for robustness.
    Returns ok=False (not raised) on OSError or corrupt JSON.
    """
    path = config_path or _code_config_path(scope)
    try:
        changed = _remove_entry(path, SERVER_NAME)
        return InstallResult("code", True, changed,
                             "removed" if changed else "not present")
    except (OSError, ValueError) as exc:
        return InstallResult("code", False, False, f"failed: {exc}")


def uninstall_mcp_desktop(*, config_path: Path | None = None) -> InstallResult:
    """Uninstall the MCP server from Claude Desktop config.

    Returns ok=False (not raised) on OSError or corrupt JSON.
    """
    path = config_path or claude_desktop_config_path()
    try:
        changed = _remove_entry(path, SERVER_NAME)
        return InstallResult("desktop", True, changed,
                             "removed; restart Claude Desktop" if changed else "not present")
    except (OSError, ValueError) as exc:
        return InstallResult("desktop", False, False, f"failed: {exc}")
