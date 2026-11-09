"""The conversation argument of the 3 per-session tools (GitHub issue #33).

The Claude desktop remote-devices bridge removes every tool argument
whose key is ``session_id`` before it forwards a call from a Cowork
cloud session to a local MCP server. So the per-session tools take
``conversation_id``. They still accept ``session_id`` from older
callers, but the JSON schema must not show it: a client that reads the
schema must never send the key that the bridge removes.

These tests call the tools through a FastMCP ``Client`` in memory, not
through ``.fn``. The bug is in the JSON arguments of an MCP call, and
only a real call runs the FastMCP middleware and the pydantic argument
check.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client

from mcp_server.server import mcp

PER_SESSION_TOOLS = ("get_session_outline", "get_messages", "export_session")


async def _call(tool: str, arguments: dict[str, Any]):
    async with Client(mcp) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


def _error_text_safe(result) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


def _error_text(result) -> str:
    assert result.is_error, f"expected an error result, got {result!r}"
    return _error_text_safe(result)


def _assert_tool_read_the_session(tool: str, result, uuid: str) -> None:
    """Prove that the tool loaded the fixture conversation ``uuid``."""
    assert not result.is_error, _error_text_safe(result)
    if tool == "get_session_outline":
        assert result.data["session_id"] == uuid
        assert [m["message_uuid"] for m in result.data["messages"]] == [
            "msg-h-1",
            "msg-a-1",
        ]
    elif tool == "get_messages":
        payload = json.loads(result.content[0].text)
        assert [m["uuid"] for m in payload] == ["msg-h-1", "msg-a-1"]
    else:
        markdown = result.content[0].text
        assert "# Alias Fixture" in markdown
        assert "Tell me about FTS5 in SQLite." in markdown


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


async def test_no_tool_schema_has_a_session_id_property():
    """No registered tool may offer ``session_id`` as an input property.

    The bridge removes that key, so a schema that offers it makes the
    tool impossible to call from a Cowork cloud session.
    """
    tools = await mcp.list_tools()
    assert tools, "the FastMCP registry returned no tools"
    offenders = [
        t.name for t in tools if "session_id" in t.parameters.get("properties", {})
    ]
    assert offenders == []


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_per_session_tool_requires_conversation_id(tool):
    """Each per-session tool shows ``conversation_id`` as a required string."""
    registered = {t.name: t for t in await mcp.list_tools()}
    params = registered[tool].parameters
    assert params["properties"]["conversation_id"]["type"] == "string"
    assert "conversation_id" in params.get("required", [])


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_conversation_id_succeeds(tool, mcp_data):
    uuid = mcp_data.add_desktop_session("alias-new", name="Alias Fixture")
    result = await _call(tool, {"conversation_id": uuid})
    _assert_tool_read_the_session(tool, result, uuid)


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_legacy_session_id_still_succeeds(tool, mcp_data):
    """Claude Code CLI callers and saved prompts still send ``session_id``."""
    uuid = mcp_data.add_desktop_session("alias-legacy", name="Alias Fixture")
    result = await _call(tool, {"session_id": uuid})
    _assert_tool_read_the_session(tool, result, uuid)


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_both_names_and_one_value_succeeds(tool, mcp_data):
    uuid = mcp_data.add_desktop_session("alias-both", name="Alias Fixture")
    result = await _call(tool, {"conversation_id": uuid, "session_id": uuid})
    _assert_tool_read_the_session(tool, result, uuid)


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_neither_name_names_conversation_id(tool, mcp_data):
    """This is the call that the bridge delivers when it removes ``session_id``.

    The error must tell the caller which argument to send instead.
    """
    mcp_data.add_desktop_session("alias-none", name="Alias Fixture")
    text = _error_text(await _call(tool, {}))
    assert "conversation_id" in text
    assert "required" in text.lower()


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_disagreeing_names_is_refused(tool, mcp_data):
    first = mcp_data.add_desktop_session("alias-first", name="Alias Fixture")
    second = mcp_data.add_desktop_session("alias-second", name="Other")
    text = _error_text(
        await _call(tool, {"conversation_id": first, "session_id": second})
    )
    assert "conversation_id" in text
    assert "session_id" in text
    assert "differ" in text


async def test_other_tools_keep_an_unknown_session_id_error(mcp_data):
    """The alias applies only to the 3 per-session tools.

    ``list_projects`` has no conversation argument, so ``session_id``
    stays an unknown argument there.
    """
    text = _error_text(await _call("list_projects", {"session_id": "x"}))
    assert "session_id" in text
    assert "Unexpected keyword argument" in text
