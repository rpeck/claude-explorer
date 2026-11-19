"""Characterization goldens for the 5 MCP tools.

They record today's outputs for the three sources, so the source adapter
refactor (N1) can prove that it changes nothing. The corpus and the
normalization rules are in ``backend/tests/goldens/corpus.py``.
``export_session`` goldens are unsliced only: L3 owns the sliced exports.
``UPDATE_GOLDENS=1`` rewrites the goldens.
"""

from __future__ import annotations

import pytest

from backend import search as search_mod
from backend.tests.goldens.corpus import (
    CONVERSATION_IDS,
    CONVERSATIONS,
    SEARCH_TOKENS,
    SOURCES,
    Corpus,
    build_corpus,
    check_golden,
    normalize,
)
from mcp_server import server


def _call(tool, **kwargs):
    return getattr(tool, "fn", tool)(**kwargs)


@pytest.fixture
def corpus(tmp_path, monkeypatch) -> Corpus:
    c = build_corpus(tmp_path, monkeypatch)

    def _no_linear(*_a, **_k):
        raise AssertionError("search fell back to the linear scan")

    # list_sessions(query=...) must use the FTS index that build_corpus made.
    monkeypatch.setattr(search_mod, "_search_via_linear_scan", _no_linear)
    return c


def test_list_sessions_golden(corpus):
    calls = {
        "default": {"limit": 100},
        "page_2_of_size_2": {"limit": 2, "offset": 2},
        **{f"source_{s}": {"source": s, "limit": 100} for s in SOURCES},
        **{f"query_{t}": {"query": t, "limit": 100} for t in SEARCH_TOKENS},
        "query_fixture": {"query": "fixture", "limit": 100},
        **{f"query_fixture_{s}": {"query": "fixture", "source": s, "limit": 100} for s in SOURCES},
        "project_fixture": {"project": "fixture", "limit": 100},
    }
    out = {key: _call(server.list_sessions, **kw) for key, kw in calls.items()}
    assert out["default"]["total"] == len(CONVERSATIONS)
    check_golden("mcp/list_sessions.json", normalize(out, corpus.root))


def test_list_projects_golden(corpus):
    out = {"all": _call(server.list_projects)}
    out.update({s: _call(server.list_projects, source=s) for s in SOURCES})
    check_golden("mcp/list_projects.json", normalize(out, corpus.root))


@pytest.mark.parametrize(("stem", "uuid"), CONVERSATIONS, ids=CONVERSATION_IDS)
def test_get_session_outline_golden(corpus, stem, uuid):
    out = {
        "first_call": _call(server.get_session_outline, conversation_id=uuid),
        # The second call reads the outline cache in cache.db.
        "cached_call": _call(server.get_session_outline, conversation_id=uuid),
        "page_1_size_2": _call(
            server.get_session_outline, conversation_id=uuid, start_position=1, limit=2
        ),
    }
    check_golden(f"mcp/outline/{stem}.json", normalize(out, corpus.root))


@pytest.mark.parametrize(("stem", "uuid"), CONVERSATIONS, ids=CONVERSATION_IDS)
def test_get_messages_golden(corpus, stem, uuid):
    out = {
        "all_default": _call(server.get_messages, conversation_id=uuid),
        "all_tool_calls": _call(
            server.get_messages, conversation_id=uuid, include_tool_calls=True
        ),
        "all_tool_results": _call(
            server.get_messages, conversation_id=uuid, include_tool_results=True
        ),
        "positions_0_1": _call(server.get_messages, conversation_id=uuid, positions=[0, 1]),
    }
    check_golden(f"mcp/messages/{stem}.json", normalize(out, corpus.root))


@pytest.mark.parametrize(("stem", "uuid"), CONVERSATIONS, ids=CONVERSATION_IDS)
def test_export_session_golden(corpus, stem, uuid):
    for include_tools, suffix in ((True, ""), (False, "-no-tools")):
        md = _call(server.export_session, conversation_id=uuid, include_tools=include_tools)
        check_golden(f"mcp/export/{stem}{suffix}.md", normalize(md, corpus.root))
