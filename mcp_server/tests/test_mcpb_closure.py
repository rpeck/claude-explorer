"""Import-closure canary for the MCPB bundle.

Per ``PLANS/2026.06.04-mcpb-bundle.md`` §"Commit 2 — import-closure
analyzer + test":

The MCPB bundle ships a deliberately narrow slice of the codebase — the
MCP server and only the ``backend.*`` modules it actually imports. If a
future PR accidentally pulls FastAPI, weasyprint, mitmproxy, etc. into the
MCP code path, the bundle would balloon (and may even be unbuildable —
weasyprint requires system libs we explicitly exclude). This test makes
that drift a build-time failure instead of a "user installed a 400 MB
extension and it crashed" failure.

The canary fires on:

* External top-level packages that must NOT be in the closure:
  ``fastapi``, ``uvicorn``, ``playwright``, ``mitmproxy``, ``curl_cffi``,
  ``weasyprint``, ``watchdog``.
* External top-level packages that MUST be in the closure (proxy for "the
  analyzer is actually walking the graph and didn't silently return empty"):
  ``fastmcp``, ``pydantic``.
* Internal modules that must NOT be in the closure: ``backend.main``,
  ``backend.routers.*``, ``backend.cc_watcher``, ``backend.deps``.

If any of these assertions fail, do NOT loosen the canary — go fix the
import that pulled the dep in.
"""

from __future__ import annotations

import pathlib
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


@pytest.fixture(scope="module")
def closure() -> tuple[set[str], set[str]]:
    """Run the analyzer against ``mcp_server.server`` and return
    ``(internal_modules, external_packages)``."""

    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import mcpb_import_closure  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    return mcpb_import_closure.compute_closure(
        entry_module="mcp_server.server",
        project_root=REPO_ROOT,
        project_packages={"mcp_server", "backend", "cli", "fetcher"},
    )


def test_forbidden_external_deps_absent(closure: tuple[set[str], set[str]]) -> None:
    """No FastAPI/uvicorn/playwright/etc. in the MCP closure.

    These are the deps that would either balloon the bundle (mitmproxy,
    playwright) or break it because Claude Desktop's sandbox can't supply
    the system libraries (weasyprint needs cairo/pango).
    """

    _internal, external = closure
    forbidden = {
        "fastapi",
        "uvicorn",
        "playwright",
        "mitmproxy",
        "curl_cffi",
        "weasyprint",
        "watchdog",
    }
    leaked = forbidden & external
    assert not leaked, (
        f"MCPB bundle would pull forbidden deps: {sorted(leaked)}. "
        f"Find the import in the closure and either move it inside a "
        f"function body (lazy) or remove it from the MCP code path."
    )


def test_required_external_deps_present(closure: tuple[set[str], set[str]]) -> None:
    """``fastmcp`` and ``pydantic`` are in the closure.

    Proxy assertion that the analyzer actually walked the graph: if the
    walker silently returned an empty set, the forbidden check would also
    "pass" and we'd ship a broken bundle.
    """

    _internal, external = closure
    assert "fastmcp" in external, (
        "fastmcp missing from MCP closure — analyzer is broken or "
        "mcp_server/server.py stopped using FastMCP"
    )
    assert "pydantic" in external, (
        "pydantic missing from MCP closure — analyzer is broken"
    )


def test_forbidden_internal_modules_absent(closure: tuple[set[str], set[str]]) -> None:
    """No FastAPI-only, watcher-only, or CLI-only backend modules in the closure.

    These modules are part of the HTTP server, the background watcher, or the
    CLI (doctor / MCP-config detection/installation) — NOT the MCP read path. Their presence
    would either pull in FastAPI (backend.main, backend.routers.*, backend.deps)
    or watchdog (backend.cc_watcher) — both already covered by the external-dep
    canary, but this is a tighter assertion that catches the regression one layer
    earlier. backend.doctor, backend.mcp_config_detect, and backend.mcp_config_install
    are CLI-only modules that must never appear in the eager-import closure of the
    MCP server bundle.
    """

    internal, _external = closure
    forbidden_prefixes = (
        "backend.main",
        "backend.routers",
        "backend.cc_watcher",
        "backend.deps",
        "backend.doctor",          # CLI-only: doctor command
        "backend.mcp_config_detect",  # CLI-only: MCP config reader
        "backend.mcp_config_install",  # CLI-only: install/uninstall writer
        "backend.cli_style",       # CLI-only: terminal color helpers (imports click)
    )
    leaked = {m for m in internal if m.startswith(forbidden_prefixes)}
    assert not leaked, (
        f"MCPB closure pulled forbidden internal modules: {sorted(leaked)}. "
        f"These are FastAPI-only, watcher-only, or CLI-only; the MCP read path must "
        f"not depend on them."
    )


def test_closure_includes_mcp_server_module(closure: tuple[set[str], set[str]]) -> None:
    """``mcp_server.server`` itself is in the internal set.

    Sanity check that the entry module is present — guards against a
    silent "analyzer returned empty" regression.
    """

    internal, _external = closure
    assert "mcp_server.server" in internal


def test_closure_includes_known_backend_modules(closure: tuple[set[str], set[str]]) -> None:
    """The MCP server's known backend dependencies are in the closure.

    Per the plan §4, the MCP server imports specifically:
    ``backend.config``, ``backend.export``, ``backend.models``,
    ``backend.search``, ``backend.store``. If any of these is missing
    from the closure, the analyzer is dropping nodes.
    """

    internal, _external = closure
    required = {
        "backend.config",
        "backend.export",
        "backend.models",
        "backend.search",
        "backend.store",
    }
    missing = required - internal
    assert not missing, (
        f"MCPB closure missing direct backend deps of mcp_server.server: "
        f"{sorted(missing)}. The analyzer is dropping nodes."
    )
