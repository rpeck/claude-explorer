"""Tests for the ``list_projects`` MCP tool."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from mcp_server.server import list_projects


def _call(**kwargs):
    fn = getattr(list_projects, "fn", list_projects)
    return fn(**kwargs)


def test_empty_store_returns_empty_list(mcp_data):
    assert _call() == []


def test_desktop_only_no_project_returns_empty(mcp_data):
    """Desktop conversations have no project_name; they aggregate to nothing."""
    mcp_data.add_desktop_session("u-1")
    assert _call() == []


def test_single_cc_project(mcp_data):
    mcp_data.add_cc_session("cc-1", cwd="/Users/me/Source/widgets")
    result = _call()
    assert result == [{"project": "widgets", "session_count": 1}]


def test_multiple_projects_sorted_descending_by_count(mcp_data):
    # widgets: 1 session, gadgets: 2 sessions, sprockets: 3 sessions
    mcp_data.add_cc_session("cc-w-1", cwd="/repo/widgets")
    mcp_data.add_cc_session("cc-g-1", cwd="/repo/gadgets")
    mcp_data.add_cc_session("cc-g-2", cwd="/repo/gadgets")
    mcp_data.add_cc_session("cc-s-1", cwd="/repo/sprockets")
    mcp_data.add_cc_session("cc-s-2", cwd="/repo/sprockets")
    mcp_data.add_cc_session("cc-s-3", cwd="/repo/sprockets")

    result = _call()
    # Descending order by session_count.
    counts = [(r["project"], r["session_count"]) for r in result]
    assert counts == [
        ("sprockets", 3),
        ("gadgets", 2),
        ("widgets", 1),
    ]


def test_source_filter_claude_ai_excludes_cc(mcp_data):
    mcp_data.add_cc_session("cc-1", cwd="/repo/widgets")
    # Filter to CLAUDE_AI: CC sessions excluded, Desktop has no project,
    # so result is empty.
    assert _call(source="CLAUDE_AI") == []


def test_source_filter_claude_code_only_cc(mcp_data):
    mcp_data.add_desktop_session("u-1")
    mcp_data.add_cc_session("cc-1", cwd="/repo/widgets")
    result = _call(source="CLAUDE_CODE")
    assert result == [{"project": "widgets", "session_count": 1}]


def test_invalid_source_is_refused(mcp_data):
    # An unknown source used to widen to all sources (#73); now it is an error.
    mcp_data.add_cc_session("cc-1", cwd="/repo/widgets")
    with pytest.raises(ToolError, match='Unknown source "BOGUS"'):
        _call(source="BOGUS")
