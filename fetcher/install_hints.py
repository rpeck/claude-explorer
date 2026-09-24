"""Install-command hints that must work when dependencies are missing.

This module imports nothing outside the standard library, on purpose.
The hints it returns are shown on failure paths such as "playwright is
not installed", so importing it must never trigger the same failure it
explains.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def playwright_install_hint(package_root: Path | None = None) -> str:
    """Return the command that downloads Playwright's Chromium build.

    The correct command depends on how the user installed claude-explorer.
    ``uv run`` works only inside a source checkout, so a PyPI install
    (``uvx`` or ``uv tool install``) must go through ``uvx --from``.

    A source checkout keeps ``pyproject.toml`` at the repo root. A wheel
    install does not, so that file is the marker.

    Args:
        package_root: Directory to test for the marker. Defaults to the
            parent of this package, which is the repo root in a checkout.
    """
    if package_root is None:
        package_root = Path(__file__).resolve().parent.parent
    if (package_root / "pyproject.toml").is_file():
        return "uv run playwright install chromium"
    return "uvx --from claude-explorer playwright install chromium"


# uvx builds each run's environment inside uv's cache, under a directory
# named archive-v<N>. Match either path separator, so a Windows path is
# recognized on any host.
_UV_CACHE_ENV = re.compile(r"[\\/]archive-v\d+[\\/]")


def is_ephemeral_interpreter(executable: str | None = None) -> bool:
    """Return True if the interpreter is a temporary ``uvx`` environment.

    uv may delete that environment at any time (``uv cache clean``, a cache
    prune, or a newer ``uvx`` run). A background job registered against it
    then fails at the next login, without a visible error.

    Args:
        executable: The interpreter path. Defaults to ``sys.executable``.
    """
    if executable is None:
        executable = sys.executable
    return bool(_UV_CACHE_ENV.search(executable))


def watcher_install_hint(
    package_root: Path | None = None,
    *,
    executable: str | None = None,
    platform_name: str | None = None,
) -> str:
    """Return the command that installs the supervised watcher.

    * A source checkout runs the command through ``uv run``.
    * A ``uv tool install`` puts ``claude-explorer`` on PATH.
    * A ``uvx`` run has no lasting interpreter. ``install-watcher`` refuses
      it, so the hint installs the tool first.

    Args:
        package_root: Directory to test for the checkout marker. Defaults to
            the parent of this package.
        executable: The interpreter path. Defaults to ``sys.executable``.
        platform_name: A ``sys.platform`` value. Defaults to the running one.
    """
    if package_root is None:
        package_root = Path(__file__).resolve().parent.parent
    if (package_root / "pyproject.toml").is_file():
        return "uv run claude-explorer install-watcher"
    if not is_ephemeral_interpreter(executable):
        return "claude-explorer install-watcher"
    if platform_name is None:
        platform_name = sys.platform
    # Windows PowerShell 5.1 rejects `&&`. A `;` works in every PowerShell.
    sep = "; " if platform_name == "win32" else " && "
    return f"uv tool install claude-explorer{sep}claude-explorer install-watcher"


def claude_desktop_launch_command(port: int, platform_name: str | None = None) -> str:
    """Return the command that launches Claude Desktop through the proxy.

    The command differs per operating system. Printing the macOS form to a
    Windows user sends that user to a command that does not exist.

    On Windows the path shown is the typical installer location. A Microsoft
    Store install lives under ``C:\\Program Files\\WindowsApps\\`` instead, and
    may ignore these flags. Callers warn about that case separately.

    Args:
        port: The local proxy port.
        platform_name: A ``sys.platform`` value. Defaults to the running one.
    """
    if platform_name is None:
        platform_name = sys.platform
    flags = f'--proxy-server="127.0.0.1:{port}" --ignore-certificate-errors'
    if platform_name == "darwin":
        return f'open -a "Claude" --args {flags}'
    if platform_name == "win32":
        return f'& "$env:LOCALAPPDATA\\AnthropicClaude\\Claude.exe" {flags}'
    return f"claude {flags}"
