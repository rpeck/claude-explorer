"""End-to-end test for ``scripts/build-mcpb.py``.

Per ``PLANS/2026.06.04-mcpb-bundle.md`` §"Commit 3 — build script":

Builds the bundle into a tmp dir and asserts:

* ``manifest.json`` is valid JSON with ``manifest_version == "0.4"`` and
  ``server.type == "uv"``.
* ``server/main.py`` exists and parses cleanly.
* The stripped ``pyproject.toml`` contains only the allowed deps and
  reads the version from ``mcp_server.__version__``.
* The bundle dir is small (< 5 MB FAIL guard — if fetcher/ leaks in, this
  catches it).
* No forbidden modules in the bundle (regression guard against the
  closure analyzer being silently bypassed).

Does NOT run ``mcpb pack``. That's commit 5.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
BUILD_SCRIPT = SCRIPTS_DIR / "build-mcpb.py"


def _load_build_module():
    """Load ``scripts/build-mcpb.py`` as a module despite the hyphen in
    the filename."""

    spec = importlib.util.spec_from_file_location("build_mcpb", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_mcpb"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built_bundle(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Build the bundle into a tmp dir once per test module."""

    out_dir = tmp_path_factory.mktemp("mcpb_bundle") / "bundle"
    build = _load_build_module()
    return build.build_bundle(output_dir=out_dir, project_root=REPO_ROOT)


def test_manifest_is_valid_json(built_bundle: pathlib.Path) -> None:
    """``manifest.json`` exists, parses, and uses the v0.4 + uv shape
    Claude Desktop needs to launch the server."""

    manifest_path = built_bundle / "manifest.json"
    assert manifest_path.exists(), "build script did not write manifest.json"

    manifest = json.loads(manifest_path.read_text())

    assert manifest["manifest_version"] == "0.4", (
        "manifest_version must be 0.4 — required for server.type=uv per "
        "the dxt/mcpb spec"
    )
    assert manifest["server"]["type"] == "uv", (
        "server.type must be 'uv' — Claude Desktop ships Node, not "
        "Python, so we need the UV runtime to handle Python + dep "
        "install on first launch"
    )
    assert manifest["server"]["entry_point"] == "server/main.py"
    assert manifest["server"]["mcp_config"]["command"] == "uv"
    assert manifest["name"] == "claude-explorer"
    # Tools block should list all 5 — that's the catalog-display surface.
    tool_names = {t["name"] for t in manifest["tools"]}
    assert tool_names == {
        "list_sessions",
        "list_projects",
        "get_session_outline",
        "get_messages",
        "export_session",
    }


def test_manifest_version_matches_mcp_server_version(
    built_bundle: pathlib.Path,
) -> None:
    """The manifest's ``version`` is read from ``mcp_server.__version__``.

    This closes the single-source-of-truth loop from commit 1: bumping
    ``mcp_server/__init__.py:__version__`` updates the bundle's manifest
    AND the pip package AND the MCP serverInfo, all together.
    """

    import mcp_server

    manifest = json.loads((built_bundle / "manifest.json").read_text())
    assert manifest["version"] == mcp_server.__version__


def test_server_main_entrypoint_parses(built_bundle: pathlib.Path) -> None:
    """``server/main.py`` exists and is syntactically valid Python.

    Cannot ``import`` it inside the test (it would pull every backend
    dep at import time and pollute sys.modules across tests), but we
    can at least verify it parses and imports the right symbol.
    """

    import ast

    main_py = built_bundle / "server" / "main.py"
    assert main_py.exists(), "build script did not write server/main.py"

    source = main_py.read_text()
    ast.parse(source)  # Raises SyntaxError on parse failure.
    assert "from mcp_server.server import main" in source


def test_bundle_pyproject_is_stripped(built_bundle: pathlib.Path) -> None:
    """The bundle's ``pyproject.toml`` lists ONLY the deps the MCP path
    actually uses.

    Bullet-proofs against accidentally shipping FastAPI / weasyprint /
    playwright / mitmproxy in a bundle that's supposed to be tiny.
    """

    import tomllib

    pyproject = built_bundle / "pyproject.toml"
    assert pyproject.exists(), "build script did not write pyproject.toml"

    data = tomllib.loads(pyproject.read_text())
    deps = data["project"]["dependencies"]
    dep_roots = {d.split(">=")[0].split("==")[0].split("<")[0].strip() for d in deps}

    allowed = {"fastmcp", "pydantic", "orjson", "platformdirs"}
    forbidden = {"fastapi", "uvicorn", "playwright", "mitmproxy", "curl_cffi", "weasyprint", "watchdog"}

    assert dep_roots == allowed, (
        f"Bundle pyproject.toml deps mismatch: got {sorted(dep_roots)}, "
        f"expected {sorted(allowed)}"
    )
    assert not (dep_roots & forbidden), (
        f"Bundle pyproject.toml leaked forbidden deps: "
        f"{sorted(dep_roots & forbidden)}"
    )


def test_bundle_does_not_ship_forbidden_modules(
    built_bundle: pathlib.Path,
) -> None:
    """Walk the bundle tree and verify no FastAPI-only modules slipped in.

    This is the file-system-level corollary of the closure-analyzer
    canary: even if the analyzer started misreporting, the file walk
    catches a regression here.
    """

    server_dir = built_bundle / "server"
    backend_dir = server_dir / "backend"
    if backend_dir.exists():
        assert not (backend_dir / "main.py").exists(), (
            "backend/main.py shipped in bundle — it pulls FastAPI"
        )
        routers_dir = backend_dir / "routers"
        assert not routers_dir.exists() or not list(routers_dir.glob("*.py")), (
            "backend/routers/ shipped in bundle — pulls FastAPI"
        )
        assert not (backend_dir / "cc_watcher.py").exists(), (
            "backend/cc_watcher.py shipped in bundle — pulls watchdog"
        )
        assert not (backend_dir / "deps.py").exists(), (
            "backend/deps.py shipped in bundle — pulls FastAPI"
        )


def test_bundle_includes_required_modules(built_bundle: pathlib.Path) -> None:
    """Sanity: the bundle includes the modules the MCP server needs at
    runtime, including the dynamically-imported ones."""

    server_dir = built_bundle / "server"
    backend_dir = server_dir / "backend"

    assert (server_dir / "mcp_server" / "server.py").exists()
    assert (backend_dir / "config.py").exists()
    assert (backend_dir / "store.py").exists()
    assert (backend_dir / "search.py").exists()
    assert (backend_dir / "models.py").exists()
    assert (backend_dir / "export.py").exists()

    # Dynamic-import modules — these would be missing if the build
    # script didn't explicitly include them.
    assert (backend_dir / "cowork_reader.py").exists(), (
        "backend.cowork_reader missing — it's lazy-imported from "
        "backend.store, so the build script must include it explicitly"
    )
    assert (backend_dir / "summary_cache.py").exists()
    assert (backend_dir / "search_index.py").exists()


def test_bundle_size_under_5mb(built_bundle: pathlib.Path) -> None:
    """Bundle directory is < 5 MB.

    FAIL guard against accidental fetcher/ inclusion (mitmproxy alone is
    > 30 MB) or frontend/ inclusion (the React build is multi-MB). The
    real bundle should be well under 1 MB — 5 MB is the loose upper
    bound that still catches the bad-include regressions.
    """

    total = sum(
        f.stat().st_size for f in built_bundle.rglob("*") if f.is_file()
    )
    mb = total / (1024 * 1024)
    assert mb < 5.0, (
        f"MCPB bundle dir is {mb:.2f} MB (>= 5 MB) — almost certainly "
        f"a regression that pulled fetcher/, frontend/, or a binary dep "
        f"into the closure. Inspect {built_bundle} for the culprit."
    )


def test_mcpbignore_exists(built_bundle: pathlib.Path) -> None:
    """``.mcpbignore`` tells the packer what to drop from the final zip."""

    ignore = built_bundle / ".mcpbignore"
    assert ignore.exists()
    text = ignore.read_text()
    assert "__pycache__/" in text
    assert "tests/" in text
