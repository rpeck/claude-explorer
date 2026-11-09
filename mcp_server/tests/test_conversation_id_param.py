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


async def _wire_tools() -> dict[str, dict[str, Any]]:
    """{tool name: inputSchema} as a client receives them, not the registry."""
    async with Client(mcp) as client:
        return {t.name: t.inputSchema for t in await client.list_tools()}


def _property_names(schema: Any) -> set[str]:
    """Every property name at any depth of a JSON schema."""
    names: set[str] = set()
    if isinstance(schema, dict):
        names.update(schema.get("properties", {}))
        for value in schema.values():
            names |= _property_names(value)
    elif isinstance(schema, list):
        for value in schema:
            names |= _property_names(value)
    return names


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
    tools = await _wire_tools()
    assert len(tools) == 5, sorted(tools)
    # Positive control: the walk finds the properties that do exist.
    assert {"conversation_id", "positions"} <= _property_names(tools["get_messages"])
    offenders = [name for name, schema in tools.items() if "session_id" in _property_names(schema)]
    assert offenders == []


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_per_session_tool_requires_conversation_id(tool):
    """Each per-session tool shows ``conversation_id`` as a required string."""
    params = (await _wire_tools())[tool]
    assert params["properties"]["conversation_id"]["type"] == "string"
    assert "conversation_id" in params.get("required", [])


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_conversation_id_description_names_the_former_name(tool):
    """A model reads this description when it builds the call. It must
    learn that a ``session_id`` in an old prompt means ``conversation_id``."""
    raw = (await _wire_tools())[tool]["properties"]["conversation_id"]["description"]
    description = " ".join(raw.split())  # a re-wrapped docstring must still pass
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
        assert result.data["conversation_id"] == uuid
        assert [m["message_uuid"] for m in result.data["messages"]] == [
            "msg-h-1",
            "msg-a-1",
        ]
    elif tool == "get_messages":
        payload = json.loads(_content_text(result))
        assert [m["uuid"] for m in payload] == ["msg-h-1", "msg-a-1"]
    else:
        markdown = _content_text(result)
        assert "# Conversation Fixture" in markdown
        assert "Tell me about FTS5 in SQLite." in markdown


async def test_outline_output_uses_the_argument_name_and_keeps_session_id(mcp_data):
    """The outline names the UUID ``conversation_id``, the same name as the
    argument, so a model can pass it on to get_messages unchanged. It also
    keeps ``session_id`` for existing readers of the output."""
    uuid = mcp_data.add_desktop_session("conv-out", name="Conversation Fixture")
    result = await _call("get_session_outline", {"conversation_id": uuid})
    assert not result.is_error, _content_text(result)
    assert result.data["conversation_id"] == uuid
    assert result.data["session_id"] == uuid


async def test_outline_conversation_id_passes_on_to_get_messages(mcp_data):
    """A model copies the outline's ``conversation_id`` into get_messages.
    That round trip must work through real MCP calls."""
    uuid = mcp_data.add_desktop_session("conv-trip", name="Conversation Fixture")
    outline = await _call("get_session_outline", {"conversation_id": uuid})
    assert not outline.is_error, _content_text(outline)
    result = await _call(
        "get_messages",
        {"conversation_id": outline.data["conversation_id"], "positions": [1]},
    )
    assert not result.is_error, _content_text(result)
    assert [m["uuid"] for m in json.loads(_content_text(result))] == ["msg-a-1"]


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_an_unknown_conversation_id_says_not_found(tool, mcp_data):
    mcp_data.add_desktop_session("conv-known", name="Conversation Fixture")
    missing = "11111111-0000-0000-0000-000000000000"
    text = _error_text(await _call(tool, {"conversation_id": missing}))
    assert f"Session '{missing}' not found." in text


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
async def test_call_with_neither_name_explains_the_rename(tool, mcp_data):
    """This is the call that the bridge delivers when it removes ``session_id``.

    The error must tell the model to send ``conversation_id``, and say why the
    ``session_id`` that it sent did not arrive.
    """
    mcp_data.add_desktop_session("conv-none", name="Conversation Fixture")
    text = _error_text(await _call(tool, {}))
    assert "Missing required argument 'conversation_id'" in text
    assert "formerly named 'session_id'" in text
    assert "bridges remove an argument named 'session_id'" in text


@pytest.mark.parametrize("tool", PER_SESSION_TOOLS)
@pytest.mark.parametrize("with_conversation_id", [False, True])
async def test_call_with_session_id_explains_the_rename(
    tool, with_conversation_id, mcp_data
):
    """An old prompt or script that still sends ``session_id`` gets an error
    that tells the model to send ``conversation_id`` instead. This holds also
    when the call sends both names, with the same value."""
    uuid = mcp_data.add_desktop_session("conv-old", name="Conversation Fixture")
    arguments = {"session_id": uuid}
    if with_conversation_id:
        arguments["conversation_id"] = uuid
    text = _error_text(await _call(tool, arguments))
    assert "'session_id' was renamed to 'conversation_id'" in text
    assert "Send the session UUID as 'conversation_id'" in text
    # Negative space: the rename message replaces the generic pydantic one.
    assert "Unexpected keyword argument" not in text


async def test_other_tools_keep_the_generic_unknown_argument_error(mcp_data):
    """The rename message is only for the 3 per-session tools.
    ``list_projects`` never had a session argument."""
    text = _error_text(await _call("list_projects", {"session_id": "x"}))
    assert "Unexpected keyword argument" in text
    assert "renamed" not in text
