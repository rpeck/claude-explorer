"""Tests for the ``get_messages`` MCP tool."""

from __future__ import annotations

import pytest

from mcp_server.server import get_messages


def _call(**kwargs):
    fn = getattr(get_messages, "fn", get_messages)
    return fn(**kwargs)


def test_get_messages_by_positions(mcp_data):
    u = mcp_data.add_desktop_session("u-1")
    result = _call(session_id=u, positions=[0, 1])
    assert len(result) == 2
    assert result[0]["position"] == 0
    assert result[0]["sender"] == "human"
    assert result[0]["uuid"] == "msg-h-1"
    assert result[1]["position"] == 1
    assert result[1]["sender"] == "assistant"


def test_get_messages_by_message_uuids(mcp_data):
    u = mcp_data.add_desktop_session("u-1")
    result = _call(session_id=u, message_uuids=["msg-a-1"])
    assert len(result) == 1
    assert result[0]["uuid"] == "msg-a-1"
    assert result[0]["sender"] == "assistant"


def test_get_messages_no_selector_returns_all(mcp_data):
    u = mcp_data.add_desktop_session("u-1")
    result = _call(session_id=u)
    assert len(result) == 2


def test_get_messages_out_of_range_positions_silently_dropped(mcp_data):
    u = mcp_data.add_desktop_session("u-1")
    # positions=[5, 1, -1] — only 1 is valid.
    result = _call(session_id=u, positions=[5, 1, -1])
    assert len(result) == 1
    assert result[0]["position"] == 1


def test_text_only_mode_strips_tool_placeholders(mcp_data):
    # The exact TOOL_PLACEHOLDER literal from backend/export.py:45 — including
    # the trailing period — is what the filter matches.
    msg_with_placeholder = {
        "uuid": "m-1",
        "sender": "assistant",
        "text": (
            "I'll check that for you. "
            "This block is not supported on your current device yet."
        ),
        "content": [
            {"type": "text", "text": (
                "I'll check that for you. "
                "This block is not supported on your current device yet."
            )},
        ],
        "created_at": "2026-04-01T10:00:00Z",
        "updated_at": "2026-04-01T10:00:00Z",
        "parent_message_uuid": None,
    }
    u = mcp_data.add_desktop_session("u-tp", messages=[msg_with_placeholder])
    result = _call(session_id=u, positions=[0])
    text = result[0]["text"]
    assert "This block is not supported on your current device yet." not in text
    assert "I'll check that for you" in text


def test_include_tool_calls_returns_structured_content(mcp_data):
    msg = mcp_data.make_tool_message(
        uuid="m-t",
        sender="assistant",
        text_before="Reading the file.",
        tool_name="read_file",
        tool_input={"path": "/foo/bar"},
        tool_result="contents",
    )
    u = mcp_data.add_desktop_session("u-t", messages=[msg])

    result = _call(session_id=u, positions=[0], include_tool_calls=True)
    assert "text" not in result[0]  # structured mode → no "text" key
    assert "content" in result[0]

    block_types = [b["type"] for b in result[0]["content"]]
    assert "text" in block_types
    assert "tool_use" in block_types
    # tool_result is gated by include_tool_results, which defaults False here.
    assert "tool_result" not in block_types


def test_include_tool_results_implies_include_tool_calls(mcp_data):
    msg = mcp_data.make_tool_message(
        uuid="m-t",
        sender="assistant",
        text_before="Reading the file.",
        tool_name="read_file",
        tool_input={"path": "/foo/bar"},
        tool_result="actual file contents",
    )
    u = mcp_data.add_desktop_session("u-tr", messages=[msg])

    result = _call(
        session_id=u,
        positions=[0],
        include_tool_calls=False,  # explicit False; should be implicitly True
        include_tool_results=True,
    )
    block_types = [b["type"] for b in result[0]["content"]]
    assert "tool_use" in block_types
    assert "tool_result" in block_types
    # The tool_result must carry the actual result text.
    tr = next(b for b in result[0]["content"] if b["type"] == "tool_result")
    nested_text = " ".join(
        b.get("text", "") for b in tr.get("content", []) if b.get("type") == "text"
    )
    assert "actual file contents" in nested_text


def test_tool_use_long_input_uses_preview(mcp_data):
    """`tool_use.input` JSON > 200 chars + include_tool_results=False
    should produce `input_preview` (truncated) rather than `input`."""
    big_input = {"data": "x" * 500}
    msg = mcp_data.make_tool_message(
        uuid="m-big",
        sender="assistant",
        text_before="Big tool call.",
        tool_name="big_tool",
        tool_input=big_input,
        tool_result="ok",
    )
    u = mcp_data.add_desktop_session("u-big", messages=[msg])
    result = _call(
        session_id=u,
        positions=[0],
        include_tool_calls=True,
        include_tool_results=False,
    )
    tool_block = next(b for b in result[0]["content"] if b["type"] == "tool_use")
    assert "input_preview" in tool_block
    assert "input" not in tool_block
    assert tool_block["input_preview"].endswith("...")


def test_get_messages_session_not_found(mcp_data):
    with pytest.raises(ValueError, match="not found"):
        _call(session_id="00000000-0000-0000-0000-000000000999")


def test_positions_take_precedence_over_uuids(mcp_data):
    """When both selectors are supplied, positions wins (per spec)."""
    u = mcp_data.add_desktop_session("u-1")
    # positions=[0] would select msg-h-1
    # message_uuids=["msg-a-1"] would select msg-a-1
    # Spec says positions wins.
    result = _call(
        session_id=u,
        positions=[0],
        message_uuids=["msg-a-1"],
    )
    assert len(result) == 1
    assert result[0]["uuid"] == "msg-h-1"
