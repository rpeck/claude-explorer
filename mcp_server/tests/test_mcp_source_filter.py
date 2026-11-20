"""L6: the MCP ``source`` argument refuses an unknown value (issue #73).

Before L6, ``list_sessions`` and ``list_projects`` treated an unknown
``source`` as ``"all"``: ``source="codx"`` returned every session of every
source, and the caller could not tell. The web API refuses such a value
(HTTP 422).

Contract (orchestrator, 2026-11-19):

- ``_source_filter(source)`` in ``mcp_server/server.py`` is the one helper
  of both tools. ``None`` and ``"all"`` (any case) mean all sources. A
  registry id matches in any case and gives the canonical id.
- The helper strips outer spaces first. ``""`` and a blank value mean all
  sources, like ``None``.
- Any other value raises ``ToolError`` with the text
  ``Unknown source "<value>". Use one of: all, <registry ids in order>.``
  The ids come from the registry at call time, not from a fixed list. The
  text quotes the value as given, before the strip.
- The docstrings and ``mcp_server/SPEC.md`` say both rules.

Every Codex session comes from ``backend.tests.codex_fake``; nothing reads a
real ``~/.codex`` or ``~/.claude``.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastmcp import Client

from backend.tests import codex_fake as fake
from backend.tests._platform_home import patch_home

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "mcp_server" / "SPEC.md"
VISIBLE_CODEX = sorted(t for t in fake.ALL_THREADS if t != fake.THREAD_ARCHIVED)
EXPECTED_IDS = "all, CLAUDE_AI, CLAUDE_CODE, CLAUDE_COWORK, CODEX"
BAD_VALUES = ("codx", "claude", "*", "CODEX,CLAUDE_AI")
TOOLS = ("list_sessions", "list_projects")


def _tool(name: str, **kwargs: Any) -> Any:
    from mcp_server import server

    fn = getattr(server, name)
    return getattr(fn, "fn", fn)(**kwargs)


def _doc(name: str) -> str:
    from mcp_server import server

    fn = getattr(server, name)
    return getattr(fn, "fn", fn).__doc__ or ""


def _source_section(doc: str) -> str:
    """The ``source:`` argument text of a docstring, up to the next argument."""
    start = doc.index("source:")
    rest = doc[start + len("source:") :]
    lines = [rest.splitlines()[0]]
    for line in rest.splitlines()[1:]:
        stripped = line.strip()
        if not stripped or (":" in stripped and stripped.split(":")[0].isidentifier()):
            break
        lines.append(stripped)
    return " ".join(lines)


async def _call(tool: str, arguments: dict[str, Any]) -> Any:
    from mcp_server.server import mcp

    async with Client(mcp) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


def _text(result: Any) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


def _unknown(value: str, ids: str = EXPECTED_IDS) -> str:
    return f'Unknown source "{value}". Use one of: {ids}.'


@dataclass
class Corpus:
    desktop: str
    cc: str


@pytest.fixture
def corpus(mcp_data: Any, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Corpus]:
    """One Claude Desktop session, one Claude Code session, and the fake Codex home."""
    from backend import config
    from mcp_server import server

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    codex = fake.build_codex_home(home / ".codex")
    monkeypatch.setenv("CODEX_HOME", str(codex))
    config.get_settings.cache_clear()
    server.reset_archive_sync_for_tests()
    desktop = mcp_data.add_desktop_session("desktop-1", name="Desktop one")
    cc = mcp_data.add_cc_session("cc-1", cwd="/fixture/cc-project", user_text="Find the semicolon bug.")
    try:
        yield Corpus(desktop=desktop, cc=cc)
    finally:
        server.reset_archive_sync_for_tests()
        config.get_settings.cache_clear()


def _sources(result: dict[str, Any]) -> set[str]:
    return {s["source"] for s in result["sessions"]}


def _uuids(result: dict[str, Any]) -> list[str]:
    return sorted(s["uuid"] for s in result["sessions"])


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "all", "ALL", "All"])
def test_the_helper_maps_no_value_and_all_in_any_case_to_all(value: str | None) -> None:
    from mcp_server.server import _source_filter

    assert _source_filter(value) == "all"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("codex", "CODEX"),
        ("Codex", "CODEX"),
        ("CODEX", "CODEX"),
        ("claude_ai", "CLAUDE_AI"),
        ("Claude_Code", "CLAUDE_CODE"),
        ("claude_cowork", "CLAUDE_COWORK"),
    ],
)
def test_the_helper_matches_a_known_source_in_any_case(value: str, expected: str) -> None:
    from mcp_server.server import _source_filter

    assert _source_filter(value) == expected


@pytest.mark.parametrize("value", ["", "  ", " all "])
def test_the_helper_maps_an_empty_or_blank_value_to_all(value: str) -> None:
    from mcp_server.server import _source_filter

    assert _source_filter(value) == "all"


@pytest.mark.parametrize(("value", "expected"), [(" codex ", "CODEX"), ("\tClaude_AI\n", "CLAUDE_AI")])
def test_the_helper_strips_outer_spaces_before_it_matches(value: str, expected: str) -> None:
    from mcp_server.server import _source_filter

    assert _source_filter(value) == expected


def test_the_error_quotes_the_value_as_given_before_the_strip() -> None:
    from fastmcp.exceptions import ToolError

    from mcp_server.server import _source_filter

    with pytest.raises(ToolError) as info:
        _source_filter(" codx ")
    assert str(info.value) == _unknown(" codx ")


@pytest.mark.parametrize("value", BAD_VALUES)
def test_the_helper_refuses_an_unknown_source_with_a_tool_error(value: str) -> None:
    from fastmcp.exceptions import ToolError

    from mcp_server.server import _source_filter

    with pytest.raises(ToolError) as info:
        _source_filter(value)
    assert str(info.value) == _unknown(value)


def test_the_error_lists_the_ids_of_the_registry_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.exceptions import ToolError

    from backend.sources import registry
    from mcp_server.server import _source_filter

    monkeypatch.setattr(registry, "_registry", dict(registry._registry))
    codex_descriptor, codex_factory = registry._registry["CODEX"]
    extra = dataclasses.replace(codex_descriptor, id="EXTRA_SOURCE")
    registry.register_adapter(extra, codex_factory)

    with pytest.raises(ToolError) as info:
        _source_filter("codx")
    assert str(info.value) == _unknown("codx", EXPECTED_IDS + ", EXTRA_SOURCE")
    assert _source_filter("extra_source") == "EXTRA_SOURCE"


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["codex", "Codex", "CODEX"])
def test_list_sessions_with_codex_in_any_case_lists_only_the_codex_sessions(corpus: Corpus, value: str) -> None:
    result = _tool("list_sessions", source=value, limit=100)

    assert _sources(result) == {"CODEX"}
    assert _uuids(result) == VISIBLE_CODEX


@pytest.mark.parametrize("value", ["claude_code", "Claude_Code"])
def test_list_sessions_with_another_source_in_any_case_lists_only_that_source(corpus: Corpus, value: str) -> None:
    result = _tool("list_sessions", source=value, limit=100)

    assert _uuids(result) == [corpus.cc]





@pytest.mark.parametrize("value", [None, "all", "ALL", ""])
def test_list_sessions_with_no_source_or_all_lists_every_source(corpus: Corpus, value: str | None) -> None:
    result = _tool("list_sessions", source=value, limit=100)

    assert _uuids(result) == sorted([corpus.desktop, corpus.cc, *VISIBLE_CODEX])


def test_list_sessions_search_with_a_lowercase_source_filters_by_that_source(corpus: Corpus) -> None:
    result = _tool("list_sessions", query="semicolon", source="codex", limit=100)

    assert result["sessions"]
    assert _sources(result) == {"CODEX"}


@pytest.mark.parametrize("value", BAD_VALUES)
def test_list_sessions_refuses_an_unknown_source(corpus: Corpus, value: str) -> None:
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        _tool("list_sessions", source=value)
    assert str(info.value) == _unknown(value)


def test_list_sessions_search_refuses_an_unknown_source(corpus: Corpus) -> None:
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        _tool("list_sessions", query="semicolon", source="codx")
    assert str(info.value) == _unknown("codx")


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


def _projects(result: list[dict[str, Any]]) -> set[str]:
    return {p["project"] for p in result}


@pytest.mark.parametrize("value", ["codex", "Codex", "CODEX"])
def test_list_projects_with_codex_in_any_case_lists_only_codex_projects(corpus: Corpus, value: str) -> None:
    codex_projects = _projects(_tool("list_projects", source="CODEX"))

    result = _projects(_tool("list_projects", source=value))

    assert "proj-alpha" in result
    assert result == codex_projects
    assert "cc-project" not in " ".join(result)





@pytest.mark.parametrize("value", [None, "all", "All", ""])
def test_list_projects_with_no_source_or_all_lists_every_source(corpus: Corpus, value: str | None) -> None:
    result = _projects(_tool("list_projects", source=value))

    assert "proj-alpha" in result
    assert "cc-project" in " ".join(result)


@pytest.mark.parametrize("value", BAD_VALUES)
def test_list_projects_refuses_an_unknown_source(corpus: Corpus, value: str) -> None:
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        _tool("list_projects", source=value)
    assert str(info.value) == _unknown(value)


# ---------------------------------------------------------------------------
# A real MCP call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
async def test_an_mcp_call_with_an_unknown_source_is_a_tool_error_that_names_it(corpus: Corpus, tool: str) -> None:
    result = await _call(tool, {"source": "codx"})

    assert result.is_error, f"expected an error result, got {result!r}"
    assert _text(result) == _unknown("codx")


async def test_both_tools_give_the_same_error_text(corpus: Corpus) -> None:
    texts = {tool: _text(await _call(tool, {"source": "CoDx"})) for tool in TOOLS}

    assert texts["list_sessions"] == texts["list_projects"] == _unknown("CoDx")


@pytest.mark.parametrize("tool", TOOLS)
async def test_an_mcp_call_with_a_lowercase_source_lists_only_that_source(corpus: Corpus, tool: str) -> None:
    result = await _call(tool, {"source": "codex"})

    assert not result.is_error, _text(result)
    assert '"CLAUDE_CODE"' not in _text(result) and "cc-project" not in _text(result), _text(result)


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
def test_the_docstring_says_a_source_matches_in_any_case_and_another_value_is_an_error(tool: str) -> None:
    section = _source_section(_doc(tool)).lower()

    assert "case" in section, section
    assert "error" in section, section
    assert "means all" not in section and 'means "all"' not in section, section


def test_spec_no_longer_says_an_unknown_source_falls_back_to_all() -> None:
    text = SPEC.read_text(encoding="utf-8")

    assert "silently fall back" not in text
    assert 'Any other value (or `None`) means "all"' not in text


def test_spec_says_a_source_matches_in_any_case_and_names_the_error() -> None:
    text = SPEC.read_text(encoding="utf-8")

    assert "Unknown source" in text
    assert "case" in text.lower()
