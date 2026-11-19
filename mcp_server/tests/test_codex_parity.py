"""N9: the committed Codex e2e fixtures, and web API / MCP parity.

Plan §6.1 (N9), §4.2 (the archive layout), §4.5 (the output fields), AC3
("the UI and MCP agree"), D7 (every reader reads the archive), and D14
(MCP shows the Codex home as ``~/...`` and never the absolute path).

Contract (orchestrator, 2026-11-19):

- ``scripts/generate_e2e_fixtures.py`` runs the real copier on
  ``backend.tests.codex_fake.build_codex_home`` (the NF default corpus).
  It renames the home folder to ``fixture000000`` and writes ``home_path``
  as ``/home/fixture/.codex``. Each meta file's ``source_path`` starts with
  ``/home/fixture/.codex/``.
- The Playwright backend gets ``CODEX_HOME`` = a folder that does not
  exist, as a string literal or ``path.join(REPO_ROOT, ...)``.

The parity tests read a temp copy of the committed fixture tree, with
``CODEX_HOME`` at a missing folder (as in e2e), so no test writes into the
repository. The ``home`` case rewrites the copy's ``home_path`` to a folder
under a temp ``~``, so both readers must show ``~/.codex``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from backend.tests import codex_fake as fake
from backend.tests._platform_home import patch_home

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_CODEX = REPO_ROOT / "backend" / "tests" / "fixtures" / "desktop" / "codex"
FIXTURE_KEY = "fixture000000"
FIXTURE_HOME_PATH = "/home/fixture/.codex"
PLAYWRIGHT_CONFIG = REPO_ROOT / "frontend" / "playwright.config.ts"
#: The threads that ``list_sessions`` and the web list show by default.
VISIBLE = sorted(t for t in fake.ALL_THREADS if t != fake.THREAD_ARCHIVED)


def _fixture_home() -> Path:
    return FIXTURE_CODEX / FIXTURE_KEY


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _tree_hash(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# The committed fixtures (the generator rules)
# ---------------------------------------------------------------------------


def test_the_fixture_archive_has_one_home_folder_named_fixture000000() -> None:
    homes = sorted(p.name for p in FIXTURE_CODEX.iterdir() if p.is_dir())
    assert homes == [FIXTURE_KEY]


def test_the_fixture_home_json_names_the_fixed_home_path() -> None:
    data = _read_json(_fixture_home() / "home.json")
    assert data["version"] == 1
    assert data["home_path"] == FIXTURE_HOME_PATH
    assert isinstance(data["first_seen_ms"], int)
    assert set(data) == {"version", "home_path", "first_seen_ms"}


def test_the_fixture_has_a_thread_file_for_every_fake_corpus_thread() -> None:
    threads = _fixture_home() / "threads"
    for tid in fake.ALL_THREADS:
        data = _read_json(threads / f"{tid}.json")
        assert data["version"] == 1
        assert data["thread_id"] == tid
        assert data["home_key"] == FIXTURE_KEY


def test_every_fixture_meta_file_names_a_source_inside_the_fixed_home() -> None:
    metas = sorted((_fixture_home() / "rollouts").glob("*.meta.json"))
    assert metas, "no rollout meta files"
    for path in metas:
        data = _read_json(path)
        assert data["source_path"].startswith(FIXTURE_HOME_PATH + "/"), path.name
        assert data["stem"] + ".meta.json" == path.name


_MACHINE_PATHS = re.compile(r"/Users/|/private/|/var/folders|/tmp/|\\Users\\|AppData|pytest-of-")


def test_no_codex_fixture_file_holds_a_path_of_the_machine_that_made_it() -> None:
    files = [p for p in FIXTURE_CODEX.rglob("*") if p.is_file()]
    assert files
    for path in files:
        text = path.read_bytes().decode("utf-8", "replace")
        match = _MACHINE_PATHS.search(text)
        assert match is None, f"{path.relative_to(FIXTURE_CODEX)}: {match.group(0) if match else ''}"
        assert str(Path.home()) not in text, path.relative_to(FIXTURE_CODEX)


@pytest.fixture
def fresh_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The real copier's archive of the NF corpus, in a temp data dir."""
    from backend import config
    from backend.archive_sync import SyncContext, home_key
    from backend.sources.registry import get_adapter
    from backend.store import ConversationStore

    codex = fake.build_codex_home(tmp_path / "home" / ".codex")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        store = ConversationStore(data_dir=data_dir, claude_dir=tmp_path / "claude")
        report = get_adapter("CODEX", store).archive_sync(
            SyncContext(settings=settings, reason="cli", lock_timeout_s=10)
        )
        assert report is not None and report.skipped_reason is None
        yield data_dir / "codex" / home_key(codex)
    finally:
        config.get_settings.cache_clear()


def test_the_fixture_rollouts_equal_the_real_copier_output(fresh_copy: Path) -> None:
    fresh = sorted((fresh_copy / "rollouts").glob("*.jsonl"))
    assert len(fresh) == len(fake.ALL_THREADS)
    for path in fresh:
        committed = _fixture_home() / "rollouts" / path.name
        assert committed.is_file(), path.name
        assert committed.read_bytes() == path.read_bytes(), path.name


def test_the_fixture_thread_files_equal_the_real_copier_output_but_the_home_key(fresh_copy: Path) -> None:
    for tid in fake.ALL_THREADS:
        fresh = _read_json(fresh_copy / "threads" / f"{tid}.json")
        committed = _read_json(_fixture_home() / "threads" / f"{tid}.json")
        assert committed.pop("home_key") == FIXTURE_KEY
        fresh.pop("home_key")
        assert committed == fresh, tid


def _codex_home_expression(text: str) -> str:
    lines = [ln for ln in text.splitlines() if re.match(r"\s*CODEX_HOME\s*:", ln)]
    assert len(lines) == 1, "playwright.config.ts needs one CODEX_HOME entry in the backend env"
    return lines[0].split(":", 1)[1].strip().rstrip(",").strip()


def _resolve(expr: str) -> Path:
    literal = re.fullmatch(r"""(['"`])(.+)\1""", expr)
    if literal:
        path = Path(literal.group(2))
        return path if path.is_absolute() else REPO_ROOT / "frontend" / path
    joined = re.fullmatch(r"path\.(?:join|resolve)\(\s*REPO_ROOT\s*,(.+)\)", expr)
    assert joined, f"unsupported CODEX_HOME expression: {expr}"
    parts = re.findall(r"""['"`]([^'"`]+)['"`]""", joined.group(1))
    assert parts, expr
    return REPO_ROOT.joinpath(*parts)


def test_the_e2e_backend_points_codex_home_at_a_missing_folder() -> None:
    # First a check of the parser itself, so a wrong parse cannot pass by accident.
    assert _resolve("path.join(REPO_ROOT, 'a', \"b\")") == REPO_ROOT / "a" / "b"
    # An absolute path on this platform (Windows needs a drive).
    absolute = Path(REPO_ROOT.anchor) / "nowhere" / "codex"
    assert _resolve(f"'{absolute}'") == absolute

    expr = _codex_home_expression(PLAYWRIGHT_CONFIG.read_text("utf-8"))
    target = _resolve(expr)
    assert not target.exists(), target
    assert target.resolve() != (Path.home() / ".codex").resolve()


# ---------------------------------------------------------------------------
# Web API and MCP parity
# ---------------------------------------------------------------------------


@dataclass
class Parity:
    data_dir: Path
    missing_home: Path
    home_display: str
    home_abs: str

    @property
    def archive(self) -> Path:
        return self.data_dir / "codex"


@pytest.fixture(params=["fixed-home", "user-home"])
def parity(
    request: pytest.FixtureRequest,
    isolated_data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Parity]:
    from backend import config
    from backend.sources.codex import clear_codex_caches
    from backend.store import invalidate_detail_dict_cache
    from mcp_server import server

    shutil.copytree(FIXTURE_CODEX, isolated_data_dir / "codex")
    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    if request.param == "user-home":
        home_abs = str(home / ".codex")
        home_json = isolated_data_dir / "codex" / FIXTURE_KEY / "home.json"
        data = _read_json(home_json)
        data["home_path"] = home_abs
        home_json.write_text(json.dumps(data, indent=2) + "\n")
        display = "~/.codex"
    else:
        home_abs = display = FIXTURE_HOME_PATH
    missing = tmp_path / "no-codex-here"
    monkeypatch.setenv("CODEX_HOME", str(missing))
    config.get_settings.cache_clear()
    clear_codex_caches()
    invalidate_detail_dict_cache()
    server.reset_archive_sync_for_tests()
    try:
        yield Parity(data_dir=isolated_data_dir, missing_home=missing, home_display=display, home_abs=home_abs)
    finally:
        server.reset_archive_sync_for_tests()
        clear_codex_caches()
        invalidate_detail_dict_cache()
        config.get_settings.cache_clear()


@pytest.fixture
def api() -> Any:
    from fastapi.testclient import TestClient

    from backend.main import app

    # No ``with``: the lifespan (watcher, index build) stays off.
    return TestClient(app)


def _tool(name: str, **kwargs: Any) -> Any:
    from mcp_server import server

    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


def _web_detail(api: Any, tid: str) -> dict[str, Any]:
    r = api.get(f"/api/conversations/{tid}")
    assert r.status_code == 200, r.text
    return r.json()


def _blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The web content blocks in the MCP ``get_messages`` form (tool results on)."""
    out: list[dict[str, Any]] = []
    for b in blocks:
        kind = b.get("type")
        if kind == "text" and (b.get("text") or "").strip():
            out.append({"type": "text", "text": b["text"]})
        elif kind == "tool_use":
            entry: dict[str, Any] = {"type": "tool_use", "name": b.get("name") or ""}
            if b.get("id"):
                entry["id"] = b["id"]
            if b.get("input"):
                entry["input"] = b["input"]
            out.append(entry)
        elif kind == "tool_result" and b.get("content"):
            entry = {"type": "tool_result", "content": _blocks(b["content"])}
            if b.get("tool_use_id"):
                entry["tool_use_id"] = b["tool_use_id"]
            out.append(entry)
    return out


def test_web_and_mcp_list_the_same_codex_sessions(parity: Parity, api: Any) -> None:
    r = api.get("/api/conversations", params={"source": "CODEX"})
    assert r.status_code == 200, r.text
    web = {row["uuid"]: row["name"] for row in r.json()}
    mcp = {s["uuid"]: s["name"] for s in _tool("list_sessions", source="CODEX", limit=100)["sessions"]}

    assert sorted(web) == VISIBLE
    assert mcp == web


def test_web_and_mcp_list_the_same_archived_codex_session(parity: Parity, api: Any) -> None:
    r = api.get("/api/conversations", params={"source": "CODEX", "show_archived": "true"})
    assert r.status_code == 200, r.text
    web = {row["uuid"]: row["name"] for row in r.json()}
    assert web[fake.THREAD_ARCHIVED] == "Gamma release notes"
    out = _tool("get_session_outline", conversation_id=fake.THREAD_ARCHIVED)
    assert out["name"] == web[fake.THREAD_ARCHIVED]


@pytest.mark.parametrize("tid", fake.ALL_THREADS)
def test_web_and_mcp_return_the_same_transcript(parity: Parity, api: Any, tid: str) -> None:
    web = _web_detail(api, tid)["messages"]
    mcp = _tool("get_messages", conversation_id=tid, include_tool_results=True)

    assert web, tid
    assert [m["uuid"] for m in mcp] == [m["uuid"] for m in web]
    assert [m["sender"] for m in mcp] == [m["sender"] for m in web]
    assert [m["content"] for m in mcp] == [_blocks(m["content"]) for m in web]


@pytest.mark.parametrize("tid", fake.ALL_THREADS)
def test_the_mcp_outline_lists_the_web_messages_in_order(parity: Parity, api: Any, tid: str) -> None:
    web = _web_detail(api, tid)
    out = _tool("get_session_outline", conversation_id=tid, limit=500)

    assert out["message_count"] == len(web["messages"])
    assert [m["message_uuid"] for m in out["messages"]] == [m["uuid"] for m in web["messages"]]
    assert [m["sender"] for m in out["messages"]] == [m["sender"] for m in web["messages"]]


@pytest.mark.parametrize("tid", fake.ALL_THREADS)
def test_web_and_mcp_return_the_same_thread_id_codex_name_and_home(parity: Parity, api: Any, tid: str) -> None:
    web = _web_detail(api, tid)
    out = _tool("get_session_outline", conversation_id=tid)

    assert web["uuid"] == tid
    assert out["conversation_id"] == tid
    assert web["source"] == out["source"] == "CODEX"
    assert web["name"] == out["name"]
    assert out["source_name"] == web["source_name"]
    assert web["source_home"] == out["source_home"] == parity.home_display
    assert web["source_home_abs"] == parity.home_abs


def test_the_codex_names_come_from_the_fake_corpus(parity: Parity, api: Any) -> None:
    assert _web_detail(api, fake.THREAD_PAGINATED)["source_name"] == "Alpha parser refactor"
    assert _web_detail(api, fake.THREAD_LEGACY)["source_name"] is None
    sessions = {s["uuid"]: s for s in _tool("list_sessions", source="CODEX", limit=100)["sessions"]}
    assert sessions[fake.THREAD_PAGINATED]["source_name"] == "Alpha parser refactor"
    assert sessions[fake.THREAD_LEGACY]["source_name"] is None
    assert sessions[fake.THREAD_PAGINATED]["source_home"] == parity.home_display


@pytest.mark.parametrize("tid", fake.ALL_THREADS)
def test_web_and_mcp_return_the_same_notices(parity: Parity, api: Any, tid: str) -> None:
    web = _web_detail(api, tid)["notices"]
    out = _tool("get_session_outline", conversation_id=tid)
    entry = {s["uuid"]: s for s in _tool("list_sessions", source="CODEX", limit=100)["sessions"]}.get(tid)

    # MCP leaves out an empty list (an empty field costs the model tokens).
    assert out.get("notices", []) == web
    if entry is not None:
        assert entry.get("notices", []) == web


def test_the_notices_are_not_all_empty(parity: Parity, api: Any) -> None:
    assert "Imported from Claude Code" in _web_detail(api, fake.THREAD_IMPORTED)["notices"]
    assert any("warning" in n for n in _web_detail(api, fake.THREAD_PAGINATED)["notices"])


_HEADER = re.compile(r"^\*\*(Thread ID|Codex name|Codex home):\*\* .*$", re.MULTILINE)


@pytest.mark.parametrize("tid", fake.ALL_THREADS)
def test_web_and_mcp_exports_have_the_same_codex_header(parity: Parity, api: Any, tid: str) -> None:
    r = api.get(f"/api/conversations/{tid}/export/markdown")
    assert r.status_code == 200, r.text
    web = [m.group(0) for m in _HEADER.finditer(r.text)]
    mcp_md = _tool("export_session", conversation_id=tid)
    mcp = [m.group(0) for m in _HEADER.finditer(mcp_md)]

    assert web == mcp
    assert web[0] == f"**Thread ID:** {tid}"
    assert web[2] == f"**Codex home:** {parity.home_display}"


def test_no_mcp_output_holds_the_absolute_codex_home(parity: Parity) -> None:
    outputs: list[Any] = [_tool("list_sessions", source="CODEX", limit=100), _tool("list_projects", source="CODEX")]
    for tid in fake.ALL_THREADS:
        outputs.append(_tool("get_session_outline", conversation_id=tid))
        outputs.append(_tool("get_messages", conversation_id=tid, include_tool_results=True))
        outputs.append(_tool("export_session", conversation_id=tid))
    text = json.dumps(outputs, default=str)

    assert "source_home_abs" not in text
    if parity.home_display.startswith("~"):
        assert parity.home_abs not in text
        assert os.path.realpath(parity.home_abs) not in text


def test_the_readers_write_nothing_into_the_archive_and_create_no_codex_home(parity: Parity, api: Any) -> None:
    before = _tree_hash(parity.archive)

    api.get("/api/conversations", params={"source": "CODEX"})
    _tool("list_sessions", source="CODEX", limit=100)
    for tid in fake.ALL_THREADS:
        _web_detail(api, tid)
        _tool("get_session_outline", conversation_id=tid)
        _tool("export_session", conversation_id=tid)

    assert _tree_hash(parity.archive) == before
    assert not parity.missing_home.exists()
