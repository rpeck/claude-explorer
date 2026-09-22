"""Regression test: FastMCP must advertise OUR package version, not its own.

Pre-fix shipping bug (found 2026-06-04 while smoke-testing the published
1.0.4 wheel from PyPI): ``mcp_server/server.py`` constructed
``FastMCP("Claude Session Explorer", instructions=...)`` with no
``version=`` argument, so the MCP-protocol ``serverInfo.version`` field
reported the FastMCP library's own version (e.g. ``3.2.4``) to every
connecting client. Clients had no portable way to ask "which version of
claude-explorer am I talking to?" — exactly what serverInfo is meant to
answer.

The fix passes the source-of-truth ``mcp_server.__version__`` into
``FastMCP(version=...)`` so it appears verbatim in ``serverInfo.version``
on the MCP handshake.

Second shipping bug (found 2026-06-09 while smoke-testing the v1.0.6 MCPB
bundle in Claude Desktop): the original v1.0.5 fix read the version via
``importlib.metadata.version("claude-explorer")``. That works for the pip
wheel, but the MCPB bundle vendors ``mcp_server/`` and ``backend/`` as
bare directories — there is no installed ``claude-explorer`` package in
the UV-managed runtime, so the metadata lookup raised
``PackageNotFoundError`` and the bundled server failed at construction
time. The corrected fix reads ``mcp_server.__version__`` directly (the
single source of truth set in MCPB plan commit 1 / ``eed8cf1f``), which
works in all three contexts: installed wheel, dev-from-source, MCPB
bundle.
"""

from __future__ import annotations

import importlib
import sys
from importlib.metadata import PackageNotFoundError, version

import mcp_server
from mcp_server.server import mcp


def test_server_version_matches_source_of_truth() -> None:
    """FastMCP serverInfo.version is ``mcp_server.__version__``, the
    single source of truth set in ``mcp_server/__init__.py``."""

    assert mcp.version == mcp_server.__version__, (
        f"FastMCP serverInfo.version must report the source-of-truth "
        f"mcp_server.__version__ ({mcp_server.__version__!r}), not "
        f"{mcp.version!r}."
    )


def test_server_version_matches_installed_package() -> None:
    """In the installed-wheel case, ``mcp_server.__version__`` and
    ``importlib.metadata.version("claude-explorer")`` must agree —
    Hatch's dynamic version path makes them the same string at build
    time. This test pins that contract."""

    expected = version("claude-explorer")
    assert mcp.version == expected, (
        f"FastMCP serverInfo.version must report the claude-explorer "
        f"package version ({expected!r}), not {mcp.version!r}."
    )


def test_server_version_is_not_the_fastmcp_library_version() -> None:
    # Regression guard for the pre-fix bug: if someone removes the
    # version= kwarg from the FastMCP(...) constructor, the attribute
    # falls back to the fastmcp library's own version. That's exactly
    # the wrong thing to advertise to MCP clients.
    fastmcp_lib_version = version("fastmcp")
    pkg_version = version("claude-explorer")
    # Only meaningful when the two diverge (they almost always do).
    if fastmcp_lib_version != pkg_version:
        assert mcp.version != fastmcp_lib_version, (
            f"FastMCP serverInfo.version is reporting the fastmcp library "
            f"version ({fastmcp_lib_version!r}) instead of the "
            f"claude-explorer package version ({pkg_version!r}). The "
            f"FastMCP(...) constructor is missing the version= kwarg."
        )


def test_server_version_does_not_require_installed_package_metadata(
    monkeypatch,
) -> None:
    """Bundle-runtime regression guard. In the MCPB bundle context the
    ``claude-explorer`` package is NOT installed (the code is vendored
    as bare directories under ``server/``), so any
    ``importlib.metadata.version("claude-explorer")`` call raises
    ``PackageNotFoundError`` and breaks server construction.

    The server module must be importable and constructable in that
    context. This test simulates the bundle environment by making
    ``importlib.metadata.version("claude-explorer")`` raise, then
    re-importing the module, and asserts construction succeeds with
    the correct version pulled from ``mcp_server.__version__``.
    """

    import importlib.metadata as md

    real_version = md.version

    def fake_version(name):
        if name == "claude-explorer":
            raise PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(md, "version", fake_version)

    # Drop cached module so module-level code re-runs under the patch.
    original = sys.modules.get("mcp_server.server")
    sys.modules.pop("mcp_server.server", None)
    try:
        reloaded = importlib.import_module("mcp_server.server")
    finally:
        # Put the ORIGINAL module object back. Popping without restoring
        # left sys.modules empty for this name, so a later import built a
        # fresh module while callers that had already imported it still held
        # the old one. The autouse singleton reset in conftest then cleared
        # the new object while the tests ran against the old, stale one, and
        # export_session looked up a session in a previous test's data dir.
        if original is not None:
            sys.modules["mcp_server.server"] = original
        else:
            sys.modules.pop("mcp_server.server", None)

    assert reloaded.mcp.version == mcp_server.__version__, (
        "In the bundle context (no installed claude-explorer wheel), "
        "the server must read its version from mcp_server.__version__ "
        "and construct successfully."
    )


def test_server_name_is_stable() -> None:
    # Belt-and-suspenders: the human-facing name is part of serverInfo too
    # and should not silently change while we're fixing version reporting.
    assert mcp.name == "Claude Session Explorer"
