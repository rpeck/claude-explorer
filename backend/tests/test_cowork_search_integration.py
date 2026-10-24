"""FTS5 search-index integration tests for the CLAUDE_COWORK source.

Pins the behavioral contract that:
  * Cowork ``audit.jsonl`` files are enumerated alongside Desktop +
    CC paths;
  * dispatch routes Cowork to ``read_cowork_conversation``, NOT to
    ``read_claude_code_conversation`` (both share the ``.jsonl``
    extension — extension-based dispatch silently corrupts Cowork
    parsing because the CC reader doesn't know about the
    ``_audit_timestamp`` field rename);
  * a known token in a Cowork user message is searchable via the
    FTS5 fast path and returns the correct conversation uuid.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.search_index import (
    SearchIndex,
    build_full_index,
)
from backend.store import ConversationStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cowork"
HAPPY_DEPLOYMENT = FIXTURE_ROOT / "d_deployment1"
HAPPY_ORG = HAPPY_DEPLOYMENT / "o_org1"
HAPPY_SESSION_DIR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777"
HAPPY_SIDECAR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777.json"


def _make_isolated_cowork_root(tmp_path: Path) -> Path:
    """Mirror the happy fixture under tmp_path so monkeypatch doesn't
    interfere with the autouse isolation fixture."""
    cowork_root = tmp_path / "claude_desktop_app" / "local-agent-mode-sessions"
    dep = cowork_root / "d_test"
    org = dep / "o_test"
    sess = org / "local_aaaa1111-2222-3333-4444-555566667777"
    sess.mkdir(parents=True)
    shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", sess / "audit.jsonl")
    shutil.copy(HAPPY_SIDECAR, org / "local_aaaa1111-2222-3333-4444-555566667777.json")
    return cowork_root


@pytest.fixture
def index_with_cowork(tmp_path: Path) -> tuple[SearchIndex, ConversationStore]:
    """Construct a fresh in-memory SearchIndex over a Cowork-only corpus."""
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    data_dir.mkdir()
    claude_dir.mkdir()
    cowork_root = _make_isolated_cowork_root(tmp_path)

    store = ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )

    db_path = tmp_path / "search-index.sqlite"
    index = SearchIndex(db_path)
    # _init_schema runs from __init__; nothing else to do.

    build_full_index(store, index=index)
    return index, store


def test_cowork_audit_jsonl_indexed(index_with_cowork):
    """The Cowork fixture's audit.jsonl is enumerated + indexed (the
    sibling CC ``.jsonl`` shares this extension, so this test would
    pass trivially if dispatch was extension-based — but the FTS5
    contents would be empty / wrong-shape. The token assertion below
    is the real load-bearing check)."""
    index, _ = index_with_cowork
    # File row exists for the audit.jsonl path.
    indexed = index._read_indexed_files_map()
    audit_paths = [p for p in indexed.keys() if p.endswith("audit.jsonl")]
    assert len(audit_paths) == 1


def test_cowork_token_searchable_via_fts5(index_with_cowork):
    """The unique token COWORK_FIXTURE_HELLO_XYZ embedded in the
    fixture user message is searchable + returns the Cowork uuid.
    This fails under extension-based dispatch (CC reader returns None
    for the audit.jsonl shape, conv never indexed)."""
    index, _ = index_with_cowork
    rows = index.query("COWORK_FIXTURE_HELLO_XYZ", limit=10)
    uuids = sorted({row["conv_uuid"] for row in rows})
    assert uuids == ["aaaa1111-2222-3333-4444-555566667777"]


def test_cowork_dispatch_does_not_invoke_cc_reader(
    index_with_cowork, monkeypatch
):
    """The CC reader (``read_claude_code_conversation``) must NOT be
    called for a Cowork audit.jsonl path. Extension-based dispatch
    silently routes Cowork through CC; this test pins source-tag
    dispatch as the only correct behavior."""
    from backend import search_index as si

    index, store = index_with_cowork

    # Drop the index so build_full_index has work to do on a re-run.
    index.clear_all()

    # Wrap _load_conversation_at to observe what source-tagged paths
    # it sees — the Cowork audit.jsonl MUST NOT trigger a CC-reader
    # call inside the function.
    from backend import claude_code_reader as ccr

    cc_reader_calls: list[Path] = []
    real_cc_reader = ccr.read_claude_code_conversation

    def _trace_cc(path):
        cc_reader_calls.append(path)
        return real_cc_reader(path)

    monkeypatch.setattr(ccr, "read_claude_code_conversation", _trace_cc)
    monkeypatch.setattr(
        si, "read_claude_code_conversation", _trace_cc, raising=False
    )

    build_full_index(store, index=index)

    audit_calls = [p for p in cc_reader_calls if p.name == "audit.jsonl"]
    assert audit_calls == []
    # Not vacuous: the rebuild did index the Cowork session, through its
    # own reader (2026-10-23; the test only checked the CC reader before).
    rows = index.query("COWORK_FIXTURE_HELLO_XYZ", limit=10)
    assert {row["conv_uuid"] for row in rows} == {"aaaa1111-2222-3333-4444-555566667777"}
