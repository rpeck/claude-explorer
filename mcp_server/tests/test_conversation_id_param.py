"""The conversation argument of the 3 per-session tools (GitHub issue #33).

The Claude desktop remote-devices bridge removes every tool argument
whose key is ``session_id`` before it forwards a call from a Cowork
cloud session to a local MCP server. So the per-session tools take
``conversation_id``, and no tool may offer ``session_id``.

These tests call the tools through a FastMCP ``Client`` in memory, not
through ``.fn``. The bug is in the JSON arguments of an MCP call, and
only a real call runs the pydantic argument check.
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


def _content_text(result) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


def _error_text(result) -> str:
    assert result.is_error, f"expected an error result, got {result!r}"
    return _content_text(result)


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


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_conversation_id_description_names_the_former_name(tool):
    """A model reads this description when it builds the call. It must
    learn that a ``session_id`` in an old prompt means ``conversation_id``."""
    registered = {t.name: t for t in await mcp.list_tools()}
    description = registered[tool].parameters["properties"]["conversation_id"][
        "description"
    ]
    assert "formerly named session_id" in description
    assert "send conversation_id" in description


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_conversation_id_succeeds(tool, mcp_data):
    uuid = mcp_data.add_desktop_session("conv-id", name="Conversation Fixture")
    result = await _call(tool, {"conversation_id": uuid})
    assert not result.is_error, _content_text(result)
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
        assert "# Conversation Fixture" in markdown
        assert "Tell me about FTS5 in SQLite." in markdown


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_neither_name_names_conversation_id(tool, mcp_data):
    """This is the call that the bridge delivers when it removes ``session_id``.

    The error must tell the caller which argument to send instead.
    """
    mcp_data.add_desktop_session("conv-none", name="Conversation Fixture")
    text = _error_text(await _call(tool, {}))
    assert "conversation_id" in text
    assert "Missing required argument" in text


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_old_session_id_names_conversation_id(tool, mcp_data):
    """An old prompt or script that still sends ``session_id`` gets an error
    that names the argument to send instead, so a model can retry."""
    uuid = mcp_data.add_desktop_session("conv-old", name="Conversation Fixture")
    text = _error_text(await _call(tool, {"session_id": uuid}))
    assert "conversation_id" in text
    assert "Missing required argument" in text
    assert "session_id" in text
    assert "Unexpected keyword argument" in text
