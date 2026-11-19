"""N7: the MCPB bundle imports and runs the archive copy on its own (plan §4.6).

``backend.archive_sync`` reaches the MCP path in N7, so the bundle needs
``portalocker`` and every module that an MCP tool call imports lazily. The
closure analyzer sees only eager imports, so a lazy import that the bundle
lacks fails only at call time. This test builds the bundle into a temp
folder, then runs a tool call in a new interpreter that sees the bundle's
``server/`` folder and not the repository.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tomllib

import pytest

pytestmark = pytest.mark.xfail(strict=True, reason="N7 not implemented yet")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-mcpb.py"


def _load_build_module():
    spec = importlib.util.spec_from_file_location("build_mcpb_smoke", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_mcpb_smoke"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    out_dir = tmp_path_factory.mktemp("mcpb_smoke") / "bundle"
    return _load_build_module().build_bundle(output_dir=out_dir, project_root=REPO_ROOT)


def test_expected_external_deps_include_portalocker() -> None:
    assert "portalocker" in _load_build_module().EXPECTED_EXTERNAL_DEPS


def test_bundle_pyproject_lists_portalocker(bundle: pathlib.Path) -> None:
    deps = tomllib.loads((bundle / "pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    roots = {
        d.split(">=")[0].split("==")[0].split("<")[0].split(";")[0].strip()
        for d in deps
    }

    assert "portalocker" in roots


def test_bundle_contains_the_archive_copy_modules(bundle: pathlib.Path) -> None:
    server = bundle / "server"

    for rel in (
        "backend/archive_sync.py",
        "backend/write_gate.py",
        "backend/cloud_sync.py",
        "fetcher/atomic.py",
    ):
        assert (server / rel).is_file(), f"the bundle lacks {rel}"


_SMOKE = """
import json, os, sys
repo = os.path.realpath(sys.argv[1])
server_dir = os.path.realpath(sys.argv[2])
sys.path[:] = [p for p in sys.path if os.path.realpath(p or os.getcwd()) != repo]
sys.path.insert(0, server_dir)

import mcp_server.server as server

fn = getattr(server.list_sessions, "fn", server.list_sessions)
result = fn()
outside = sorted(
    name for name, mod in list(sys.modules.items())
    if name.split(".")[0] in ("backend", "mcp_server", "fetcher", "cli")
    and getattr(mod, "__file__", None)
    and not os.path.realpath(mod.__file__).startswith(server_dir + os.sep)
)
print(json.dumps({
    "keys": sorted(result),
    "outside": outside,
    "archive_sync_loaded": "backend.archive_sync" in sys.modules,
    "write_gate_loaded": "backend.write_gate" in sys.modules,
}))
"""


def test_bundle_runs_list_sessions_and_the_archive_copy_in_isolation(
    bundle: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    home = tmp_path / "home"
    data_dir = home / ".claude-explorer" / "conversations"
    data_dir.mkdir(parents=True)
    (tmp_path / "codex").mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "CLAUDE_EXPLORER_DATA_DIR": str(data_dir),
        "CLAUDE_DIR": str(tmp_path / "claude"),
        "CLAUDE_DESKTOP_APP_DIR": str(tmp_path / "desktop_app"),
        "CODEX_HOME": str(tmp_path / "codex"),
        "CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION": "1",
    }
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-c", _SMOKE, str(REPO_ROOT), str(bundle / "server")],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["outside"] == [], "these modules came from outside the bundle"
    assert "sessions" in out["keys"]
    assert out["write_gate_loaded"], "an MCP tool call must run the archive copy"
    assert out["archive_sync_loaded"]
