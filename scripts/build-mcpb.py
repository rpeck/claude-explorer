"""Build the MCPB (Claude Desktop extension) bundle for claude-explorer.

Output: ``dist/claude-explorer-${VERSION}.mcpb`` — a single zip file that
Claude Desktop accepts as a drag-drop extension install. Wraps the
existing stdio MCP server (``mcp_server/server.py``, 5 tools over
FastMCP) so a non-CLI user can light up the conversation-archive tools
without editing ``claude_desktop_config.json`` by hand.

Pipeline:

1. Clean ``build/mcpb/`` work dir.
2. Compute the import closure of ``mcp_server.server`` via
   ``scripts.mcpb_import_closure``. The closure analyzer is the canary —
   if a future change pulls FastAPI or weasyprint into the MCP path it
   fails here, NOT at install-time on a user's machine.
3. Copy the closed-over project modules into ``build/mcpb/server/``.
   Append a small set of dynamically-imported modules
   (``backend.cowork_reader``, ``backend.summary_cache``,
   ``backend.search_index``) that static analysis cannot see because
   ``backend.store`` / ``backend.search`` lazy-import them.
4. Write ``build/mcpb/server/main.py`` — thin entrypoint that just
   delegates to ``mcp_server.server.main``.
5. Write the bundle's stripped ``pyproject.toml`` (only the deps the MCP
   path actually uses; pinned by the closure analyzer's external set).
6. Write ``manifest.json`` from the template below, stamping
   ``version`` from ``mcp_server.__version__`` (single source of truth).
7. Copy ``assets/mcpb-icon.png`` and ``assets/mcpb-README.md`` into the
   bundle (added in commit 4).
8. Write ``.mcpbignore``.
9. Run ``mcpb pack`` to zip ``build/mcpb/`` into
   ``dist/claude-explorer-${VERSION}.mcpb`` (added in commit 5).

The script has zero third-party deps beyond stdlib so it can run on a
minimal CI runner before any ``uv sync``.

Manual install path (post-commit 5):

    python scripts/build-mcpb.py
    # → drag dist/claude-explorer-1.0.6.mcpb into Claude Desktop
    #   Settings → Extensions

Run ``--help`` for the full CLI surface.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Modules that ``mcp_server.server`` reaches at runtime but NOT at
# module-load time (e.g. ``from .cowork_reader import …`` inside a
# function body). The closure analyzer correctly skips these because it
# only walks eager imports, but the bundle still needs them.
DYNAMIC_IMPORT_MODULES: tuple[str, ...] = (
    "backend.cowork_reader",
    "backend.summary_cache",
    "backend.search_index",
)

# External deps the MCP code path actually uses. Asserted at build time
# against the closure analyzer's external set — see ``_assert_deps``.
EXPECTED_EXTERNAL_DEPS: frozenset[str] = frozenset(
    {"fastmcp", "pydantic", "orjson", "platformdirs"}
)

# Bundle-relative paths for non-Python assets.
ICON_PATH = "icon.png"
README_PATH = "README.md"


def _load_closure_analyzer():
    """Import ``scripts/mcpb_import_closure.py`` without polluting
    ``sys.path`` permanently."""

    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import mcpb_import_closure  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return mcpb_import_closure


def _read_version() -> str:
    """Read ``mcp_server.__version__`` without importing the whole
    package (which would pull every backend dep on first import)."""

    init_py = (REPO_ROOT / "mcp_server" / "__init__.py").read_text(encoding="utf-8")
    for line in init_py.splitlines():
        line = line.strip()
        if line.startswith("__version__"):
            # `__version__ = "1.0.6"`  →  `1.0.6`
            _, _, rhs = line.partition("=")
            return rhs.strip().strip('"').strip("'")
    raise RuntimeError(
        "mcp_server/__init__.py does not define __version__ — "
        "commit 1 of the MCPB plan should have added it"
    )


def _assert_deps(external: set[str]) -> None:
    """Hard-fail if the closure analyzer surfaces an unexpected external
    package.

    The bundle's ``pyproject.toml`` enumerates ``EXPECTED_EXTERNAL_DEPS``
    by hand; if the analyzer says we now also need ``redis`` because
    somebody added a backend module that imports it, the build should
    fail rather than silently ship a bundle missing a dep at install
    time.
    """

    unexpected = external - EXPECTED_EXTERNAL_DEPS
    if unexpected:
        raise SystemExit(
            f"MCPB build: closure analyzer found new external deps "
            f"{sorted(unexpected)} not in EXPECTED_EXTERNAL_DEPS. Either "
            f"add them to EXPECTED_EXTERNAL_DEPS and the bundle's "
            f"pyproject.toml (and verify the dep is light enough to "
            f"ship), or remove the import."
        )
    missing = EXPECTED_EXTERNAL_DEPS - external
    if missing:
        raise SystemExit(
            f"MCPB build: EXPECTED_EXTERNAL_DEPS lists {sorted(missing)} "
            f"but the analyzer didn't surface them. Either the import "
            f"was removed (delete from EXPECTED_EXTERNAL_DEPS) or the "
            f"analyzer is broken."
        )


def _copy_internal_modules(
    internal: set[str],
    project_root: pathlib.Path,
    server_dir: pathlib.Path,
) -> None:
    """Copy the closed-over project files into ``build/mcpb/server/``.

    Preserves the package layout so ``import backend.config`` continues
    to work inside the bundle. Also writes empty ``__init__.py`` files
    for any package whose ``__init__.py`` wasn't reached by the closure
    walk (the analyzer follows imports, not directory structure, so
    ``backend/exporters/__init__.py`` might be skipped even though we
    need it for ``import backend.exporters.markdown`` to work).
    """

    for module in sorted(internal):
        parts = module.split(".")
        src_file = project_root.joinpath(*parts).with_suffix(".py")
        src_pkg = project_root.joinpath(*parts, "__init__.py")

        if src_file.exists():
            dest = server_dir.joinpath(*parts).with_suffix(".py")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
        elif src_pkg.exists():
            dest = server_dir.joinpath(*parts, "__init__.py")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_pkg, dest)

    # Ensure every package directory has an __init__.py — the closure
    # walk follows imports, so a package whose __init__.py contains no
    # imports won't be in `internal`. We need them anyway so Python
    # treats the dirs as packages.
    for pkg_dir in server_dir.rglob("*"):
        if pkg_dir.is_dir() and not (pkg_dir / "__init__.py").exists():
            # Skip dist-info / cache-style dirs.
            if pkg_dir.name.startswith((".", "__pycache__")):
                continue
            # Only create if the parent's tree includes a .py file
            # somewhere — avoids creating __init__.py in incidental
            # subdirs.
            if any(pkg_dir.rglob("*.py")):
                (pkg_dir / "__init__.py").touch()


def _write_main_entrypoint(server_dir: pathlib.Path) -> None:
    """Write ``server/main.py`` — the entrypoint Claude Desktop invokes.

    Claude Desktop runs ``uv run --directory ${__dirname} server/main.py``
    after the manifest is parsed (see ``manifest.json`` below). This
    file just imports and runs ``mcp_server.server.main`` so the bundle
    stays a thin shell around the in-repo MCP server logic.
    """

    main_path = server_dir / "main.py"
    main_path.write_text(
        '"""Entrypoint for the claude-explorer MCPB bundle.\n'
        "\n"
        "Claude Desktop launches this via the manifest's mcp_config\n"
        "(`uv run --directory ${__dirname} server/main.py`).\n"
        '"""\n'
        "\n"
        "from mcp_server.server import main\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
        encoding="utf-8",
    )


def _write_bundle_pyproject(
    bundle_root: pathlib.Path,
    version: str,
) -> None:
    """Write the bundle's stripped ``pyproject.toml``.

    UV reads this on first launch to resolve + install the MCP-only dep
    closure into a host-managed venv. The deps here MUST be a subset of
    the main project's ``pyproject.toml`` and must match
    ``EXPECTED_EXTERNAL_DEPS`` — enforced by ``_assert_deps`` above.
    """

    content = (
        "[project]\n"
        f'name = "claude-explorer-mcp"\n'
        f'version = "{version}"\n'
        f'description = "MCP server bundle for the claude-explorer conversation archive"\n'
        f'requires-python = ">=3.11"\n'
        f"dependencies = [\n"
        f'    "fastmcp>=3.0",\n'
        f'    "pydantic>=2.0",\n'
        f'    "orjson>=3.10",\n'
        f'    "platformdirs>=4.0",\n'
        f"]\n"
    )
    (bundle_root / "pyproject.toml").write_text(content, encoding="utf-8")


def _write_manifest(bundle_root: pathlib.Path, version: str) -> None:
    """Write ``manifest.json`` — the Claude Desktop extension descriptor.

    Anchored against ``examples/file-manager-python/manifest.json`` in
    the dxt/mcpb spec repo. ``manifest_version: "0.4"`` unlocks
    ``server.type: "uv"`` — Claude Desktop ships Node but NOT Python,
    so UV handles the Python install + dep resolve on first launch.

    Tool entries here are display metadata; the actual ``@mcp.tool``
    registrations live in ``mcp_server/server.py`` and are what the
    runtime exposes.
    """

    manifest = {
        "manifest_version": "0.4",
        "name": "claude-explorer",
        "display_name": "Claude Explorer (Conversation Archive)",
        "version": version,
        "description": (
            "Search and read your saved Claude Desktop and Claude Code "
            "conversations from inside Claude."
        ),
        "long_description": (
            "Exposes the local conversation archive at "
            "~/.claude-explorer/conversations/ as 5 MCP tools: list_sessions, "
            "list_projects, get_session_outline, get_messages, export_session. "
            "Read-only — no destructive writes. Conversations are NOT fetched "
            "by this extension; install the `claude-explorer` CLI separately "
            "to capture and refresh."
        ),
        "author": {
            "name": "Raymond Peck",
            "url": "https://github.com/rpeck/claude-explorer",
        },
        "repository": {
            "type": "git",
            "url": "https://github.com/rpeck/claude-explorer",
        },
        "homepage": "https://github.com/rpeck/claude-explorer",
        "documentation": "https://github.com/rpeck/claude-explorer#readme",
        "support": "https://github.com/rpeck/claude-explorer/issues",
        "icon": ICON_PATH,
        "keywords": [
            "claude",
            "claude-code",
            "archive",
            "conversations",
            "search",
            "mcp",
        ],
        "license": "Apache-2.0",
        "server": {
            "type": "uv",
            "entry_point": "server/main.py",
            "mcp_config": {
                "command": "uv",
                "args": [
                    "run",
                    "--directory",
                    "${__dirname}",
                    "server/main.py",
                ],
                "env": {
                    "CLAUDE_EXPLORER_DATA_DIR": "${user_config.data_dir}",
                },
            },
        },
        "tools": [
            {
                "name": "list_sessions",
                "description": (
                    "Search and list conversation sessions, filtered by "
                    "project, date, or text query"
                ),
            },
            {
                "name": "list_projects",
                "description": "List distinct projects with session counts",
            },
            {
                "name": "get_session_outline",
                "description": (
                    "Lightweight per-message summaries of a session, "
                    "cached in SQLite"
                ),
            },
            {
                "name": "get_messages",
                "description": "Full message content for specific message UUIDs",
            },
            {
                "name": "export_session",
                "description": "Markdown export of a full or partial session",
            },
        ],
        "user_config": {
            "data_dir": {
                "type": "directory",
                "title": "Conversation archive directory",
                "description": (
                    "Where claude-explorer stores fetched conversation "
                    "JSON. Defaults to ~/.claude-explorer/conversations/."
                ),
                "default": "${HOME}/.claude-explorer/conversations",
                "required": False,
            },
        },
        "compatibility": {
            "claude_desktop": ">=0.10.0",
            "platforms": ["darwin", "linux", "win32"],
            "runtimes": {"python": ">=3.11 <4"},
        },
    }
    (bundle_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def _write_mcpbignore(bundle_root: pathlib.Path) -> None:
    """Tell ``mcpb pack`` what to leave out of the final zip."""

    (bundle_root / ".mcpbignore").write_text(
        ".venv/\n"
        "__pycache__/\n"
        "*.pyc\n"
        ".pytest_cache/\n"
        ".mypy_cache/\n"
        "*.egg-info/\n"
        "uv.lock\n"
        "tests/\n",
        encoding="utf-8",
    )


def build_bundle(
    output_dir: pathlib.Path | None = None,
    project_root: pathlib.Path = REPO_ROOT,
) -> pathlib.Path:
    """Assemble the bundle into ``output_dir`` (default
    ``project_root/build/mcpb``) and return the path.

    Does NOT call ``mcpb pack`` yet — that's added in commit 5. After
    this returns, the directory is shaped exactly like the contents of
    the eventual ``.mcpb`` zip.
    """

    bundle_root = output_dir if output_dir is not None else project_root / "build" / "mcpb"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    server_dir = bundle_root / "server"
    server_dir.mkdir(parents=True, exist_ok=True)

    analyzer = _load_closure_analyzer()
    internal, external = analyzer.compute_closure(
        entry_module="mcp_server.server",
        project_root=project_root,
        project_packages={"mcp_server", "backend", "cli", "fetcher"},
    )

    # Pull in dynamically-imported modules and re-run the closure on
    # each one to capture any transitive deps the static walker missed.
    for dyn in DYNAMIC_IMPORT_MODULES:
        internal.add(dyn)
        dyn_internal, dyn_external = analyzer.compute_closure(
            entry_module=dyn,
            project_root=project_root,
            project_packages={"mcp_server", "backend", "cli", "fetcher"},
        )
        internal |= dyn_internal
        external |= dyn_external

    _assert_deps(external)

    _copy_internal_modules(internal, project_root, server_dir)
    _write_main_entrypoint(server_dir)

    version = _read_version()
    _write_bundle_pyproject(bundle_root, version)
    _write_manifest(bundle_root, version)
    _write_mcpbignore(bundle_root)

    # Icon + README come from assets/ (added in commit 4). Soft-skip if
    # absent so commit 3's tests don't depend on commit 4's assets.
    icon_src = project_root / "assets" / "mcpb-icon.png"
    if icon_src.exists():
        shutil.copy2(icon_src, bundle_root / ICON_PATH)
    readme_src = project_root / "assets" / "mcpb-README.md"
    if readme_src.exists():
        shutil.copy2(readme_src, bundle_root / README_PATH)

    return bundle_root


class MCPBPackError(RuntimeError):
    """Raised when ``mcpb pack`` is unavailable or fails."""


def pack_bundle(
    bundle_root: pathlib.Path,
    dist_dir: pathlib.Path,
    version: str,
) -> pathlib.Path:
    """Run ``mcpb pack`` against the assembled bundle dir and return the
    output ``.mcpb`` artifact path.

    Raises ``MCPBPackError`` (NOT ``SystemExit``) if the ``mcpb`` CLI is
    not on PATH so callers can choose how to handle it — the test
    suite SKIPs cleanly, the dev CLI prints an install hint and exits.

    Per ``PLANS/2026.06.04-mcpb-bundle.md`` §5 step 9. Install the CLI
    with::

        npm install -g @anthropic-ai/mcpb
    """

    if shutil.which("mcpb") is None:
        raise MCPBPackError(
            "mcpb CLI not found on PATH. Install with: "
            "npm install -g @anthropic-ai/mcpb"
        )

    dist_dir.mkdir(parents=True, exist_ok=True)
    artifact = dist_dir / f"claude-explorer-{version}.mcpb"
    if artifact.exists():
        artifact.unlink()

    result = subprocess.run(
        ["mcpb", "pack", str(bundle_root), str(artifact)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MCPBPackError(
            f"mcpb pack failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    if not artifact.exists():
        raise MCPBPackError(
            f"mcpb pack returned success but {artifact} was not created"
        )

    return artifact


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        default=None,
        help="Override the build directory (default: build/mcpb/)",
    )
    parser.add_argument(
        "--dist-dir",
        type=pathlib.Path,
        default=REPO_ROOT / "dist",
        help="Where to write the packed .mcpb (default: dist/)",
    )
    parser.add_argument(
        "--no-pack",
        action="store_true",
        help="Assemble the bundle dir but skip the mcpb pack step",
    )
    args = parser.parse_args()

    bundle_root = build_bundle(output_dir=args.output_dir)
    print(f"MCPB bundle assembled at {bundle_root}")

    if args.no_pack:
        return 0

    version = _read_version()
    try:
        artifact = pack_bundle(bundle_root, args.dist_dir, version)
    except MCPBPackError as e:
        print(f"\n[skip pack] {e}", file=sys.stderr)
        print(
            "Bundle dir is ready at "
            f"{bundle_root}; re-run with mcpb on PATH to produce the "
            f".mcpb artifact.",
            file=sys.stderr,
        )
        return 0

    size_kb = artifact.stat().st_size / 1024
    print(f"MCPB artifact written to {artifact} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
