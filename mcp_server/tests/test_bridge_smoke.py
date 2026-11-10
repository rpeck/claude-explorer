"""Smoke test: every tool works through a simulated remote-devices bridge.

A Cowork cloud session reaches this server through the Claude desktop app's
remote-devices bridge. The bridge does not forward every tool argument: it
removes each argument named ``session_id`` (GitHub issue #33, seen on desktop
app 4.4212.0 and earlier builds since 2026-10-26). A tool that needs a removed
argument cannot be called from Cowork, and no in-process test notices.

This test starts the server as a real stdio subprocess, the way that Claude
Desktop starts it. It reads the tool list from the server, builds the required
arguments of each tool from its schema, removes what the bridge removes, and
requires every call to succeed. A new tool is covered without a change here.

The real bridge cannot run in CI. TESTING.md §8 (testing/manual-checks.md)
has the manual smoke prompt for a real Cowork session. When that check finds
another argument that the bridge removes, add it to BRIDGE_REMOVED_ARGUMENTS.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

REPO = Path(__file__).resolve().parents[2]

# Argument names that the remote-devices bridge removes from a tool call.
BRIDGE_REMOVED_ARGUMENTS = frozenset({"session_id"})


def _through_bridge(arguments: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in arguments.items() if k not in BRIDGE_REMOVED_ARGUMENTS}


@asynccontextmanager
async def _stdio_client() -> AsyncIterator[Client]:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        # The isolated_data_dir fixture put the temp data, Claude Code, and
        # Cowork folders in os.environ, so the subprocess reads only those.
        env=dict(os.environ),
        cwd=str(REPO),
        # The default (True) keeps the subprocess alive after the client
        # closes. Its asyncio transport is then garbage-collected while open,
        # and on Windows that raises a ResourceWarning that pytest turns into
        # a teardown error (main at 82c5b7b9, both Windows pytest rows).
        keep_alive=False,
    )
    try:
        async with Client(transport) as client:
            yield client
    finally:
        await transport.close()


def _text(result) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


async def test_every_tool_works_through_the_bridge(mcp_data):
    uuid = mcp_data.add_desktop_session("bridge-smoke", name="Bridge Smoke")
    mcp_data.add_cc_session("bridge-cc", cwd="/fixture/bridge-project")
    # A value for every argument name that a session tool has ever used.
    # session_id is here on purpose: a tool that still needs it builds a call
    # that the bridge empties, so this test fails with the real symptom.
    values = {"conversation_id": uuid, "session_id": uuid}

    called: dict[str, str] = {}
    async with _stdio_client() as client:
        tools = await client.list_tools()
        assert len(tools) == 5, [t.name for t in tools]
        for tool in tools:
            required = tool.inputSchema.get("required", [])
            missing = [name for name in required if name not in values]
            assert not missing, (
                f"{tool.name} requires {missing}; add a fixture value for it"
            )
            arguments = _through_bridge({name: values[name] for name in required})
            result = await client.call_tool(tool.name, arguments, raise_on_error=False)
            assert not result.is_error, f"{tool.name}({arguments}): {_text(result)}"
            called[tool.name] = _text(result)

    # Each tool returned this fixture's data, not only "no error".
    assert set(called) == {
        "list_sessions", "list_projects", "get_session_outline",
        "get_messages", "export_session",
    }
    assert uuid in called["list_sessions"]
    assert "bridge-project" in called["list_projects"]
    assert json.loads(called["get_session_outline"])["conversation_id"] == uuid
    assert [m["uuid"] for m in json.loads(called["get_messages"])] == [
        "msg-h-1", "msg-a-1",
    ]
    assert "# Bridge Smoke" in called["export_session"]


async def test_the_bridge_simulation_removes_session_id(mcp_data):
    """Proof that the simulation is not a no-op: an old-style call loses its
    ``session_id`` on the way, and the server explains what to send."""
    uuid = mcp_data.add_desktop_session("bridge-old", name="Bridge Smoke")
    async with _stdio_client() as client:
        result = await client.call_tool(
            "get_session_outline", _through_bridge({"session_id": uuid}),
            raise_on_error=False,
        )
    assert result.is_error
    assert "Missing required argument 'conversation_id'" in _text(result)
    assert "formerly named 'session_id'" in _text(result)
