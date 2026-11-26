"""Issue #96: the MCP tools and agent transcripts.

* ``list_sessions(query=...)``: an agent hit is its own entry; ``uuid`` is
  the session uuid, with ``agent_id`` and ``agent_description``;
  ``match_count`` counts the agent's messages; a session entry has
  ``agent_id`` null.
* ``get_session_outline``, ``get_messages``, and ``export_session`` take an
  optional ``agent_id``; the outline output has ``agent_id``. A bad or
  unknown ``agent_id`` raises ``ValueError``.
* ``mcp_server/SPEC.md`` documents ``agent_id``.

The tree is ``backend/tests/test_subagent_discovery.write_tree``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests.test_subagent_discovery import (
    COMPACT_RUN,
    CUR_AGENT,
    CUR_DESCRIPTION,
    CUR_MESSAGE,
    ESCAPE_AGENT,
    LEGACY_AGENT,
    REAL_AGENT,
    REAL_AGENT_CODE_WORD,
    REAL_AGENT_REPLY_UUID,
    REAL_DESCRIPTION,
    REAL_SESSION,
    SESSION,
    WORDS,
    close_tree,
    use_tree,
)

SPEC = Path(__file__).resolve().parents[1] / "SPEC.md"


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from backend import search_index as si
    from backend.store import ConversationStore
    from mcp_server import server

    root = use_tree(tmp_path, monkeypatch)
    idx = si.SearchIndex(root / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    monkeypatch.setattr(si, "_search_index", idx)
    server.reset_archive_sync_for_tests()
    try:
        yield
    finally:
        server.reset_archive_sync_for_tests()
        idx.close()
        close_tree()


def _call(name: str, **kwargs: Any) -> Any:
    from mcp_server import server

    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


def _entries(query: str, **kw: Any) -> list[dict[str, Any]]:
    return _call("list_sessions", query=query, **kw)["sessions"]


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_an_agent_hit_is_an_entry_with_the_session_uuid_and_the_agent_id(corpus: None) -> None:
    (entry,) = _entries(WORDS["text"])
    assert entry["uuid"] == SESSION
    assert entry["agent_id"] == CUR_AGENT
    assert entry["agent_description"] == CUR_DESCRIPTION
    assert entry["match_count"] == 1
    assert entry["source"] == "CLAUDE_CODE"


def test_a_session_entry_has_agent_id_null(corpus: None) -> None:
    assert [e["agent_id"] for e in _entries(WORDS["text"])] == [CUR_AGENT]  # agents are searched
    (entry,) = _entries(WORDS["session"])
    assert entry["uuid"] == SESSION
    assert entry.get("agent_id") is None


def test_a_word_in_the_session_and_its_agent_gives_two_entries(corpus: None) -> None:
    pairs = sorted((e["uuid"], e.get("agent_id") or "") for e in _entries(WORDS["shared"]))
    assert pairs == [(SESSION, ""), (SESSION, CUR_AGENT)]


def test_the_agent_entry_takes_the_other_fields_from_the_session(corpus: None) -> None:
    entries = _entries(WORDS["shared"])
    session = next(e for e in entries if e.get("agent_id") is None)
    agent = next(e for e in entries if e.get("agent_id") == CUR_AGENT)
    for key in ("name", "project", "created_at", "source"):
        assert agent[key] == session[key], key


def test_the_toggles_apply_to_agent_hits(corpus: None) -> None:
    assert _entries(WORDS["tool"]) == []
    assert [e["agent_id"] for e in _entries(WORDS["tool"], include_tool_calls=True)] == [CUR_AGENT]
    assert _entries(REAL_AGENT_CODE_WORD) == []
    (real,) = _entries(REAL_AGENT_CODE_WORD, include_code_mode=True)
    assert (real["uuid"], real["agent_id"], real["agent_description"]) == (REAL_SESSION, REAL_AGENT, REAL_DESCRIPTION)


def test_the_project_filter_applies_to_agent_hits(corpus: None) -> None:
    assert [e["agent_id"] for e in _entries(WORDS["text"], project="proj-sa")] == [CUR_AGENT]
    assert _entries(WORDS["text"], project="cm-sample") == []


# ---------------------------------------------------------------------------
# get_session_outline, get_messages, export_session
# ---------------------------------------------------------------------------


def test_the_outline_of_an_agent_is_its_transcript(corpus: None) -> None:
    out = _call("get_session_outline", conversation_id=REAL_SESSION, agent_id=REAL_AGENT)
    assert out["conversation_id"] == REAL_SESSION
    assert out["agent_id"] == REAL_AGENT
    uuids = [m["message_uuid"] for m in out["messages"]]
    assert REAL_AGENT_REPLY_UUID in uuids
    assert sum(m["code_run_count"] for m in out["messages"]) == 1


def test_the_outline_of_the_session_is_not_the_agent(corpus: None) -> None:
    """The outline cache keys on the agent id too: the session outline stays the session."""
    _call("get_session_outline", conversation_id=REAL_SESSION, agent_id=REAL_AGENT)
    out = _call("get_session_outline", conversation_id=REAL_SESSION)
    assert out.get("agent_id") is None
    assert REAL_AGENT_REPLY_UUID not in [m["message_uuid"] for m in out["messages"]]
    again = _call("get_session_outline", conversation_id=REAL_SESSION, agent_id=REAL_AGENT)
    assert REAL_AGENT_REPLY_UUID in [m["message_uuid"] for m in again["messages"]]


def test_get_messages_reads_the_agent_by_message_uuid(corpus: None) -> None:
    (msg,) = _call("get_messages", conversation_id=SESSION, agent_id=CUR_AGENT,
                   message_uuids=[CUR_MESSAGE["text"]])
    assert WORDS["text"] in str(msg)


def test_get_messages_gives_the_agent_code_run_with_include_code_mode(corpus: None) -> None:
    msgs = _call("get_messages", conversation_id=REAL_SESSION, agent_id=REAL_AGENT, include_code_mode=True)
    runs = [r for m in msgs for r in m.get("code_runs", [])]
    assert len(runs) == 1 and REAL_AGENT_CODE_WORD in runs[0]["code"]


def test_get_messages_without_agent_id_is_the_session(corpus: None) -> None:
    assert WORDS["text"] in str(_call("get_messages", conversation_id=SESSION, agent_id=CUR_AGENT))
    msgs = _call("get_messages", conversation_id=SESSION)
    assert WORDS["session"] in str(msgs) and WORDS["text"] not in str(msgs)


def test_export_session_exports_the_agent(corpus: None) -> None:
    text = _call("export_session", conversation_id=SESSION, agent_id=LEGACY_AGENT)
    assert WORDS["legacy"] in text and WORDS["session"] not in text


@pytest.mark.parametrize("tool", ["get_session_outline", "get_messages", "export_session"])
@pytest.mark.parametrize("agent", ["a.b", "a" * 65, "aunknown0000096", COMPACT_RUN, ESCAPE_AGENT],
                         ids=["dot", "too-long", "unknown", "internal-run", "symlink-outside"])
def test_a_bad_or_unknown_agent_id_raises_value_error(corpus: None, tool: str, agent: str) -> None:
    # The tool must take agent_id at all: a TypeError would not prove the check.
    _call(tool, conversation_id=SESSION, agent_id=CUR_AGENT)
    with pytest.raises(ValueError):
        _call(tool, conversation_id=SESSION, agent_id=agent)


def test_an_agent_of_another_session_raises_value_error(corpus: None) -> None:
    _call("get_session_outline", conversation_id=SESSION, agent_id=CUR_AGENT)
    with pytest.raises(ValueError):
        _call("get_session_outline", conversation_id=REAL_SESSION, agent_id=CUR_AGENT)


def test_the_spec_documents_agent_id() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "agent_id" in text
    assert "agent_description" in text
