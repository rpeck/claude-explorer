"""Single-source-of-truth version test.

Per ``PLANS/2026.06.04-mcpb-bundle.md`` §"Commit 1 — single-source-of-truth
version" and §9 (Version contract):

* ``mcp_server.__version__`` is the canonical source.
* ``pyproject.toml`` reads it via ``[tool.hatch.version] path = "mcp_server/__init__.py"``.
* The pip-installed package version (``importlib.metadata.version``) must
  agree with ``mcp_server.__version__`` so the MCP server's
  ``FastMCP(version=...)`` and ``click.version_option`` keep pointing at the
  same string.
* The MCPB build script (added in commit 3) will read this same
  ``__version__`` to stamp the manifest, closing the loop so a single bump
  updates: pip version + MCP serverInfo + MCPB manifest.
"""

from __future__ import annotations

import re
from importlib.metadata import version as _pkg_version

import mcp_server

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")


def test_mcp_server_exposes_version_attribute() -> None:
    """``mcp_server.__version__`` exists and is a non-empty string."""

    assert hasattr(mcp_server, "__version__"), (
        "mcp_server/__init__.py must define __version__ (single source of "
        "truth for pip package + MCP serverInfo + MCPB manifest)"
    )
    assert isinstance(mcp_server.__version__, str)
    assert mcp_server.__version__, "mcp_server.__version__ must not be empty"


def test_mcp_server_version_is_semver() -> None:
    """``mcp_server.__version__`` is a valid SemVer 2.0 string."""

    assert _SEMVER_RE.match(mcp_server.__version__), (
        f"mcp_server.__version__={mcp_server.__version__!r} is not valid SemVer"
    )


def test_mcp_server_version_matches_installed_package_metadata() -> None:
    """``importlib.metadata.version("claude-explorer")`` matches
    ``mcp_server.__version__``.

    Hatch's ``[tool.hatch.version] path = "mcp_server/__init__.py"`` makes
    these the same string at build time. If they ever diverge, the MCPB
    manifest (which reads ``mcp_server.__version__``) will publish a
    different version than the pip wheel — exactly the desync this contract
    exists to prevent.
    """

    assert _pkg_version("claude-explorer") == mcp_server.__version__
