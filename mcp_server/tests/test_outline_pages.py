"""``get_session_outline`` returns the outline in pages.

On 2026-11-09 a Cowork client refused the outline of a 579-message session:
126,470 characters, over the client's size limit for one tool result. A
model that cannot read the outline cannot use the outline-first workflow.
So the outline has ``start_position`` and ``limit`` arguments, a default
page that fits in a client, and ``next_position`` for the next page.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client

from mcp_server.server import (
    OUTLINE_PAGE_DEFAULT,
    OUTLINE_PAGE_MAX,
    get_session_outline,
    mcp,
)

# One page of the default size must fit in a client with a size limit. The
# refused outline had 126,470 characters; this budget is far below it.
DEFAULT_PAGE_CHAR_BUDGET = 40_000


def _call(**kwargs) -> dict[str, Any]:
    fn = getattr(get_session_outline, "fn", get_session_outline)
    return fn(**kwargs)


def _long_session(mcp_data, name: str, count: int) -> str:
    """A session of ``count`` messages, each with a full 200-character summary."""
    messages = []
    parent = None
    for i in range(count):
        uuid = f"msg-{i:04d}"
        text = f"Message {i}: " + " ".join(["word"] * 60)
        messages.append({
            "uuid": uuid,
            "sender": "human" if i % 2 == 0 else "assistant",
            "text": text,
            "content": [{"type": "text", "text": text}],
            "created_at": "2026-04-01T10:00:00Z",
            "updated_at": "2026-04-01T10:00:00Z",
            "parent_message_uuid": parent,
        })
        parent = uuid
    return mcp_data.add_desktop_session(name, name="Long Session", messages=messages)


def _positions(result: dict[str, Any]) -> list[int]:
    return [m["position"] for m in result["messages"]]


def test_a_small_session_fits_in_one_page(mcp_data):
    uuid = mcp_data.add_desktop_session("short", name="Short")
    result = _call(conversation_id=uuid)
    assert _positions(result) == [0, 1]
    assert result["start_position"] == 0
    assert result["next_position"] is None
    assert result["message_count"] == 2


def test_the_default_page_of_a_long_session(mcp_data):
    count = OUTLINE_PAGE_DEFAULT + 30
    uuid = _long_session(mcp_data, "long-default", count)
    result = _call(conversation_id=uuid)
    assert _positions(result) == list(range(OUTLINE_PAGE_DEFAULT))
    assert result["next_position"] == OUTLINE_PAGE_DEFAULT
    # message_count stays the size of the whole session, not of the page.
    assert result["message_count"] == count


def test_the_default_page_fits_in_a_client(mcp_data):
    uuid = _long_session(mcp_data, "long-size", OUTLINE_PAGE_DEFAULT * 3)
    result = _call(conversation_id=uuid)
    assert all(len(m["summary"]) >= 190 for m in result["messages"])  # full-size summaries
    assert len(json.dumps(result)) <= DEFAULT_PAGE_CHAR_BUDGET


def test_pages_cover_every_position_once(mcp_data):
    count = 57
    uuid = _long_session(mcp_data, "long-pages", count)
    seen: list[int] = []
    start: int | None = 0
    pages = 0
    while start is not None:
        result = _call(conversation_id=uuid, start_position=start, limit=10)
        seen += _positions(result)
        start = result["next_position"]
        pages += 1
    assert seen == list(range(count))
    assert pages == 6


def test_a_page_in_the_middle(mcp_data):
    uuid = _long_session(mcp_data, "long-middle", 30)
    result = _call(conversation_id=uuid, start_position=5, limit=10)
    assert _positions(result) == list(range(5, 15))
    assert result["start_position"] == 5
    assert result["next_position"] == 15


def test_the_last_page_has_no_next_position(mcp_data):
    uuid = _long_session(mcp_data, "long-last", 30)
    result = _call(conversation_id=uuid, start_position=25, limit=10)
    assert _positions(result) == list(range(25, 30))
    assert result["next_position"] is None


def test_a_start_past_the_end_gives_an_empty_page(mcp_data):
    uuid = _long_session(mcp_data, "long-past", 12)
    result = _call(conversation_id=uuid, start_position=50)
    assert result["messages"] == []
    assert result["next_position"] is None
    assert result["message_count"] == 12


@pytest.mark.parametrize(
    ("start", "limit", "expected_start", "expected_count"),
    [
        (-5, 3, 0, 3),  # a negative start counts from the beginning
        (0, 0, 0, 1),  # a limit below 1 is 1
        (0, OUTLINE_PAGE_MAX + 100, 0, OUTLINE_PAGE_MAX),  # a limit above the maximum is the maximum
    ],
)
def test_out_of_range_arguments_are_clamped(mcp_data, start, limit, expected_start, expected_count):
    uuid = _long_session(mcp_data, f"long-clamp-{start}-{limit}", OUTLINE_PAGE_MAX + 10)
    result = _call(conversation_id=uuid, start_position=start, limit=limit)
    assert result["start_position"] == expected_start
    assert len(result["messages"]) == expected_count
    assert result["messages"][0]["position"] == expected_start


def test_a_page_reads_the_same_cache_rows_as_the_whole_outline(mcp_data):
    """Pages are slices of one cached outline, so they agree with each other."""
    uuid = _long_session(mcp_data, "long-cache", 25)
    whole = _call(conversation_id=uuid, limit=OUTLINE_PAGE_MAX)
    page = _call(conversation_id=uuid, start_position=10, limit=5)
    assert page["messages"] == whole["messages"][10:15]


async def test_pages_through_a_real_mcp_call(mcp_data):
    """The arguments arrive as JSON through the FastMCP Client."""
    uuid = _long_session(mcp_data, "long-client", 8)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_session_outline",
            {"conversation_id": uuid, "start_position": 3, "limit": 2},
            raise_on_error=False,
        )
    assert not result.is_error
    assert [m["position"] for m in result.data["messages"]] == [3, 4]
    assert result.data["next_position"] == 5


async def test_the_schema_offers_the_page_arguments():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    schema = tools["get_session_outline"].inputSchema
    assert schema["required"] == ["conversation_id"]
    assert {"start_position", "limit"} <= set(schema["properties"])
    description = " ".join((tools["get_session_outline"].description or "").split())
    assert "next_position" in description
