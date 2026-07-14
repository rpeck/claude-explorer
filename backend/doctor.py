"""Read-only environment/install diagnostics for `claude-explorer doctor`.

Each check is a zero-arg callable returning a :class:`CheckResult`. The
registry pairs a display name with the callable so the runner can label a
result even if the check raises. Checks MUST NOT mutate state — fixing
lives in dedicated commands (install-watcher, reindex-search, mcp).
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from .config import get_settings
from .mcp_config_detect import detect_mcp_in_claude_code, detect_mcp_in_claude_desktop
from .search_index import get_search_index
from .watcher_status import is_watcher_installed


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str
    fix_command: str | None = None
    fix: Callable[[], None] | None = None


Check = Callable[[], "CheckResult"]


def run_checks(checks: list[tuple[str, Check]]) -> list[CheckResult]:
    """Run every check, wrapping unexpected exceptions as FAIL results.

    One check failing never aborts the others.
    """
    out: list[CheckResult] = []
    for name, fn in checks:
        try:
            out.append(fn())
        except Exception as exc:  # noqa: BLE001 - doctor must never crash
            out.append(
                CheckResult(
                    name=name,
                    status=Status.FAIL,
                    detail=f"unexpected error: {type(exc).__name__}: {exc}",
                )
            )
    return out


def has_failure(results: list[CheckResult]) -> bool:
    return any(r.status is Status.FAIL for r in results)


def credentials_path() -> Path:
    """Return the path to the credentials file."""
    return Path.home() / ".claude-explorer" / "credentials.json"


def check_credentials() -> CheckResult:
    """Check if credentials file exists."""
    p = credentials_path()
    if p.is_file():
        return CheckResult("Credentials", Status.OK, f"found ({p})")
    return CheckResult(
        "Credentials", Status.WARN,
        "not found (needed for fetch, not for browsing existing data)",
        fix_command="claude-explorer capture",
    )


def check_data_dir() -> CheckResult:
    """Check if data directory exists and is writable."""
    data_dir = get_settings().data_dir
    if not data_dir.exists():
        return CheckResult(
            "Data directory", Status.FAIL, f"missing: {data_dir}",
            fix_command=f"mkdir -p {data_dir}  (or set CLAUDE_EXPLORER_DATA_DIR)",
        )
    if not os.access(data_dir, os.W_OK):
        return CheckResult(
            "Data directory", Status.FAIL, f"not writable: {data_dir}",
            fix_command=f"chmod u+w {data_dir}",
        )
    count = sum(1 for _ in data_dir.glob("*.json"))
    return CheckResult("Data directory", Status.OK, f"{data_dir} ({count} conversation(s))")


def check_config() -> CheckResult:
    """Check if config is valid (not corrupt)."""
    reason = get_settings().config_corrupt_reason
    if reason:
        return CheckResult(
            "Config", Status.FAIL, f"corrupt: {reason}",
            fix_command="fix or remove the named config file",
        )
    return CheckResult("Config", Status.OK, "valid")


def watcher_install_command() -> str:
    """Return platform-correct install command hint."""
    base = "claude-explorer install-watcher"
    if sys.platform.startswith("linux"):
        return base + "  (then: sudo loginctl enable-linger $USER)"
    return base


def check_watcher() -> CheckResult:
    """Check if CC watcher is installed."""
    if is_watcher_installed():
        return CheckResult("CC watcher", Status.OK, "installed")
    return CheckResult(
        "CC watcher", Status.WARN,
        "not installed (image-cache data loss risk during downtime)",
        fix_command=watcher_install_command(),
    )


def check_search() -> CheckResult:
    """Check the on-disk FTS5 index health.

    Uses ``is_built_on_disk()`` (schema intact + populated), NOT
    ``is_ready()`` — the latter is a process-local build flag that is always
    False in a cold one-shot CLI, so it would spuriously warn even when the
    on-disk index is healthy. The running server keeps the index current via
    the watcher's drift pass; this check only flags a genuinely absent/empty
    index."""
    idx = get_search_index()
    if idx is None:
        return CheckResult(
            "Search (FTS5)", Status.WARN,
            "FTS5 unavailable; search uses linear scan (still works)",
        )
    if not idx.is_built_on_disk():
        return CheckResult(
            "Search (FTS5)", Status.WARN,
            "index not built yet (empty or schema mismatch); linear-scan fallback active",
            fix_command=(
                "start `claude-explorer serve` once (auto-builds), "
                "or run `claude-explorer reindex-search`"
            ),
        )
    return CheckResult(
        "Search (FTS5)", Status.OK,
        f"index present ({idx.indexed_file_count()} file(s) indexed)",
    )


def check_uvx() -> CheckResult:
    """Check if uvx or uv is on PATH."""
    uvx = shutil.which("uvx")
    uv = shutil.which("uv")
    if uvx or uv:
        found = uvx or uv
        return CheckResult("Runtime (uv/uvx)", Status.OK, f"found ({found})")
    return CheckResult(
        "Runtime (uv/uvx)", Status.WARN,
        "uv/uvx not on PATH (needed only for the uvx-based MCP config)",
        fix_command="install uv (https://docs.astral.sh/uv/) or add it to PATH",
    )


def _weasyprint_importable() -> tuple[bool, str]:
    """Check if weasyprint can be imported (PDF export support)."""
    try:
        import weasyprint  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - OSError when pango missing, etc.
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def pdf_install_hint() -> str:
    """Return OS-specific hint for installing PDF export dependencies."""
    if sys.platform == "darwin":
        return "brew install pango cairo libffi"
    if sys.platform.startswith("linux"):
        return "apt-get install libpango-1.0-0 libpangocairo-1.0-0 libcairo2"
    return "MSYS2: pacman -S mingw-w64-x86_64-pango (or the standalone WeasyPrint .exe)"


def check_pdf_libs() -> CheckResult:
    """Check if PDF export dependencies are available."""
    ok, err = _weasyprint_importable()
    if ok:
        return CheckResult("PDF export", Status.OK, "weasyprint importable")
    return CheckResult(
        "PDF export", Status.WARN,
        f"unavailable ({err}); PDF export disabled, rest of app fine",
        fix_command=pdf_install_hint(),
    )


MCPB_CAVEAT = (
    "if you installed the .mcpb bundle via the Extensions UI, this is "
    "expected — bundle installs aren't detectable from disk"
)


def check_mcp_code() -> CheckResult:
    """Check if claude-explorer mcp is registered in Claude Code."""
    reg = detect_mcp_in_claude_code()
    if reg.found:
        return CheckResult(
            "MCP -> Claude Code", Status.OK,
            f"registered ({reg.scope} scope: {reg.server_name})",
        )
    return CheckResult(
        "MCP -> Claude Code", Status.WARN, "not registered",
        fix_command="claude-explorer install mcp --client code",
    )


def check_mcp_desktop() -> CheckResult:
    """Check if claude-explorer mcp is registered in Claude Desktop.

    Returns WARN (not FAIL) on not-found, since .mcpb bundle installs
    are not detectable from disk config files.
    """
    reg = detect_mcp_in_claude_desktop()
    if reg.found:
        return CheckResult(
            "MCP -> Claude Desktop", Status.OK,
            f"registered ({reg.server_name})",
        )
    where = reg.config_path or "claude_desktop_config.json"
    return CheckResult(
        "MCP -> Claude Desktop", Status.WARN,
        f"no entry in {where}; {MCPB_CAVEAT}",
        fix_command=(
            "claude-explorer install mcp --client desktop, then restart Claude Desktop"
        ),
    )


ALL_CHECKS: list[tuple[str, Check]] = [
    ("Credentials", check_credentials),
    ("Data directory", check_data_dir),
    ("Config", check_config),
    ("CC watcher", check_watcher),
    ("Search (FTS5)", check_search),
    ("Runtime (uv/uvx)", check_uvx),
    ("PDF export", check_pdf_libs),
    ("MCP -> Claude Code", check_mcp_code),
    ("MCP -> Claude Desktop", check_mcp_desktop),
]

_SYMBOL = {Status.OK: "[ok]", Status.WARN: "[warn]", Status.FAIL: "[FAIL]"}


def render_text(results: list[CheckResult]) -> str:
    width = max((len(r.name) for r in results), default=0)
    lines: list[str] = []
    for r in results:
        lines.append(f"  {r.name.ljust(width)}  {_SYMBOL[r.status]} {r.detail}")
        if r.status is not Status.OK and r.fix_command:
            lines.append(f"  {' ' * width}  -> {r.fix_command}")
    failed = sum(1 for r in results if r.status is Status.FAIL)
    warned = sum(1 for r in results if r.status is Status.WARN)
    if failed:
        lines.append(f"\n{failed} problem(s) found, {warned} warning(s).")
    elif warned:
        lines.append(f"\nNo failures. {warned} warning(s) — see fixes above.")
    else:
        lines.append("\nAll checks passed.")
    return "\n".join(lines)


def to_json(results: list[CheckResult]) -> dict:
    return {
        "checks": [
            {
                "name": r.name,
                "status": r.status.value,
                "detail": r.detail,
                "fix_command": r.fix_command,
            }
            for r in results
        ],
        "summary": {
            "ok": sum(1 for r in results if r.status is Status.OK),
            "warnings": sum(1 for r in results if r.status is Status.WARN),
            "failed": sum(1 for r in results if r.status is Status.FAIL),
        },
    }
