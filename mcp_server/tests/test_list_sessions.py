"""Tests for the ``list_sessions`` MCP tool."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from mcp_server.server import list_sessions


def _call(**kwargs):
    """Invoke the tool, peeling the FastMCP wrapper if necessary."""
    fn = getattr(list_sessions, "fn", list_sessions)
    return fn(**kwargs)


def test_empty_store_returns_no_sessions(mcp_data):
    result = _call()
    assert result == {"sessions": [], "total": 0}


def test_single_desktop_session_appears(mcp_data):
    u1 = mcp_data.add_desktop_session("u-1", name="First conv")
    result = _call()
    assert result["total"] == 1
    assert len(result["sessions"]) == 1
    s = result["sessions"][0]
    assert s["uuid"] == u1
    assert s["name"] == "First conv"
    assert s["source"] == "CLAUDE_AI"
    assert s["message_count"] == 2
    assert s["human_message_count"] == 1
    assert s["model"] == "claude-sonnet-4-6"
    # No `match_count` field when there's no query.
    assert "match_count" not in s


def test_two_sessions_total_count(mcp_data):
    u1 = mcp_data.add_desktop_session("u-1", name="A")
    u2 = mcp_data.add_desktop_session("u-2", name="B")
    result = _call()
    assert result["total"] == 2
    uuids = {s["uuid"] for s in result["sessions"]}
    assert uuids == {u1, u2}


def test_source_filter_claude_ai_returns_only_desktop(mcp_data):
    u1 = mcp_data.add_desktop_session("u-1", name="Desktop")
    mcp_data.add_cc_session("cc-1")
    result = _call(source="CLAUDE_AI")
    assert {s["uuid"] for s in result["sessions"]} == {u1}


def test_source_filter_claude_code_returns_only_cc(mcp_data):
    mcp_data.add_desktop_session("u-1", name="Desktop")
    cc1 = mcp_data.add_cc_session("cc-1")
    result = _call(source="CLAUDE_CODE")
    uuids = {s["uuid"] for s in result["sessions"]}
    assert uuids == {cc1}
    assert all(s["source"] == "CLAUDE_CODE" for s in result["sessions"])


def test_source_filter_claude_cowork_excludes_other_sources(mcp_data):
    # Regression: CLAUDE_COWORK was silently dropped to "all" (and
    # returned everything) because the MCP source filter only allowed
    # AI/CODE. With no Cowork sessions present, the filter must exclude
    # the desktop + CC sessions and return nothing.
    mcp_data.add_desktop_session("u-1")
    mcp_data.add_cc_session("cc-1")
    result = _call(source="CLAUDE_COWORK")
    assert result == {"sessions": [], "total": 0}


def test_invalid_source_is_refused(mcp_data):
    # An unknown source used to widen to all sources (#73); now it is an error.
    mcp_data.add_desktop_session("u-1")
    mcp_data.add_cc_session("cc-1")
    with pytest.raises(ToolError, match='Unknown source "NOT_A_SOURCE"'):
        _call(source="NOT_A_SOURCE")


def test_project_filter_substring_case_insensitive(mcp_data):
    # Two CC sessions in different projects.
    cc_foo = mcp_data.add_cc_session("cc-foo", cwd="/Users/me/Source/foo-project")
    mcp_data.add_cc_session("cc-bar", cwd="/Users/me/Source/bar-tool")
    # Match the 'foo' substring, case-insensitive.
    result = _call(project="FOO")
    uuids = {s["uuid"] for s in result["sessions"]}
    assert uuids == {cc_foo}


def test_project_filter_no_match_returns_empty(mcp_data):
    mcp_data.add_cc_session("cc-1", cwd="/Users/me/Source/foo")
    result = _call(project="nonexistent")
    assert result == {"sessions": [], "total": 0}


def test_limit_clamped_to_100(mcp_data):
    # Plant 3 sessions; ask for a huge limit.
    for i in range(3):
        mcp_data.add_desktop_session(f"u-{i}", name=f"Conv {i}")
    result = _call(limit=10_000)
    # Should not error; should return all 3.
    assert result["total"] == 3
    assert len(result["sessions"]) == 3


def test_limit_floor_at_one(mcp_data):
    for i in range(3):
        mcp_data.add_desktop_session(f"u-{i}")
    result = _call(limit=0)
    # Clamped up to 1.
    assert len(result["sessions"]) == 1
    assert result["total"] == 3  # total reflects pre-slice count


def test_offset_pagination(mcp_data):
    expected_uuids = set()
    for i in range(5):
        u = mcp_data.add_desktop_session(
            f"u-page-{i:02d}", name=f"Conv {i}",
            updated_at=f"2026-04-0{i + 1}T10:00:00Z",
        )
        expected_uuids.add(u)
    # Page 1: 2 items
    page_1 = _call(limit=2, offset=0)
    # Page 2: 2 items
    page_2 = _call(limit=2, offset=2)
    # Page 3: 1 item
    page_3 = _call(limit=2, offset=4)

    assert page_1["total"] == page_2["total"] == page_3["total"] == 5
    assert len(page_1["sessions"]) == 2
    assert len(page_2["sessions"]) == 2
    assert len(page_3["sessions"]) == 1

    # No overlap between pages.
    uuids_seen = set()
    for page in (page_1, page_2, page_3):
        for s in page["sessions"]:
            assert s["uuid"] not in uuids_seen, "pagination overlap"
            uuids_seen.add(s["uuid"])
    assert uuids_seen == expected_uuids


def test_offset_negative_clamped(mcp_data):
    mcp_data.add_desktop_session("u-1")
    # offset=-5 should clamp to 0; no error.
    result = _call(offset=-5)
    assert result["total"] == 1
    assert len(result["sessions"]) == 1


def test_query_includes_match_count(mcp_data):
    """When `query` is supplied, each session entry carries a match_count."""
    u1 = mcp_data.add_desktop_session(
        "u-1",
        name="FTS5 deep dive",
        messages=[
            {
                "uuid": "h-1",
                "sender": "human",
                "text": "Tell me about NEEDLE_TOKEN in detail.",
                "content": [{"type": "text", "text": "Tell me about NEEDLE_TOKEN in detail."}],
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-01T10:00:00Z",
                "parent_message_uuid": None,
            },
            {
                "uuid": "a-1",
                "sender": "assistant",
                "text": "NEEDLE_TOKEN is a fixture string used by tests.",
                "content": [{"type": "text", "text": "NEEDLE_TOKEN is a fixture string used by tests."}],
                "created_at": "2026-04-01T10:00:30Z",
                "updated_at": "2026-04-01T10:00:30Z",
                "parent_message_uuid": "h-1",
            },
        ],
    )
    # Plant a second conversation that does NOT match.
    mcp_data.add_desktop_session("u-2", name="Unrelated")

    result = _call(query="NEEDLE_TOKEN")
    matched = [s for s in result["sessions"] if s["uuid"] == u1]
    assert matched, "query should have matched u-1"
    assert "match_count" in matched[0]
    assert matched[0]["match_count"] >= 1
