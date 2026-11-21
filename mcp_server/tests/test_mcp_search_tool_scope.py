"""Issue #84: ``list_sessions`` searches tool content only when asked.

Same corpus and words as ``backend/tests/test_search_tool_scope.py``: one
session per source (Desktop, Claude Code, local and remote Cowork, Codex),
with a word only in the message text, only in a tool call input, only in a
tool output, and only in a thinking or reasoning block.

Contract (orchestrator, 2026-11-20):

- ``list_sessions`` takes ``include_tool_calls: bool = False``. With False
  a search matches session names and the text of user and assistant
  messages only; with True it also matches tool calls and tool output.
- The parameter description is the exact text in ``DESCRIPTION``.
- ``match_count`` counts matching messages only: a title-only session is
  listed with ``match_count`` 0.
- ``list_sessions`` and ``/api/search`` agree for the same
  ``include_tool_calls``.
- ``get_messages`` keeps its own ``include_tool_calls`` (default false).
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

DESCRIPTION = (
    "Also search tool calls and tool output: shell commands and their "
    "output, file reads and edits, MCP tool calls and their results, and web searches. "
    "Default false: the search matches only session names and the text of user and "
    "assistant messages. Set it to true when the words may be only in a command's output, "
    "a file, or a tool result (for example an error message, a file path, or a function "
    "name), or when a search with false finds nothing. Thinking and reasoning are never "
    "searched. Ignored when query is omitted."
)
MATCH_COUNT_LINE = "match_count: the number of matching messages; 0 when only the name matches."

PATHS = ("fts", "linear")
TOOLS = (True, False)
TOOLS_IDS = ["tools-on", "tools-off"]
KINDS = ("text", "call", "output")


def _sources() -> tuple:
    from backend.tests.test_search_tool_scope import SOURCES

    return SOURCES


# The parametrize ids are static, so collection imports nothing from the
# backend test module.
SOURCE_IDS = ["desktop", "claude-code", "cowork-local", "cowork-remote", "codex"]
TITLE_SOURCE_IDS = ["desktop", "codex"]


def _src(source_id: str) -> tuple:
    return next(s for s in _sources() if s[0] == source_id)


def _word(tag: str, kind: str) -> str:
    from backend.tests.test_search_tool_scope import word

    return word(tag, kind)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from backend.tests.test_search_all_sources import close_e2e_corpus
    from backend.tests.test_search_tool_scope import build_tool_scope_corpus

    idx = build_tool_scope_corpus(tmp_path, monkeypatch)
    try:
        yield
    finally:
        close_e2e_corpus(idx)


def _use(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.tests.test_search_all_sources import use_path

    use_path("fts-snippet" if path == "fts" else "linear", monkeypatch)


def _list(**kwargs: Any) -> dict[str, Any]:
    from mcp_server import server

    tool = server.list_sessions
    return getattr(tool, "fn", tool)(limit=100, **kwargs)


def _by_uuid(out: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["uuid"]: s for s in out["sessions"]}


async def _tool_schema(name: str) -> Any:
    from fastmcp import Client

    from mcp_server.server import mcp

    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    return tools[name]


# ---------------------------------------------------------------------------
# The parameter and its description
# ---------------------------------------------------------------------------


async def test_list_sessions_include_tool_calls_defaults_false() -> None:
    tool = await _tool_schema("list_sessions")
    prop = tool.inputSchema["properties"].get("include_tool_calls")
    assert prop is not None, f"no include_tool_calls in {sorted(tool.inputSchema['properties'])}"
    assert prop.get("type") == "boolean", prop
    assert prop.get("default") is False, prop
    assert "include_tool_calls" not in tool.inputSchema.get("required", [])


async def test_include_tool_calls_description_text() -> None:
    """The texts that a client sees: FastMCP moves each Args entry into the
    parameter schema, and drops a Returns section."""
    tool = await _tool_schema("list_sessions")
    prop = tool.inputSchema["properties"].get("include_tool_calls") or {}
    param = " ".join((prop.get("description") or "").split())
    assert param == DESCRIPTION, f"the include_tool_calls description is:\n{param}"
    description = " ".join((tool.description or "").split())
    assert MATCH_COUNT_LINE in description, f"the match_count line is missing from the tool description:\n{description}"


def test_get_messages_include_tool_calls_still_defaults_false() -> None:
    from mcp_server import server

    fn = getattr(server.get_messages, "fn", server.get_messages)
    assert inspect.signature(fn).parameters["include_tool_calls"].default is False


# ---------------------------------------------------------------------------
# What a search finds, per source and path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("kind", ("call", "output"))
@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_list_sessions_default_skips_tool_content(
    corpus: None, source_id: str, kind: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tag = _src(source_id)[3]
    _use(path, monkeypatch)
    out = _list(query=_word(tag, kind))
    assert _by_uuid(out) == {}, f"list_sessions(query={_word(tag, kind)!r}) with no include_tool_calls"
    # The flag exists and False gives the same result.
    out = _list(query=_word(tag, kind), include_tool_calls=False)
    assert _by_uuid(out) == {}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("kind", ("call", "output"))
@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_list_sessions_true_finds_tool_content(
    corpus: None, source_id: str, kind: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source, conv_uuid, tag = _src(source_id)
    _use(path, monkeypatch)
    sessions = _by_uuid(_list(query=_word(tag, kind), include_tool_calls=True))
    assert set(sessions) == {conv_uuid}, f"{_word(tag, kind)!r} include_tool_calls=True: {sorted(sessions)}"
    assert sessions[conv_uuid]["source"] == source
    assert sessions[conv_uuid]["match_count"] == 1, sessions[conv_uuid]


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_list_sessions_text_word_found_either_way(
    corpus: None, source_id: str, path: str, tools: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv_uuid, tag = _src(source_id)
    _use(path, monkeypatch)
    sessions = _by_uuid(_list(query=_word(tag, "text"), include_tool_calls=tools))
    assert set(sessions) == {conv_uuid}, f"{_word(tag, 'text')!r} include_tool_calls={tools}: {sorted(sessions)}"
    assert sessions[conv_uuid]["match_count"] >= 1


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_list_sessions_thinking_never_matches(
    corpus: None, source_id: str, path: str, tools: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tag = _src(source_id)[3]
    _use(path, monkeypatch)
    out = _list(query=_word(tag, "think"), include_tool_calls=tools)
    assert _by_uuid(out) == {}, f"{_word(tag, 'think')!r} include_tool_calls={tools}"


@pytest.mark.parametrize("tools", TOOLS, ids=TOOLS_IDS)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_list_sessions_agrees_with_api_search(corpus: None, source_id: str, kind: str, tools: bool) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    tag = _src(source_id)[3]
    q = _word(tag, kind)
    r = TestClient(app).get("/api/search", params={"q": q, "include_tool_calls": str(tools).lower()})
    assert r.status_code == 200, r.text
    api = {
        res["conversation_uuid"]: len([m for m in res["matching_messages"] if m["message_uuid"] != "title"])
        for res in r.json()["results"]
    }
    mcp = {u: s["match_count"] for u, s in _by_uuid(_list(query=q, include_tool_calls=tools)).items()}
    assert mcp == api, f"q={q!r} include_tool_calls={tools}: MCP {mcp} != /api/search {api}"


# ---------------------------------------------------------------------------
# match_count and titles (rule A)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("source_id", TITLE_SOURCE_IDS)
def test_match_count_excludes_title(
    corpus: None, source_id: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv_uuid, tag = _src(source_id)
    _use(path, monkeypatch)
    sessions = _by_uuid(_list(query=_word(tag, "titled")))
    assert set(sessions) == {conv_uuid}, sorted(sessions)
    assert sessions[conv_uuid]["match_count"] == 1, (
        f"the word is in the name and in one message: {sessions[conv_uuid]['match_count']}"
    )


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("source_id", TITLE_SOURCE_IDS)
def test_title_only_session_has_match_count_zero(
    corpus: None, source_id: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv_uuid, tag = _src(source_id)
    _use(path, monkeypatch)
    sessions = _by_uuid(_list(query=_word(tag, "titleonly")))
    assert set(sessions) == {conv_uuid}, "a title-only session is still listed"
    assert sessions[conv_uuid]["match_count"] == 0, sessions[conv_uuid]
