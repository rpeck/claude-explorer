"""N1: the source adapter registry (PLANS/2026.11.18-codex-source.md §4.1).

The three current sources move behind ``backend/sources/``. The contract is
the council decision in
``PLANS/reviews/2026.11.18-codex-source/council_n1_adapter.md`` (Q1 and Q2,
"Rules N1 must implement" and "API names for the tester"). These tests pin
the registry, the descriptors, the adapter methods on the N0 corpus, the one
definition of ``SourceId`` and ``SourceFilter``, the dispatch rule, and the
generated frontend JSON. The N0 goldens pin that the list, viewer, search,
export, and MCP outputs do not change.

The oracles for ``list_rows`` and ``load_for_index`` are the reader functions
that N1 does not own (``claude_code_reader``, ``cowork_reader``) and the store
seams that the council keeps (``_get_conversation_files``,
``_load_conversation``, ``_cowork_summary``), so a test never compares the
new code with itself only.

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

REPO = Path(__file__).resolve().parents[2]
GENERATED_JSON = REPO / "frontend" / "src" / "lib" / "sources.generated.json"
GENERATOR = REPO / "scripts" / "generate_sources_json.py"
#: The three sources that N1 ported; the parametrized tests pin their behavior.
SOURCE_IDS = ("CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK")
#: Every built-in source, in registry order (N8 adds Codex).
REGISTRY_IDS = (*SOURCE_IDS, "CODEX")

#: The N0 corpus (backend/tests/goldens/corpus.py): source -> its uuids.
CORPUS_UUIDS = {
    "CLAUDE_AI": {
        "0f415a45-9c62-8671-d4ad-53b84acb7e1a",
        "a3c33811-ab27-c195-6938-54121b8673e4",
        "c8da94ab-641d-bf28-d4a6-cba9bc72468e",
    },
    "CLAUDE_CODE": {
        "435e56ed-50ba-21c0-3495-0e8749b20543",
        # Issue #90: the real Code Mode recordings (scripts/generate_e2e_fixtures.py).
        "2339aab4-744c-4556-95b8-5ee5552c1b85",
        "b007d327-80c4-46ea-b737-53d84b30c624",
        # Issue #96: the synthetic session with subagents.
        "5a6e0000-0000-4000-8000-000000000096",
    },
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
    "assistant_label",
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
        "assistant_label": "Claude",
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
        "assistant_label": "Claude",
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
        "assistant_label": "Claude",
    },
}

ADAPTER_CLASSES = {
    "CLAUDE_AI": ("backend.sources.claude_ai", "ClaudeAiAdapter"),
    "CLAUDE_CODE": ("backend.sources.claude_code", "ClaudeCodeAdapter"),
    "CLAUDE_COWORK": ("backend.sources.cowork", "CoworkAdapter"),
}

ADAPTER_METHODS = (
    "descriptor",
    "watch_roots",
    "discover",
    "list_rows",
    "find",
    "load_for_index",
    "archive_sync",
    "branch_flag",
)

MISSING_UUID = "00000000-0000-0000-0000-000000000000"

#: Messages with two children of one parent: today's has_branches() is True.
BRANCHED = [
    {"uuid": "m1", "parent_message_uuid": None},
    {"uuid": "m2", "parent_message_uuid": "m1"},
    {"uuid": "m3", "parent_message_uuid": "m1"},
]
#: A linear chain: has_branches() is False.
LINEAR = [
    {"uuid": "m1", "parent_message_uuid": None},
    {"uuid": "m2", "parent_message_uuid": "m1"},
]
#: Messages with no parent, as in Cowork: has_branches() reads 2 or more as True.
NO_PARENTS_TWO = [{"uuid": "m1"}, {"uuid": "m2"}]
NO_PARENTS_ONE = [{"uuid": "m1"}]


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


@pytest.fixture
def store(corpus):
    """A store bound to the corpus through the settings, as production builds it."""
    from backend.store import ConversationStore

    return ConversationStore()


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
    def __init__(self, store, descriptor):
        self.store = store
        self._descriptor = descriptor

    def descriptor(self):
        return self._descriptor

    def watch_roots(self):
        return []

    def discover(self):
        return []

    def list_rows(self, *, full_content, include_phantom):
        return []

    def find(self, uuid):
        return None

    def load_for_index(self, path):
        return None

    def archive_sync(self, ctx):
        return None

    def branch_flag(self, data, chat_messages):
        return False


def _fake_descriptor():
    from backend.sources.base import SourceDescriptor

    return SourceDescriptor(
        id="FAKE_SOURCE",
        label="Fake Source",
        short_label="Fake",
        chronological=True,
        has_projects=False,
        has_archive_state=False,
        has_organizations=False,
        refetchable=False,
        messages_change_in_place=False,
    )


@pytest.fixture
def fake_registered():
    """Registers a fake adapter; the teardown restores the built-ins."""
    from backend.sources import registry

    descriptor = _fake_descriptor()
    calls = []

    def factory(store):
        calls.append(store)
        return _FakeAdapter(store, descriptor)

    registry.register_adapter(descriptor, factory)
    yield descriptor, calls
    registry.reset_for_tests()


# --- ids.py: one definition ---------------------------------------------------


def test_source_id_lists_every_source_in_order():
    from backend.sources.ids import SourceId

    assert get_args(SourceId) == REGISTRY_IDS


def test_source_filter_is_all_plus_every_source_id():
    from backend.sources.ids import SourceFilter, SourceId

    assert get_args(SourceFilter) == ("all", *get_args(SourceId))


def test_source_id_members_equal_the_descriptors_after_reset():
    from backend.sources import registry
    from backend.sources.ids import SourceId

    registry.reset_for_tests()
    descriptors = registry.get_descriptors()
    assert isinstance(descriptors, tuple)
    assert [d.id for d in descriptors] == list(get_args(SourceId))


def _parse(rel: str) -> ast.Module:
    return ast.parse((REPO / rel).read_text(encoding="utf-8"))


def _source_literal_sites() -> list[str]:
    """Each place outside ids.py that spells the source set by hand."""
    ids = set(REGISTRY_IDS)
    sites = []
    for top in ("backend", "mcp_server", "cli", "fetcher"):
        for path in sorted((REPO / top).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "tests" in path.parts or rel == "backend/sources/ids.py":
                continue
            for node in ast.walk(_parse(rel)):
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

    # A Literal subscript also counts as a tuple; one line per site is enough.
    assert sorted(set(_source_literal_sites())) == []


#: Q2 rule 7 scope: store bridge methods and backend/sources/ are allowed.
DISPATCH_SCOPE = (
    "backend/search.py",
    "backend/search_index.py",
    "backend/cc_watcher.py",
    "backend/routers",
    "mcp_server",
)
#: Functions that the council or N1 leaves as they are:
#: - _file_path_for_conv: no production caller (council "Risks recorded").
#: - _cowork_sidecar_mtime: the L1 sidecar drift check; the contract has no
#:   adapter method for it (reported to the orchestrator 2026-11-19).
DISPATCH_EXEMPT = {
    ("backend/search_index.py", "_file_path_for_conv"),
    ("backend/search_index.py", "_cowork_sidecar_mtime"),
}


def _dispatch_sites() -> list[str]:
    ids = set(REGISTRY_IDS)
    files = []
    for entry in DISPATCH_SCOPE:
        path = REPO / entry
        found = [path] if path.is_file() else sorted(path.rglob("*.py"))
        files += [p for p in found if "tests" not in p.parts]
    sites = []
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        tree = _parse(rel)
        owner: dict[int, str] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    owner.setdefault(id(node), fn.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            consts = {
                c.value
                for part in (node.left, *node.comparators)
                for c in ast.walk(part)
                if isinstance(c, ast.Constant) and c.value in ids
            }
            fn_name = owner.get(id(node))
            if consts and (rel, fn_name) not in DISPATCH_EXEMPT:
                sites.append(f"{rel}:{node.lineno} in {fn_name}: {sorted(consts)}")
    return sites


def test_no_code_outside_the_adapters_dispatches_on_a_source_id():
    """Q2 rule 7: dispatch goes through the adapters and the descriptors."""
    from backend.sources import registry  # noqa: F401

    assert _dispatch_sites() == []


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


def test_source_descriptor_is_a_frozen_dataclass_with_the_plan_fields():
    from backend.sources.base import SourceDescriptor

    assert dataclasses.is_dataclass(SourceDescriptor)
    assert {f.name for f in dataclasses.fields(SourceDescriptor)} == DESCRIPTOR_FIELDS
    with pytest.raises(dataclasses.FrozenInstanceError):
        _fake_descriptor().label = "changed"


def test_source_adapter_protocol_names_every_method():
    from backend.sources.base import SourceAdapter

    for name in ADAPTER_METHODS:
        assert callable(getattr(SourceAdapter, name, None)), name


def test_source_adapter_protocol_has_no_read_summary():
    """Q1 rule 2: list_rows replaces the per-path summary."""
    from backend.sources.base import SourceAdapter

    assert not hasattr(SourceAdapter, "read_summary")


def test_get_adapters_returns_a_tuple_in_registry_order(store):
    from backend.sources.registry import get_adapters

    adapters = get_adapters(store)
    assert isinstance(adapters, tuple)
    assert [a.descriptor().id for a in adapters] == list(REGISTRY_IDS)


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_get_adapter_returns_the_adapter_for_that_id(store, source_id):
    from backend.sources.registry import get_adapter

    assert get_adapter(source_id, store).descriptor().id == source_id


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_each_adapter_is_its_named_class_in_its_own_module(store, source_id):
    from backend.sources.registry import get_adapter

    cls = type(get_adapter(source_id, store))
    assert (cls.__module__, cls.__name__) == ADAPTER_CLASSES[source_id]


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_each_adapter_has_every_protocol_method(store, source_id):
    from backend.sources.registry import get_adapter

    adapter = get_adapter(source_id, store)
    for name in ADAPTER_METHODS:
        assert callable(getattr(adapter, name, None)), (source_id, name)


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_get_descriptor_equals_the_adapter_descriptor(store, source_id):
    from backend.sources.registry import get_adapter, get_descriptor

    assert get_descriptor(source_id) == get_adapter(source_id, store).descriptor()


def test_get_adapter_raises_key_error_for_an_unknown_id(store):
    from backend.sources.registry import get_adapter

    with pytest.raises(KeyError):
        get_adapter("NOT_A_SOURCE", store)


def test_unknown_id_gives_none_from_the_lenient_lookups(store):
    """Q2 rule 6: stored data with an unknown source keeps today's fallback."""
    from backend.sources.registry import get_adapter_or_none, get_descriptor

    assert get_adapter_or_none("NOT_A_SOURCE", store) is None
    assert get_descriptor("NOT_A_SOURCE") is None


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_get_adapter_or_none_finds_a_known_id(store, source_id):
    from backend.sources.registry import get_adapter_or_none

    adapter = get_adapter_or_none(source_id, store)
    assert adapter is not None
    assert adapter.descriptor().id == source_id


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_descriptor_values_match_todays_rules(source_id):
    from backend.sources.registry import get_descriptor

    assert dataclasses.asdict(get_descriptor(source_id)) == EXPECTED_DESCRIPTORS[source_id]


def test_a_registered_factory_gets_the_store(store, fake_registered):
    from backend.sources.registry import get_adapter

    descriptor, calls = fake_registered
    adapter = get_adapter("FAKE_SOURCE", store)
    assert adapter.descriptor() == descriptor
    assert adapter.store is store
    assert calls and calls[-1] is store


def test_a_registered_adapter_comes_after_the_built_ins(store, fake_registered):
    from backend.sources.registry import get_adapters, get_descriptor, get_descriptors

    descriptor, _ = fake_registered
    assert [d.id for d in get_descriptors()] == [*REGISTRY_IDS, "FAKE_SOURCE"]
    assert [a.descriptor().id for a in get_adapters(store)] == [*REGISTRY_IDS, "FAKE_SOURCE"]
    assert get_descriptor("FAKE_SOURCE") == descriptor


def test_reset_for_tests_restores_exactly_the_built_ins(store, fake_registered):
    from backend.sources import registry

    registry.reset_for_tests()
    assert [d.id for d in registry.get_descriptors()] == list(REGISTRY_IDS)
    assert registry.get_adapter_or_none("FAKE_SOURCE", store) is None
    with pytest.raises(KeyError):
        registry.get_adapter("FAKE_SOURCE", store)


# --- roots come from the store -------------------------------------------------


@pytest.fixture
def other_store(corpus, tmp_path):
    """A store with explicit roots that differ from the settings."""
    from backend.store import ConversationStore

    other = tmp_path / "other"
    shutil.copytree(corpus.root, other)
    store = ConversationStore(
        data_dir=other / "data",
        claude_dir=other / "claude",
        cowork_root=other / "desktop-app" / "local-agent-mode-sessions",
    )
    return store, other


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_discover_reads_the_roots_of_the_store_not_the_settings(other_store, source_id):
    from backend.sources.registry import get_adapter

    store, other = other_store
    paths = [_real(p) for p in get_adapter(source_id, store).discover()]
    assert len(paths) == len(CORPUS_UUIDS[source_id])
    for path in paths:
        assert path.startswith(_real(other) + os.sep), path


def test_watch_roots_are_the_roots_of_the_store(other_store):
    from backend.sources.registry import get_adapter

    store, _ = other_store
    (cc,) = list(get_adapter("CLAUDE_CODE", store).watch_roots())
    (cowork,) = list(get_adapter("CLAUDE_COWORK", store).watch_roots())
    assert _real(cc.path) == _real(store.claude_dir / "projects")
    assert _real(cowork.path) == _real(store.cowork_root)


def test_find_reads_the_roots_of_the_store_not_the_settings(other_store):
    from backend.sources.registry import get_adapter

    store, other = other_store
    hit = get_adapter("CLAUDE_CODE", store).find("435e56ed-50ba-21c0-3495-0e8749b20543")
    assert hit is not None
    assert _real(hit[1]).startswith(_real(other) + os.sep)


# --- watch roots -----------------------------------------------------------------


def test_claude_ai_has_no_watch_root(store):
    from backend.sources.registry import get_adapter

    assert list(get_adapter("CLAUDE_AI", store).watch_roots()) == []


@pytest.mark.parametrize(
    ("source_id", "root_fn", "matches", "ignores"),
    [
        ("CLAUDE_CODE", "_live_projects_root", ["abc.jsonl"], ["abc.log", "abc.tmp"]),
        ("CLAUDE_COWORK", "_live_cowork_root", ["audit.jsonl"], ["notes.jsonl", "audit.log"]),
    ],
)
def test_watch_root_matches_todays_watcher(store, source_id, root_fn, matches, ignores):
    from backend import cc_watcher
    from backend.sources.registry import get_adapter

    (root,) = list(get_adapter(source_id, store).watch_roots())
    assert _real(root.path) == _real(getattr(cc_watcher, root_fn)())
    assert root.create is True
    for name in matches:
        assert any(fnmatch.fnmatch(name, pat) for pat in root.patterns), name
    for name in ignores:
        assert not any(fnmatch.fnmatch(name, pat) for pat in root.patterns), name


# --- discover and load_for_index -------------------------------------------------


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_discover_yields_the_files_that_the_index_records(corpus, store, source_id):
    from backend.sources.registry import get_adapter

    indexed = _indexed_paths(corpus)
    expected = {indexed[u] for u in CORPUS_UUIDS[source_id]}
    assert {_real(p) for p in get_adapter(source_id, store).discover()} == expected


def _reader_load(source_id: str, store, path: Path):
    """Today's _load_conversation_at, per source."""
    if source_id == "CLAUDE_CODE":
        from backend.claude_code_reader import read_claude_code_conversation

        return read_claude_code_conversation(path)
    if source_id == "CLAUDE_COWORK":
        from backend.cowork_reader import read_cowork_conversation

        return read_cowork_conversation(path.parent)
    return store._load_conversation(path)


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_load_for_index_equals_todays_index_reader(store, source_id):
    from backend.sources.registry import get_adapter

    adapter = get_adapter(source_id, store)
    paths = list(adapter.discover())
    assert paths
    for path in paths:
        data = adapter.load_for_index(Path(path))
        assert data is not None, path
        assert data["uuid"] in CORPUS_UUIDS[source_id]
        assert data["chat_messages"], path
        assert data == _reader_load(source_id, store, Path(path)), path


@pytest.mark.parametrize("source_id", ["CLAUDE_CODE", "CLAUDE_COWORK"])
def test_load_for_index_does_not_fill_the_file_cache(store, source_id):
    """Q1 rule 6: CC and Cowork read uncached, as the index does today."""
    from backend.cache import clear_cache, get_conversation_cache
    from backend.sources.registry import get_adapter

    adapter = get_adapter(source_id, store)
    clear_cache()
    for path in adapter.discover():
        adapter.load_for_index(Path(path))
    assert get_conversation_cache().stats["entries"] == 0


# --- find --------------------------------------------------------------------------


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_find_returns_the_data_and_the_file_of_each_uuid(corpus, store, source_id):
    from backend.sources.registry import get_adapter

    indexed = _indexed_paths(corpus)
    adapter = get_adapter(source_id, store)
    for uuid in CORPUS_UUIDS[source_id]:
        hit = adapter.find(uuid)
        assert hit is not None, uuid
        data, path = hit
        assert data["uuid"] == uuid
        assert data["chat_messages"], uuid
        assert _real(path) == indexed[uuid]


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_find_returns_none_for_a_uuid_of_another_source(store, source_id):
    from backend.sources.registry import get_adapter

    adapter = get_adapter(source_id, store)
    others = set().union(*(u for s, u in CORPUS_UUIDS.items() if s != source_id))
    for uuid in [*sorted(others), MISSING_UUID]:
        assert adapter.find(uuid) is None, uuid


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_store_find_returns_the_adapter_hit(store, source_id):
    """Q1 rule 5: the store asks the adapters and keeps its signature."""
    from backend.sources.registry import get_adapter

    for uuid in CORPUS_UUIDS[source_id]:
        data, path = store._find_conversation_data(uuid)
        a_data, a_path = get_adapter(source_id, store).find(uuid)
        assert data == a_data
        assert _real(path) == _real(a_path)


def test_store_find_miss_is_still_none_none(store):
    from backend.sources import registry  # noqa: F401

    assert store._find_conversation_data(MISSING_UUID) == (None, None)


# --- list_rows ---------------------------------------------------------------------


def _today_rows(source_id: str, store, full_content: bool, include_phantom: bool):
    """Today's per-source branch of _get_all_conversations_data (store.py:606-636),
    built from functions and seams that N1 keeps."""
    from backend import store as store_mod

    if source_id == "CLAUDE_AI":
        rows = (store._load_conversation(p) for p in store._get_conversation_files())
        return [
            d for d in rows if d and d.get("source") not in ("CLAUDE_CODE", "CLAUDE_COWORK")
        ]
    if source_id == "CLAUDE_CODE":
        from backend.claude_code_reader import list_claude_code_conversations

        return list_claude_code_conversations(
            store.claude_dir, full_content=full_content, include_phantom=include_phantom
        )
    from backend.cowork_reader import list_cowork_conversations

    full = list_cowork_conversations(store.cowork_root, store.remote_cowork_root)
    return full if full_content else [store_mod._cowork_summary(d) for d in full]


@pytest.mark.parametrize("full_content", [False, True], ids=["summary", "full"])
@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_list_rows_equals_todays_branch(store, source_id, full_content):
    from backend.sources.registry import get_adapter

    rows = get_adapter(source_id, store).list_rows(full_content=full_content, include_phantom=False)
    assert isinstance(rows, list)
    assert sorted(r["uuid"] for r in rows) == sorted(CORPUS_UUIDS[source_id])
    assert rows == _today_rows(source_id, store, full_content, False)


@pytest.mark.parametrize("full_content", [False, True], ids=["summary", "full"])
@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_store_list_data_for_one_source_is_that_adapters_rows(store, source_id, full_content):
    """Q1 rule 4: the store loops over the adapters."""
    from backend.sources.registry import get_adapter

    rows = get_adapter(source_id, store).list_rows(full_content=full_content, include_phantom=False)
    assert store._get_all_conversations_data(source=source_id, full_content=full_content) == rows


def test_store_list_data_for_all_is_the_rows_in_registry_order(store):
    from backend.sources.registry import get_adapters

    expected = [
        row
        for adapter in get_adapters(store)
        for row in adapter.list_rows(full_content=False, include_phantom=False)
    ]
    assert store._get_all_conversations_data(source="all") == expected


@pytest.mark.parametrize("source_id", ["CLAUDE_AI", "CLAUDE_COWORK"])
def test_a_source_without_phantoms_ignores_include_phantom(store, source_id):
    from backend.sources.registry import get_adapter

    adapter = get_adapter(source_id, store)
    assert adapter.list_rows(full_content=False, include_phantom=True) == adapter.list_rows(
        full_content=False, include_phantom=False
    )


@pytest.mark.parametrize("include_phantom", [False, True])
def test_claude_code_list_rows_passes_include_phantom(store, include_phantom):
    from backend.sources.registry import get_adapter

    rows = get_adapter("CLAUDE_CODE", store).list_rows(
        full_content=False, include_phantom=include_phantom
    )
    assert rows == _today_rows("CLAUDE_CODE", store, False, include_phantom)


def test_an_empty_session_file_is_not_a_list_row(corpus, store):
    """D2: a 0-byte file has no turns."""
    from backend.sources.registry import get_adapter

    empty_uuid = "11111111-2222-3333-4444-555555555555"
    (corpus.claude_dir / "projects" / "-fixture-project" / f"{empty_uuid}.jsonl").write_bytes(b"")
    adapter = get_adapter("CLAUDE_CODE", store)
    for full_content in (False, True):
        for include_phantom in (False, True):
            rows = adapter.list_rows(full_content=full_content, include_phantom=include_phantom)
            assert empty_uuid not in {r["uuid"] for r in rows}, (full_content, include_phantom)


# --- branch_flag -------------------------------------------------------------------


@pytest.mark.parametrize(
    "messages", [BRANCHED, LINEAR, NO_PARENTS_TWO, NO_PARENTS_ONE, []],
    ids=["branched", "linear", "no-parents-2", "no-parents-1", "empty"],
)
@pytest.mark.parametrize("source_id", ["CLAUDE_AI"])
def test_branch_flag_is_todays_computed_value(store, source_id, messages):
    from backend.sources.registry import get_adapter
    from backend.store import has_branches

    data = {"uuid": "x", "source": source_id, "chat_messages": messages}
    assert get_adapter(source_id, store).branch_flag(data, messages) is has_branches(messages)


@pytest.mark.parametrize("messages", [BRANCHED, LINEAR, NO_PARENTS_TWO], ids=["branched", "linear", "no-parents-2"])
def test_claude_code_branch_flag_is_always_false(store, messages):
    from backend.sources.registry import get_adapter

    data = {"uuid": "x", "source": "CLAUDE_CODE", "chat_messages": messages}
    assert get_adapter("CLAUDE_CODE", store).branch_flag(data, messages) is False


# --- archive_sync ------------------------------------------------------------------


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_archive_sync_is_none_for_the_current_sources(store, source_id):
    from backend.archive_sync import SyncContext
    from backend.sources.registry import get_adapter

    ctx = SyncContext(settings=_settings(), reason="watcher", lock_timeout_s=0.0)
    assert get_adapter(source_id, store).archive_sync(ctx) is None


# --- the generated frontend JSON ---------------------------------------------


def test_generated_json_lists_every_descriptor_in_registry_order():
    from backend.sources import registry

    registry.reset_for_tests()
    expected = [dataclasses.asdict(d) for d in registry.get_descriptors()]
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


def test_generator_never_builds_a_store():
    """Q2 rule 8: it reads the built-in descriptors only."""
    assert "ConversationStore" not in GENERATOR.read_text(encoding="utf-8")


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
