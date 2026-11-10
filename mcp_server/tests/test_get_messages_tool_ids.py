"""Tests pinning that ``get_messages`` surfaces Anthropic content-block IDs.

External feedback (2026-06-08): the Anthropic tool-call protocol pairs a
``tool_use`` block (carrying ``id``) with a ``tool_result`` block (carrying
``tool_use_id``) by ID, not by message-positional adjacency. Prior to this
fix, ``get_messages`` returned both blocks but stripped the IDs at two
points:

1. ``backend.models.ContentBlock`` did not declare ``id`` or
   ``tool_use_id`` fields, so Pydantic v2's default ``extra='ignore'``
   dropped them at parse.
2. ``mcp_server.server._filter_content_blocks`` emitted only
   ``{type, name, input}`` for tool_use and ``{type, content}`` for
   tool_result.

Consequence: when an assistant fires N parallel tool_use blocks in one
message, the consumer cannot match each call to its matching result,
because (a) parallel tools return out of order, and (b) some results
may be missing. These tests pin the fix.
"""

from __future__ import annotations

from typing import Any

from mcp_server.server import get_messages


def _call(**kwargs: Any) -> list[dict[str, Any]]:
    fn = getattr(get_messages, "fn", get_messages)
    return fn(**kwargs)


def _tool_use_msg(
    uuid: str,
    sender: str,
    blocks: list[dict[str, Any]],
    parent_uuid: str | None = None,
    created_at: str = "2026-04-01T10:05:00Z",
) -> dict[str, Any]:
    """Build a disk-shape message from explicit content blocks."""
    return {
        "uuid": uuid,
        "sender": sender,
        "text": "",
        "content": blocks,
        "created_at": created_at,
        "updated_at": created_at,
        "parent_message_uuid": parent_uuid,
    }


def test_tool_use_id_surfaces_on_call_block(mcp_data):
    """A ``tool_use`` block's ``id`` must appear in the surfaced output.

    Without this, callers cannot match parallel calls to their results.
    """
    asst_msg = _tool_use_msg(
        uuid="m-asst-1",
        sender="assistant",
        blocks=[
            {"type": "text", "text": "Calling a tool."},
            {
                "type": "tool_use",
                "id": "toolu_test_alpha",
                "name": "read_file",
                "input": {"path": "/foo/bar"},
            },
        ],
    )
    u = mcp_data.add_desktop_session("u-id-call", messages=[asst_msg])

    result = _call(
        conversation_id=u,
        positions=[0],
        include_tool_calls=True,
        include_tool_results=True,
    )
    blocks = result[0]["content"]
    tool_uses = [b for b in blocks if b["type"] == "tool_use"]
    assert len(tool_uses) == 1, f"expected 1 tool_use, got {blocks!r}"
    assert tool_uses[0].get("id") == "toolu_test_alpha", (
        f"tool_use block missing or wrong id: {tool_uses[0]!r}"
    )


def test_tool_use_id_surfaces_on_result_block(mcp_data):
    """A ``tool_result`` block's ``tool_use_id`` must appear in output.

    This is the back-reference half of the pairing.
    """
    user_msg = _tool_use_msg(
        uuid="m-user-1",
        sender="human",
        blocks=[
            {
                "type": "tool_result",
                "tool_use_id": "toolu_test_alpha",
                "content": [{"type": "text", "text": "file contents"}],
            },
        ],
    )
    u = mcp_data.add_desktop_session("u-id-result", messages=[user_msg])

    result = _call(
        conversation_id=u,
        positions=[0],
        include_tool_calls=True,
        include_tool_results=True,
    )
    blocks = result[0]["content"]
    tool_results = [b for b in blocks if b["type"] == "tool_result"]
    assert len(tool_results) == 1, f"expected 1 tool_result, got {blocks!r}"
    assert tool_results[0].get("tool_use_id") == "toolu_test_alpha", (
        f"tool_result block missing or wrong tool_use_id: {tool_results[0]!r}"
    )


def test_call_and_result_pair_by_id_not_position(mcp_data):
    """Two parallel tool_use blocks, results returned REVERSED — pair by id.

    This is the load-bearing semantic: positional adjacency is NOT a
    reliable pairing signal. The consumer must walk the returned content
    blocks and match ``tool_use.id`` to ``tool_result.tool_use_id``.
    """
    asst_msg = _tool_use_msg(
        uuid="m-asst-par",
        sender="assistant",
        blocks=[
            {"type": "text", "text": "Calling two tools in parallel."},
            {
                "type": "tool_use",
                "id": "toolu_a",
                "name": "read_file",
                "input": {"path": "/a"},
            },
            {
                "type": "tool_use",
                "id": "toolu_b",
                "name": "read_file",
                "input": {"path": "/b"},
            },
        ],
    )
    # Results in reversed order: B first, then A.
    user_msg = _tool_use_msg(
        uuid="m-user-par",
        sender="human",
        blocks=[
            {
                "type": "tool_result",
                "tool_use_id": "toolu_b",
                "content": [{"type": "text", "text": "contents of /b"}],
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu_a",
                "content": [{"type": "text", "text": "contents of /a"}],
            },
        ],
        parent_uuid="m-asst-par",
        created_at="2026-04-01T10:05:30Z",
    )
    u = mcp_data.add_desktop_session(
        "u-id-par", messages=[asst_msg, user_msg]
    )

    result = _call(
        conversation_id=u,
        include_tool_calls=True,
        include_tool_results=True,
    )
    assert len(result) == 2

    # Collect call IDs from the assistant message and result back-refs
    # from the user message.
    asst_blocks = result[0]["content"]
    user_blocks = result[1]["content"]

    call_ids = [b["id"] for b in asst_blocks if b["type"] == "tool_use"]
    result_refs = [
        b["tool_use_id"] for b in user_blocks if b["type"] == "tool_result"
    ]

    assert call_ids == ["toolu_a", "toolu_b"], call_ids
    # Results are in REVERSED order on disk — the IDs prove it.
    assert result_refs == ["toolu_b", "toolu_a"], result_refs

    # And the pairing reconstructs cleanly via id ↔ tool_use_id matching:
    pairs: dict[str, dict[str, Any]] = {}
    for b in asst_blocks:
        if b["type"] == "tool_use":
            pairs.setdefault(b["id"], {})["call"] = b
    for b in user_blocks:
        if b["type"] == "tool_result":
            pairs.setdefault(b["tool_use_id"], {})["result"] = b

    assert set(pairs.keys()) == {"toolu_a", "toolu_b"}
    for tid, pair in pairs.items():
        assert "call" in pair, f"missing call for {tid}"
        assert "result" in pair, f"missing result for {tid}"
