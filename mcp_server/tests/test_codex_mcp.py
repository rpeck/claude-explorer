"""N8: the 5 MCP tools work for Codex, and the MCPB bundle copies Codex.

Plan §4.3 (the MCP caller policy), §4.5 (the MCP fields), §4.6 (the bundle),
D5 (an MCPB-only user has no watcher, so a tool call copies), D14 (the
absolute Codex home never reaches a model), and D15 (``sync_pending``).
Every test builds a synthetic Codex home with ``backend.tests.codex_fake``
at ``~/.codex`` under a temp home; nothing reads a real ``~/.codex``.

Contract (orchestrator, 2026-11-19):

- ``list_sessions`` entries and ``get_session_outline`` carry
  ``source_name`` and ``source_home`` for Codex, and ``notices`` when it is
  not empty. No MCP output holds ``source_home_abs`` or the absolute home.
- The Markdown header of a Codex export has the ``**Thread ID:**``,
  ``**Codex name:**``, and ``**Codex home:**`` lines.
- The bundle's stripped ``pyproject.toml`` lists
  ``zstandard>=0.23; python_version < "3.14"``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from backend.tests import codex_fake as fake
from backend.tests._platform_home import patch_home

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-mcpb.py"
VISIBLE = sorted(t for t in fake.ALL_THREADS if t != fake.THREAD_ARCHIVED)


def _tool(name: str, **kwargs: Any) -> Any:
    from mcp_server import server

    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


@dataclass
class McpCodex:
    home: pathlib.Path
    codex: pathlib.Path
    data_dir: pathlib.Path

    @property
    def archive(self) -> pathlib.Path:
        from backend.archive_sync import home_key

        return self.data_dir / "codex" / home_key(self.codex)

    def absolute_forms(self) -> list[str]:
        return sorted({str(self.codex), os.path.realpath(self.codex), self.codex.as_posix()})


@pytest.fixture
def cm(isolated_data_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[McpCodex]:
    from backend import config
    from mcp_server import server

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    codex = fake.build_codex_home(home / ".codex")
    monkeypatch.setenv("CODEX_HOME", str(codex))
    config.get_settings.cache_clear()
    server.reset_archive_sync_for_tests()
    try:
        yield McpCodex(home=home, codex=codex, data_dir=isolated_data_dir)
    finally:
        server.reset_archive_sync_for_tests()
        config.get_settings.cache_clear()


def _by_uuid(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["uuid"]: s for s in result["sessions"]}


# ---------------------------------------------------------------------------
# Server text
# ---------------------------------------------------------------------------


def test_the_server_instructions_name_claude_and_codex() -> None:
    from mcp_server.server import mcp

    instructions = getattr(mcp, "instructions", None) or getattr(mcp, "_instructions", None) or ""
    assert "saved conversation history (Claude and Codex)" in instructions


def test_list_sessions_documents_the_codex_source() -> None:
    from mcp_server import server

    fn = getattr(server.list_sessions, "fn", server.list_sessions)
    assert '"CODEX"' in (fn.__doc__ or "")


# ---------------------------------------------------------------------------
# The 5 tools
# ---------------------------------------------------------------------------


def test_an_mcp_only_list_copies_codex_and_shows_its_sessions(cm: McpCodex) -> None:
    assert not (cm.data_dir / "codex").exists()

    result = _tool("list_sessions", source="CODEX", limit=100)

    assert sorted(_by_uuid(result)) == VISIBLE
    assert (cm.archive / "threads" / f"{fake.THREAD_PAGINATED}.json").is_file()
    assert "sync_pending" not in result


def test_list_sessions_entries_carry_the_codex_name_and_home(cm: McpCodex) -> None:
    sessions = _by_uuid(_tool("list_sessions", source="CODEX", limit=100))

    pag = sessions[fake.THREAD_PAGINATED]
    assert pag["source"] == "CODEX"
    assert pag["name"] == "Alpha parser refactor"
    assert pag["source_name"] == "Alpha parser refactor"
    assert pag["source_home"] == "~/.codex"
    assert pag["project"] == "proj-alpha"
    assert sessions[fake.THREAD_LEGACY]["source_name"] is None


def test_list_sessions_entries_carry_their_notices(cm: McpCodex) -> None:
    sessions = _by_uuid(_tool("list_sessions", source="CODEX", limit=100))
    assert "Imported from Claude Code" in sessions[fake.THREAD_IMPORTED]["notices"]


def test_list_sessions_search_finds_a_codex_message(cm: McpCodex) -> None:
    sessions = _by_uuid(_tool("list_sessions", query="semicolon", limit=100))

    hit = sessions[fake.THREAD_LEGACY]
    assert hit["source"] == "CODEX"
    assert hit["source_home"] == "~/.codex"
    assert hit["match_count"] >= 1


def test_list_projects_lists_the_codex_projects(cm: McpCodex) -> None:
    projects = {p["project"] for p in _tool("list_projects", source="CODEX")}
    assert {"proj-alpha", "proj-beta", "proj-delta"} <= projects


def test_get_session_outline_of_a_codex_session(cm: McpCodex) -> None:
    out = _tool("get_session_outline", conversation_id=fake.THREAD_PAGINATED)

    assert out["source"] == "CODEX"
    assert out["source_name"] == "Alpha parser refactor"
    assert out["source_home"] == "~/.codex"
    assert out["message_count"] == len(out["messages"]) > 0
    assert out["messages"][0]["sender"] == "human"
    assert any("warning" in n for n in out["notices"])


def test_get_messages_of_a_legacy_codex_session(cm: McpCodex) -> None:
    msgs = _tool("get_messages", conversation_id=fake.THREAD_LEGACY, include_tool_results=True)

    text = json.dumps(msgs)
    assert "Line 3 of main.c needs a semicolon." in text
    assert "Why does the build fail?" in text
    assert "missing ';'" in text


def test_export_session_header_names_the_thread_the_codex_name_and_the_home(cm: McpCodex) -> None:
    md = _tool("export_session", conversation_id=fake.THREAD_PAGINATED)

    assert f"**Thread ID:** {fake.THREAD_PAGINATED}" in md
    assert "**Codex name:** Alpha parser refactor" in md
    assert "**Codex home:** ~/.codex" in md


def test_a_sliced_export_session_keeps_the_codex_header(cm: McpCodex) -> None:
    md = _tool("export_session", conversation_id=fake.THREAD_LEGACY, start_position=0, end_position=0)

    assert f"**Thread ID:** {fake.THREAD_LEGACY}" in md
    assert "**Codex name:** none" in md


def test_no_mcp_output_holds_the_absolute_codex_home(cm: McpCodex) -> None:
    outputs = [
        _tool("list_sessions", source="CODEX", limit=100),
        _tool("list_sessions", query="semicolon", limit=100),
        _tool("list_projects", source="CODEX"),
    ]
    for tid in fake.ALL_THREADS:
        outputs.append(_tool("get_session_outline", conversation_id=tid))
        outputs.append(_tool("get_messages", conversation_id=tid, include_tool_results=True))
        outputs.append(_tool("export_session", conversation_id=tid))

    text = json.dumps(outputs, default=str)
    assert "source_home_abs" not in text
    for form in cm.absolute_forms():
        assert form not in text


def test_a_busy_codex_lock_reads_the_archive_as_it_is_and_sets_sync_pending(cm: McpCodex) -> None:
    from backend.archive_sync import LOCK_FILE_NAME, ArchiveLock
    from mcp_server import server

    _tool("list_sessions", source="CODEX", limit=100)
    with open(cm.codex / "session_index.jsonl", "ab") as fh:
        fh.write(fake.encode_lines([{"id": fake.THREAD_PAGINATED, "thread_name": "Alpha renamed",
                                     "updated_at": "2026-11-11T08:00:00.000Z"}]))
    lock = ArchiveLock(cm.archive / LOCK_FILE_NAME)
    assert lock.acquire(1)
    try:
        server.reset_archive_sync_for_tests()
        busy = _tool("list_sessions", source="CODEX", limit=100)
        outline = _tool("get_session_outline", conversation_id=fake.THREAD_PAGINATED)
    finally:
        lock.release()

    assert busy["sync_pending"] is True
    assert outline["sync_pending"] is True
    assert _by_uuid(busy)[fake.THREAD_PAGINATED]["name"] == "Alpha parser refactor"

    server.reset_archive_sync_for_tests()
    after = _tool("list_sessions", source="CODEX", limit=100)
    assert "sync_pending" not in after
    assert _by_uuid(after)[fake.THREAD_PAGINATED]["name"] == "Alpha renamed"


def test_a_missing_codex_home_creates_nothing_and_the_archive_still_lists(
    cm: McpCodex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import config
    from mcp_server import server

    _tool("list_sessions", source="CODEX", limit=100)
    missing = cm.home / "no-codex-here"
    monkeypatch.setenv("CODEX_HOME", str(missing))
    config.get_settings.cache_clear()
    server._settings = None
    server._store = None
    server.reset_archive_sync_for_tests()

    result = _tool("list_sessions", source="CODEX", limit=100)

    assert sorted(_by_uuid(result)) == VISIBLE
    assert not missing.exists()
    assert "sync_pending" not in result


# ---------------------------------------------------------------------------
# The MCPB bundle (§4.6)
# ---------------------------------------------------------------------------


def _load_build_module():
    spec = importlib.util.spec_from_file_location("build_mcpb_codex", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_mcpb_codex"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    out_dir = tmp_path_factory.mktemp("mcpb_codex") / "bundle"
    return _load_build_module().build_bundle(output_dir=out_dir, project_root=REPO_ROOT)


def test_the_bundle_pyproject_lists_zstandard_below_python_3_14(bundle: pathlib.Path) -> None:
    deps = tomllib.loads((bundle / "pyproject.toml").read_text())["project"]["dependencies"]
    zstd = [d for d in deps if d.replace(" ", "").startswith("zstandard")]

    assert len(zstd) == 1, deps
    spec = zstd[0].replace(" ", "").replace("'", '"')
    assert spec.startswith("zstandard>=0.23;")
    assert 'python_version<"3.14"' in spec


def test_the_bundle_contains_the_codex_modules(bundle: pathlib.Path) -> None:
    server = bundle / "server"
    assert (server / "backend" / "sources" / "codex.py").is_file()
    for path in sorted((REPO_ROOT / "backend" / "codex").glob("*.py")):
        assert (server / "backend" / "codex" / path.name).is_file(), path.name


def test_the_bundle_readme_names_codex(bundle: pathlib.Path) -> None:
    assert "Codex" in (REPO_ROOT / "assets" / "mcpb-README.md").read_text("utf-8")
    assert "Codex" in (bundle / "README.md").read_text("utf-8")


_SMOKE = """
import json, os, sys
repo = os.path.realpath(sys.argv[1])
server_dir = os.path.realpath(sys.argv[2])
sys.path[:] = [p for p in sys.path if os.path.realpath(p or os.getcwd()) != repo]
sys.path.insert(0, server_dir)

import mcp_server.server as server

fn = getattr(server.list_sessions, "fn", server.list_sessions)
result = fn(source="CODEX", limit=100)
outside = sorted(
    name for name, mod in list(sys.modules.items())
    if name.split(".")[0] in ("backend", "mcp_server", "fetcher", "cli")
    and getattr(mod, "__file__", None)
    and not os.path.realpath(mod.__file__).startswith(server_dir + os.sep)
)
print(json.dumps({"uuids": sorted(s["uuid"] for s in result["sessions"]), "outside": outside}))
"""


def test_the_bundle_copies_and_lists_codex_in_isolation(bundle: pathlib.Path, tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    data_dir = home / ".claude-explorer" / "conversations"
    data_dir.mkdir(parents=True)
    codex = fake.build_codex_home(home / ".codex")
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "CLAUDE_EXPLORER_DATA_DIR": str(data_dir),
        "CLAUDE_DIR": str(tmp_path / "claude"),
        "CLAUDE_DESKTOP_APP_DIR": str(tmp_path / "desktop_app"),
        "CODEX_HOME": str(codex),
        "CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION": "1",
    }
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-c", _SMOKE, str(REPO_ROOT), str(bundle / "server")],
        check=False, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["outside"] == [], "these modules came from outside the bundle"
    assert out["uuids"] == VISIBLE
    assert list((data_dir / "codex").glob(f"*/threads/{fake.THREAD_PAGINATED}.json"))
