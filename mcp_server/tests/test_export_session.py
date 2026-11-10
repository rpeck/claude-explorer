"""Tests for the ``export_session`` MCP tool."""

from __future__ import annotations

import pytest

from mcp_server.server import export_session


def _call(**kwargs):
    fn = getattr(export_session, "fn", export_session)
    return fn(**kwargs)


def test_export_full_session_returns_markdown(mcp_data):
    u = mcp_data.add_desktop_session("u-1", name="Sample")
    md = _call(conversation_id=u)
    assert isinstance(md, str)
    # The Markdown header pipeline emits the title somewhere.
    assert "Sample" in md
    # Both messages must be present (by text content).
    assert "Hello, Claude" in md
    assert "FTS5" in md


def test_export_session_not_found(mcp_data):
    with pytest.raises(ValueError, match="not found"):
        _call(conversation_id="00000000-0000-0000-0000-000000000999")


def test_export_start_position_only(mcp_data):
    # 3-message session: 2 default + 1 extra
    extra = {
        "uuid": "msg-h-2",
        "sender": "human",
        "text": "SECOND_HUMAN_MARKER",
        "content": [{"type": "text", "text": "SECOND_HUMAN_MARKER"}],
        "created_at": "2026-04-01T10:01:00Z",
        "updated_at": "2026-04-01T10:01:00Z",
        "parent_message_uuid": "msg-a-1",
    }
    u = mcp_data.add_desktop_session(
        "u-1",
        messages=mcp_data._default_desktop_messages() + [extra],
    )
    # Start at position 1 → skips the first human message but keeps
    # the assistant reply and the second human message.
    md = _call(conversation_id=u, start_position=1)
    assert "Hello, Claude" not in md
    assert "FTS5" in md  # assistant reply present
    assert "SECOND_HUMAN_MARKER" in md


def test_export_end_position_inclusive(mcp_data):
    # Build a 3-message session with distinct, non-overlapping marker
    # tokens per position so end-position slicing is unambiguous.
    msgs = [
        {
            "uuid": "msg-h-x",
            "sender": "human",
            "text": "FIRST_HUMAN_MARKER",
            "content": [{"type": "text", "text": "FIRST_HUMAN_MARKER"}],
            "created_at": "2026-04-01T10:00:00Z",
            "updated_at": "2026-04-01T10:00:00Z",
            "parent_message_uuid": None,
        },
        {
            "uuid": "msg-a-x",
            "sender": "assistant",
            "text": "ASSISTANT_MARKER_TWO",
            "content": [{"type": "text", "text": "ASSISTANT_MARKER_TWO"}],
            "created_at": "2026-04-01T10:00:30Z",
            "updated_at": "2026-04-01T10:00:30Z",
            "parent_message_uuid": "msg-h-x",
        },
        {
            "uuid": "msg-h-y",
            "sender": "human",
            "text": "SECOND_HUMAN_MARKER",
            "content": [{"type": "text", "text": "SECOND_HUMAN_MARKER"}],
            "created_at": "2026-04-01T10:01:00Z",
            "updated_at": "2026-04-01T10:01:00Z",
            "parent_message_uuid": "msg-a-x",
        },
    ]
    u = mcp_data.add_desktop_session("u-1", messages=msgs)
    # end_position=0 → only the first message.
    md = _call(conversation_id=u, end_position=0)
    assert "FIRST_HUMAN_MARKER" in md
    assert "ASSISTANT_MARKER_TWO" not in md
    assert "SECOND_HUMAN_MARKER" not in md


def test_export_position_range(mcp_data):
    extra = {
        "uuid": "msg-h-2",
        "sender": "human",
        "text": "SECOND_HUMAN_MARKER",
        "content": [{"type": "text", "text": "SECOND_HUMAN_MARKER"}],
        "created_at": "2026-04-01T10:01:00Z",
        "updated_at": "2026-04-01T10:01:00Z",
        "parent_message_uuid": "msg-a-1",
    }
    u = mcp_data.add_desktop_session(
        "u-1",
        messages=mcp_data._default_desktop_messages() + [extra],
    )
    # Just the middle message.
    md = _call(conversation_id=u, start_position=1, end_position=1)
    assert "Hello, Claude" not in md
    assert "FTS5" in md
    assert "SECOND_HUMAN_MARKER" not in md


def test_export_out_of_range_positions_clamp(mcp_data):
    u = mcp_data.add_desktop_session("u-1")
    # start=-5, end=100 — both out of range; should clamp to full session.
    md = _call(conversation_id=u, start_position=-5, end_position=100)
    assert "Hello, Claude" in md
    assert "FTS5" in md


def test_export_include_tools_default_true(mcp_data):
    msg = mcp_data.make_tool_message(
        uuid="m-t",
        sender="assistant",
        text_before="Checking now.",
        tool_name="read_file",
        tool_input={"path": "/foo/bar"},
        tool_result="contents here",
    )
    u = mcp_data.add_desktop_session("u-t", messages=[msg])
    md = _call(conversation_id=u)
    # Tool content should appear when include_tools=True (the default).
    assert "read_file" in md or "/foo/bar" in md or "contents here" in md


def test_export_include_tools_false_excludes_tool_content(mcp_data):
    msg = mcp_data.make_tool_message(
        uuid="m-t",
        sender="assistant",
        text_before="Checking now.",
        tool_name="read_file",
        tool_input={"path": "/secret/value"},
        tool_result="redacted contents",
    )
    u = mcp_data.add_desktop_session("u-t", messages=[msg])
    md = _call(conversation_id=u, include_tools=False)
    # Tool name/input/output should NOT be present.
    assert "read_file" not in md
    assert "/secret/value" not in md
    assert "redacted contents" not in md
    # But the surrounding text should be.
    assert "Checking now" in md
