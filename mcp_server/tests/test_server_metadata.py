"""Server-level metadata tests: name, instructions text, tool registry.

These don't need fixture data; they verify the static shape of the
FastMCP server object.
"""

from __future__ import annotations

from mcp_server.server import mcp

EXPECTED_TOOLS = {
    "list_sessions",
    "list_projects",
    "get_session_outline",
    "get_messages",
    "export_session",
}


def test_server_name():
    """Server name is the user-visible identity in MCP client UIs."""
    # FastMCP exposes the name through the constructor argument; the
    # field name has varied across releases, so check the public-ish
    # attributes most likely to carry it.
    name = getattr(mcp, "name", None) or getattr(mcp, "_name", None)
    assert name == "Claude Session Explorer"


def test_server_instructions_are_explicit_only():
    """The server-level instructions must carry the 'only when explicitly
    asked' wording. This is the durable engineering safeguard that keeps
    client LLMs from speculatively fanning out across saved sessions."""
    instructions = (
        getattr(mcp, "instructions", None)
        or getattr(mcp, "_instructions", None)
        or ""
    )
    assert "ONLY use them when the user EXPLICITLY" in instructions, (
        "Server-level instructions must include the literal 'ONLY use them "
        "when the user EXPLICITLY' clause."
    )
    assert "Never call these tools proactively or speculatively" in instructions


async def test_tool_registry_has_all_five_tools():
    """All five tools must be registered with the expected names.

    FastMCP 3 exposes the registered tools via the async
    :meth:`FastMCP.list_tools` method, which returns
    ``list[FunctionTool]`` with a ``.name`` attribute per entry.
    """
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS, (
        f"Tool registry mismatch.\n  expected: {sorted(EXPECTED_TOOLS)}\n  got:      {sorted(names)}"
    )
