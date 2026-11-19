"""N1: the source adapter registry (PLANS/2026.11.18-codex-source.md §4.1).

The three current sources move behind ``backend/sources/``. These tests pin
the registry, the descriptors, the adapter methods on the N0 corpus, the one
definition of ``SourceId`` and ``SourceFilter``, and the generated frontend
JSON. The N0 goldens pin that the list, viewer, search, export, and MCP
outputs do not change.

The descriptor values come from today's hard-coded rules:

- ``chronological``: ``store.py`` treats Code and Cowork as chronological.
- ``has_projects``: only Claude Code groups by project (Sidebar.tsx,
  conversationListView.ts); Cowork paths are VM sandbox paths.
- ``has_archive_state``: the Archived toggle applies to Cowork only.
- ``has_organizations``: Desktop conversations and remote Cowork tasks carry
  ``organization_id``; Claude Code sessions have none.
- ``refetchable``: Re-download shows for CLAUDE_AI only.
- ``messages_change_in_place``: no current source; the MCP outline cache
  appends when the count grows.
"""

from __future__ import annotations

import ast
import dataclasses
import fnmatch
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest

pytestmark = pytest.mark.xfail(strict=True, reason="N1 not implemented yet")

REPO = Path(__file__).resolve().parents[2]
GENERATED_JSON = REPO / "frontend" / "src" / "lib" / "sources.generated.json"
GENERATOR = REPO / "scripts" / "generate_sources_json.py"
SOURCE_IDS = ("CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK")

#: The N0 corpus (backend/tests/goldens/corpus.py): source -> its uuids.
CORPUS_UUIDS = {
    "CLAUDE_AI": {
        "0f415a45-9c62-8671-d4ad-53b84acb7e1a",
        "a3c33811-ab27-c195-6938-54121b8673e4",
        "c8da94ab-641d-bf28-d4a6-cba9bc72468e",
    },
    "CLAUDE_CODE": {"435e56ed-50ba-21c0-3495-0e8749b20543"},
    "CLAUDE_COWORK": {
        "aaaa1111-2222-3333-4444-555566667777",
        "session_01FIXTUREREMOTE000000000001",
    },
}

DESCRIPTOR_FIELDS = {
    "id",
    "label",
    "short_label",
    "chronological",
    "has_projects",
    "has_archive_state",
    "has_organizations",
    "refetchable",
    "messages_change_in_place",
}

EXPECTED_DESCRIPTORS = {
    "CLAUDE_AI": {
        "id": "CLAUDE_AI",
        "label": "Claude Desktop",
        "short_label": "Desktop",
        "chronological": False,
        "has_projects": False,
        "has_archive_state": False,
        "has_organizations": True,
        "refetchable": True,
        "messages_change_in_place": False,
    },
    "CLAUDE_CODE": {
        "id": "CLAUDE_CODE",
        "label": "Claude Code",
        "short_label": "Code",
        "chronological": True,
        "has_projects": True,
        "has_archive_state": False,
        "has_organizations": False,
        "refetchable": False,
        "messages_change_in_place": False,
    },
    "CLAUDE_COWORK": {
        "id": "CLAUDE_COWORK",
        "label": "Claude Cowork",
        "short_label": "Cowork",
        "chronological": True,
        "has_projects": False,
        "has_archive_state": True,
        "has_organizations": True,
        "refetchable": False,
        "messages_change_in_place": False,
    },
}

ADAPTER_METHODS = (
    "descriptor",
    "watch_roots",
    "discover",
    "find",
    "read_summary",
    "load",
    "archive_sync",
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    try:
        from backend.sources import registry
    except ModuleNotFoundError:
        # Before N1 the test body reports the missing module itself.
        yield
        return
    registry.reset_for_tests()
    yield
    registry.reset_for_tests()


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    from backend.tests.goldens.corpus import build_corpus

    return build_corpus(tmp_path, monkeypatch)


def _settings():
    from backend.config import get_settings

    return get_settings()


def _real(path) -> str:
    return os.path.realpath(str(path))


def _indexed_paths(corpus) -> dict[str, str]:
    """conv_uuid -> path, as today's index build records it."""
    conn = sqlite3.connect(corpus.root / "index.sqlite")
    try:
        rows = conn.execute("SELECT conv_uuid, path FROM indexed_files").fetchall()
    finally:
        conn.close()
    return {r[0]: _real(r[1]) for r in rows}


class _FakeAdapter:
    def __init__(self, source_id: str = "FAKE_SOURCE"):
        from backend.sources.base import SourceDescriptor

        self._descriptor = SourceDescriptor(
            id=source_id,
            label="Fake Source",
            short_label="Fake",
            chronological=True,
            has_projects=False,
            has_archive_state=False,
            has_organizations=False,
            refetchable=False,
            messages_change_in_place=False,
        )

    def descriptor(self):
        return self._descriptor

    def watch_roots(self, settings):
        return []

    def discover(self):
        return []

    def find(self, uuid):
        return None

    def read_summary(self, path):
        return None

    def load(self, path):
        return None

    def archive_sync(self, ctx):
        return None


@pytest.fixture
def fake_adapter():
    from backend.sources import registry

    adapter = _FakeAdapter()
    registry.register_adapter(adapter)
    return adapter


# --- ids.py: one definition ---------------------------------------------------


def test_source_id_lists_the_three_sources_in_order():
    from backend.sources.ids import SourceId

    assert get_args(SourceId) == SOURCE_IDS


def test_source_filter_is_all_plus_every_source_id():
    from backend.sources.ids import SourceFilter, SourceId

    assert get_args(SourceFilter) == ("all", *get_args(SourceId))


def test_source_id_members_equal_the_registry_keys():
    from backend.sources.ids import SourceId
    from backend.sources.registry import get_adapters

    ids = [a.descriptor().id for a in get_adapters(_settings())]
    assert ids == list(get_args(SourceId))


def _source_literal_sites() -> list[str]:
    """Each place outside ids.py that spells the source set by hand."""
    ids = set(SOURCE_IDS)
    sites = []
    for top in ("backend", "mcp_server", "cli", "fetcher"):
        for path in sorted((REPO / top).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "tests" in path.parts or rel == "backend/sources/ids.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript):
                    name = getattr(node.value, "id", None) or getattr(node.value, "attr", None)
                    if name == "Literal":
                        consts = {
                            c.value for c in ast.walk(node.slice) if isinstance(c, ast.Constant)
                        }
                        if consts & ids:
                            sites.append(f"{rel}:{node.lineno} Literal")
                elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                    consts = {e.value for e in node.elts if isinstance(e, ast.Constant)}
                    if ids <= consts:
                        sites.append(f"{rel}:{node.lineno} collection of all source ids")
    return sites


def test_no_module_outside_ids_spells_the_source_set():
    from backend.sources import ids  # noqa: F401  (the one definition exists)

    sites = _source_literal_sites()
    # Literal(...) subscripts also count as tuples; one line per site is enough.
    assert sorted(set(sites)) == []


def test_models_source_field_uses_source_id():
    from backend.models import (
        ConversationDetail,
        ConversationListItem,
        ConversationSummary,
    )
    from backend.sources.ids import SourceId

    for model in (ConversationListItem, ConversationSummary, ConversationDetail):
        if "source" not in model.model_fields:
            continue
        assert get_args(model.model_fields["source"].annotation) == get_args(SourceId), model


@pytest.mark.parametrize("route", ["/api/conversations", "/api/search"])
def test_api_source_query_accepts_exactly_the_source_filter(route):
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.sources.ids import SourceFilter

    spec = TestClient(app).get("/openapi.json").json()
    params = spec["paths"][route]["get"]["parameters"]
    (source,) = [p for p in params if p["name"] == "source"]
    assert source["schema"]["enum"] == list(get_args(SourceFilter))


# --- base.py and registry.py --------------------------------------------------


def test_source_descriptor_is_a_dataclass_with_the_plan_fields():
    from backend.sources.base import SourceDescriptor

    assert dataclasses.is_dataclass(SourceDescriptor)
    assert {f.name for f in dataclasses.fields(SourceDescriptor)} == DESCRIPTOR_FIELDS


def test_source_adapter_protocol_names_every_method():
    from backend.sources.base import SourceAdapter

    for name in ADAPTER_METHODS:
        assert callable(getattr(SourceAdapter, name, None)), name


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_get_adapter_returns_the_adapter_for_that_id(source_id):
    from backend.sources.registry import get_adapter

    assert get_adapter(source_id).descriptor().id == source_id


@pytest.mark.parametrize(
    ("source_id", "module"),
    [
        ("CLAUDE_AI", "backend.sources.claude_ai"),
        ("CLAUDE_CODE", "backend.sources.claude_code"),
        ("CLAUDE_COWORK", "backend.sources.cowork"),
    ],
)
def test_each_adapter_lives_in_its_own_module(source_id, module):
    from backend.sources.registry import get_adapter

    assert type(get_adapter(source_id)).__module__ == module


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_each_adapter_has_every_protocol_method(source_id):
    from backend.sources.registry import get_adapter

    adapter = get_adapter(source_id)
    for name in ADAPTER_METHODS:
        assert callable(getattr(adapter, name, None)), (source_id, name)


def test_get_adapter_rejects_an_unknown_id():
    from backend.sources.registry import get_adapter

    with pytest.raises(LookupError):
        get_adapter("NOT_A_SOURCE")


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_descriptor_values_match_todays_rules(source_id):
    from backend.sources.registry import get_adapter

    desc = get_adapter(source_id).descriptor()
    assert dataclasses.asdict(desc) == EXPECTED_DESCRIPTORS[source_id]


def test_a_registered_fake_adapter_is_returned(fake_adapter):
    from backend.sources.registry import get_adapter, get_adapters

    assert get_adapter("FAKE_SOURCE") is fake_adapter
    assert fake_adapter in get_adapters(_settings())


def test_reset_for_tests_removes_a_registered_fake_adapter(fake_adapter):
    from backend.sources import registry

    registry.reset_for_tests()
    with pytest.raises(LookupError):
        registry.get_adapter("FAKE_SOURCE")
    assert [a.descriptor().id for a in registry.get_adapters(_settings())] == list(SOURCE_IDS)


def test_adapters_follow_a_settings_change(tmp_path, monkeypatch):
    """/api/config clears the settings cache; a stale adapter would read the
    old folder."""
    from backend import config
    from backend.sources.registry import get_adapter

    found = {}
    for name in ("a", "b"):
        session = tmp_path / name / "projects" / "-p" / f"{name * 8}-0000-0000-0000-000000000000.jsonl"
        session.parent.mkdir(parents=True)
        shutil.copy(
            REPO / "backend/tests/fixtures/claude/projects/-fixture-project"
            / "435e56ed-50ba-21c0-3495-0e8749b20543.jsonl",
            session,
        )
        monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / name))
        config.get_settings.cache_clear()
        found[name] = {_real(p) for p in get_adapter("CLAUDE_CODE").discover()}
        assert found[name] == {_real(session)}, name
    config.get_settings.cache_clear()


# --- watch roots ---------------------------------------------------------------


def test_claude_ai_has_no_watch_root(corpus):
    from backend.sources.registry import get_adapter

    assert list(get_adapter("CLAUDE_AI").watch_roots(_settings())) == []


@pytest.mark.parametrize(
    ("source_id", "root_fn", "matches", "ignores"),
    [
        ("CLAUDE_CODE", "_live_projects_root", ["abc.jsonl"], ["abc.log", "abc.tmp"]),
        ("CLAUDE_COWORK", "_live_cowork_root", ["audit.jsonl"], ["notes.jsonl", "audit.log"]),
    ],
)
def test_watch_root_matches_todays_watcher(corpus, source_id, root_fn, matches, ignores):
    from backend import cc_watcher
    from backend.sources.registry import get_adapter

    (root,) = list(get_adapter(source_id).watch_roots(_settings()))
    assert _real(root.path) == _real(getattr(cc_watcher, root_fn)())
    assert root.create is True
    for name in matches:
        assert any(fnmatch.fnmatch(name, pat) for pat in root.patterns), name
    for name in ignores:
        assert not any(fnmatch.fnmatch(name, pat) for pat in root.patterns), name


# --- adapter methods on the N0 corpus ----------------------------------------


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_discover_yields_the_files_that_the_index_records(corpus, source_id):
    from backend.sources.registry import get_adapter

    indexed = _indexed_paths(corpus)
    expected = {indexed[u] for u in CORPUS_UUIDS[source_id]}
    assert {_real(p) for p in get_adapter(source_id).discover()} == expected


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_find_returns_the_file_of_each_uuid(corpus, source_id):
    from backend.sources.registry import get_adapter

    indexed = _indexed_paths(corpus)
    adapter = get_adapter(source_id)
    for uuid in CORPUS_UUIDS[source_id]:
        path = adapter.find(uuid)
        assert path is not None, uuid
        assert _real(path) == indexed[uuid]


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_find_returns_none_for_a_uuid_of_another_source(corpus, source_id):
    from backend.sources.registry import get_adapter

    adapter = get_adapter(source_id)
    others = set().union(*(u for s, u in CORPUS_UUIDS.items() if s != source_id))
    for uuid in [*sorted(others), "00000000-0000-0000-0000-000000000000"]:
        assert adapter.find(uuid) is None, uuid


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_read_summary_gives_the_list_row_uuid_and_name(corpus, source_id):
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.sources.registry import get_adapter

    rows = TestClient(app).get("/api/conversations", params={"show_archived": "true"}).json()
    names = {r["uuid"]: r["name"] for r in rows}
    adapter = get_adapter(source_id)
    for uuid in CORPUS_UUIDS[source_id]:
        summary = adapter.read_summary(adapter.find(uuid))
        assert summary is not None, uuid
        assert summary["uuid"] == uuid
        assert summary["name"] == names[uuid]


def test_read_summary_is_none_for_an_empty_session_file(corpus):
    """D2: a 0-byte file has no turns."""
    from backend.sources.registry import get_adapter

    empty = corpus.claude_dir / "projects" / "-fixture-project" / "11111111-2222-3333-4444-555555555555.jsonl"
    empty.write_bytes(b"")
    assert get_adapter("CLAUDE_CODE").read_summary(empty) is None


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_load_gives_the_raw_dict_with_messages(corpus, source_id):
    from backend.sources.registry import get_adapter

    adapter = get_adapter(source_id)
    for uuid in CORPUS_UUIDS[source_id]:
        data = adapter.load(adapter.find(uuid))
        assert data is not None, uuid
        assert data["uuid"] == uuid
        assert isinstance(data["chat_messages"], list)
        assert data["chat_messages"], uuid


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_archive_sync_is_none_for_the_current_sources(corpus, source_id):
    from backend.archive_sync import SyncContext
    from backend.sources.registry import get_adapter

    ctx = SyncContext(settings=_settings(), reason="watcher", lock_timeout_s=0.0)
    assert get_adapter(source_id).archive_sync(ctx) is None


# --- the generated frontend JSON ---------------------------------------------


def test_generated_json_lists_every_descriptor_in_registry_order():
    from backend.sources.registry import get_adapters

    expected = [dataclasses.asdict(a.descriptor()) for a in get_adapters(_settings())]
    assert json.loads(GENERATED_JSON.read_text(encoding="utf-8")) == expected


def test_generator_reproduces_the_checked_in_json(tmp_path):
    """The generator runs from a copy of the repo layout, so the test never
    rewrites the real file."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "frontend" / "src" / "lib").mkdir(parents=True)
    shutil.copy(GENERATOR, tmp_path / "scripts" / GENERATOR.name)
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / GENERATOR.name)],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "frontend" / "src" / "lib" / GENERATED_JSON.name
    assert out.read_bytes() == GENERATED_JSON.read_bytes()


def _check_module():
    spec = importlib.util.spec_from_file_location("_check_for_n1", REPO / "scripts" / "check.py")
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve string annotations through sys.modules.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


def test_check_py_python_section_fails_on_a_stale_generated_json():
    steps = _check_module().default_sections()["python"]
    argvs = [step.argv for step in steps]
    runs_generator = [a for a in argvs if any(str(x).endswith("generate_sources_json.py") for x in a)]
    assert runs_generator, "the python section must run scripts/generate_sources_json.py"
    has_own_check = any("--check" in a for a in runs_generator)
    diff_step = any(
        "diff" in a and "--exit-code" in a and any("sources.generated.json" in str(x) for x in a)
        for a in argvs
    )
    assert has_own_check or diff_step, "a stale sources.generated.json must fail the python section"


def test_tsconfig_app_resolves_json_modules():
    text = (REPO / "frontend" / "tsconfig.app.json").read_text(encoding="utf-8")
    assert re.search(r'"resolveJsonModule"\s*:\s*true', text)
